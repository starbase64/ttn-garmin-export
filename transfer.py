"""Conservative range planning and measured, phase-aware transfer estimates."""
from collections import Counter,defaultdict,deque
from dataclasses import dataclass
from datetime import date,timedelta
import statistics
import time

@dataclass
class Job:
    dataset: object
    days: list
    cached: bool=False
    mode: str='single'
    @property
    def family(self):return 'cache' if self.cached else self.dataset.key

def plan_jobs(exporter,selected):
    from engine import days
    result=[]
    for ds in selected:
        calendar=['snapshot'] if ds.kind=='account' else list(days(exporter.plan.start,exporter.plan.end))
        limit=1
        if exporter.plan.optimize and exporter.plan.source!='offline':
            if ds.key=='body_battery':limit=7
            elif ds.key=='activities' and callable(getattr(exporter.api,'search_activities',None)):limit=exporter.plan.activity_batch_days
            elif ds.key=='summary' and callable(getattr(exporter.api,'get_daily_summaries',None)):limit=exporter.plan.health_batch_days
            elif ds.key.endswith('-daily'):limit=exporter.plan.health_batch_days
        pending=[]
        def flush():
            if pending:
                result.append(Job(ds,list(pending),False,'range' if len(pending)>1 else 'single'));pending.clear()
        for day in calendar:
            exporter.check()
            args=() if ds.kind=='account' else (day,day) if ds.kind=='range' else (day,)
            kwargs={'aggregation':'daily'} if ds.key=='endurance' else {}
            cached=exporter.available(ds.key,day,ds.method,args,kwargs,ds.binary)
            if cached:
                flush();result.append(Job(ds,[day],True));continue
            pending.append(day)
            if len(pending)>=limit:flush()
        flush()
    return result

class RemainingTime:
    def __init__(self,families,delay,event,clock=time.monotonic,workers=1):
        self.pending=Counter(families);self.samples=defaultdict(lambda:deque(maxlen=24))
        self.delay=delay;self.event=event;self.clock=clock;self.active=None;self.started=clock();self.measured=0
        self.network_samples=deque(maxlen=40);self.open_activity_days=0
        self.workers=workers;self.parallel_families=set()
        self.parallel_samples=defaultdict(lambda:deque(maxlen=12));self.parallel_costs=deque(maxlen=24)
    def observe_parallel_batch(self,families,duration):
        # A completed window measures actual wall-clock throughput, including
        # shared spacing and worker waits. Summing concurrent request durations
        # would count their overlapping time repeatedly.
        if not families:return
        cost=max(self.delay,duration/len(families))
        self.parallel_costs.append(cost)
        for family in set(families):self.parallel_samples[family].append(cost)
        self.emit()
    def observe_request(self,duration):
        self.network_samples.append(max(.0001,duration));self.measured+=1;self.emit()
    def add(self,family,count):self.pending[family]+=count;self.emit()
    def skip(self,family):
        self.pending[family]=max(0,self.pending[family]-1);self.emit()
    def begin(self,family):self.active=(family,self.clock());self.emit()
    def finish(self):
        if self.active:
            family,start=self.active;duration=max(.0001,self.clock()-start)
            self.pending[family]=max(0,self.pending[family]-1);self.samples[family].append(duration)
            self.active=None
        self.emit()
    def emit(self):
        network=list(self.network_samples)
        fallback=max(self.delay,statistics.median(network)) if network else None
        seconds=0.;unknown=False
        for family,n in self.pending.items():
            if n<=0:continue
            if family=='cache':cost=statistics.median(self.samples[family]) if self.samples[family] else .002
            elif family in self.parallel_families:
                if self.parallel_samples[family]:cost=statistics.median(self.parallel_samples[family])
                elif self.parallel_costs:cost=statistics.median(self.parallel_costs)
                elif fallback is not None:cost=max(self.delay,fallback/self.workers)
                else:unknown=True;continue
            elif self.samples[family]:cost=max(self.delay,statistics.median(self.samples[family]))
            elif fallback is not None:cost=fallback
            else:unknown=True;continue
            seconds+=n*cost
        # Active task's elapsed time is subtracted by the UI timer, not twice here.
        provisional=self.open_activity_days>0
        self.event('eta',{'seconds':None if unknown or (network and self.measured<3) else max(0,seconds),
                          'measured':self.measured,'provisional':provisional,'pending':sum(self.pending.values())})

def split_body_battery(payload,requested):
    if not isinstance(payload,list):return None
    if not payload:return {day:[] for day in requested}
    result={}
    for item in payload:
        if not isinstance(item,dict):return None
        day=item.get('date') or item.get('calendarDate')
        if day not in requested:return None
        result.setdefault(day,[]).append(item)
    # Missing dates are NOT assumed empty: the server might have truncated the range.
    return result

def split_dated_records(payload,requested,summary=False):
    """Preserve complete dated rows; missing dates are retried, never fabricated.

    Full summaries use a documented data envelope. Daily statistics use lists.
    Unknown shapes, conflicting duplicates and dates outside the request reject
    the entire batch before anything is staged for the persistent archive.
    """
    rows=payload.get('data') if summary and isinstance(payload,dict) else payload
    if not isinstance(rows,list):return None
    result={}
    for row in rows:
        if not isinstance(row,dict):return None
        day=row.get('calendarDate')
        if day not in requested or (day in result and result[day]!=(row if summary else [row])):return None
        result[day]=row if summary else [row]
    return result
