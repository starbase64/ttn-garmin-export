"""Explicit queues for missing resources; no automatic restart after a rate limit."""
import re
from archive import Archive
from catalog import DATASETS,ACTIVITY_DETAILS,all_definitions,selected_datasets
from engine import days,request_spec
from offline import ArchiveSync
from failures import diagnostic

def repair_plan(path,account,mode,selected,plan):
    from garminconnect import Garmin
    tasks=[];seen=set()
    def add(key,day,method,args=(),kwargs=None,fmt=''):
        token=(key,day)
        if token not in seen:
            seen.add(token);tasks.append((key,day,method,args,kwargs or {},fmt))
    with Archive(path,account,readonly=True) as db:
        if mode==3:
            allowed={d.method for d in all_definitions()}|set(ACTIVITY_DETAILS)
            if db.has_inferred_skips:
                for row in db.db.execute('SELECT * FROM inferred_skips WHERE day BETWEEN ? AND ? ORDER BY day,dataset',(plan.start,plan.end)):
                    request=__import__('json').loads(row['request_json'])
                    if request['method'] in allowed and not request.get('binary'):
                        add(row['dataset'],row['day'],request['method'],tuple(request['args']),request['kwargs'])
        elif mode==0:
            from datahub import track_from_archive
            for a in selected:
                if str(a.get('activityId','')).isdigit() and not track_from_archive(db,a):
                    ident=a['activityId'];add('activity-'+str(ident)+'-gpx',a['_day'],'download_activity',(ident,),{'dl_fmt':Garmin.ActivityDownloadFormat.GPX},'GPX')
        elif mode==1:
            for row in db.db.execute('SELECT * FROM attempts ORDER BY day'):
                key,day=row['dataset'],row['day']
                ds=next((d for d in all_definitions() if d.key==key),None)
                if ds:
                    args=() if ds.kind=='account' else (day,day) if ds.kind=='range' else (day,)
                    add(key,day,ds.method,args,{'aggregation':'daily'} if key=='endurance' else {},ds.binary)
                else:
                    match=re.fullmatch(r'activity-(\d+)-(.+)',key)
                    if not match:continue
                    ident,suffix=match.groups();fmt=suffix.upper()
                    if fmt in ('GPX','TCX','ORIGINAL','KML','CSV'):
                        add(key,day,'download_activity',(int(ident),),{'dl_fmt':Garmin.ActivityDownloadFormat[fmt]},fmt)
                    elif 'get_activity_'+suffix in ACTIVITY_DETAILS:add(key,day,'get_activity_'+suffix,(int(ident),))
        else:
            for day in days(plan.start,plan.end):
                for ds in selected_datasets(plan):
                    if ds.kind=='account':continue
                    args=(day,day) if ds.kind=='range' else (day,);kwargs={'aggregation':'daily'} if ds.key=='endurance' else {}
                    if not db.get(ds.key,day,request_spec(ds.key,ds.method,args,kwargs,ds.binary)):
                        add(ds.key,day,ds.method,args,kwargs,ds.binary)
    return tasks

class RepairSync(ArchiveSync):
    def run_tasks(self,tasks):
        from engine import Cancelled
        stopped=False;fatal={}
        self.force_requests={(t[0],t[1]) for t in tasks}
        from transfer import RemainingTime
        families=['files' if t[5] else 'details' if t[0].startswith('activity-') else t[0] for t in tasks]
        self.eta=RemainingTime(families,self.plan.delay,self.event)
        self.event('phase','download')
        with Archive(self.plan.archive_path,self.plan.account) as archive:
            self.archive=archive;self.event('total',len(tasks))
            try:
                for (key,day,method,args,kwargs,fmt),family in zip(tasks,families):
                    # Explicit retry refreshes even a previously successful response with a later failed attempt.
                    self.eta.begin(family)
                    if method in self.blocked_methods:self.eta.finish();continue
                    req=request_spec(key,method,args,kwargs,fmt)
                    self.retrieve(key,day,method,args,kwargs,fmt);self.event('progress')
                    self.eta.finish()
            except Cancelled:stopped=True
            except Exception as exc:fatal=diagnostic(exc)
            report={'completed':not stopped and not fatal and not self.stats['errors'],'cancelled':stopped,'fatal_diagnostic':fatal,'stats':self.stats,'errors':self.errors,'warnings':self.warnings,'operation':'repair'}
            report['transfer']=self.transfer
            archive.record_run(report)
            self.event('eta_end','cancelled' if stopped else 'error' if fatal or self.stats['errors'] else 'complete')
            return {'path':str(archive.path),'report':report,'start':self.plan.start,'end':self.plan.end,'coverage':archive.coverage(self.plan.start,self.plan.end)}
