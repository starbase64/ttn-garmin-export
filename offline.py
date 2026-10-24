"""Resumable archive sync and strictly network-free offline export/preview."""
from __future__ import annotations
import json
import threading
from datetime import date,timedelta
from pathlib import Path
from engine import Exporter,Cancelled,request_spec,days,error_status,fingerprint,BINARY_SUFFIX
from catalog import DATASETS, selected_datasets
from activity_table import type_graph
from failures import diagnostic
from archive import Archive,OfflineDataMissing,OfflineNetworkDisabled
from preview import Previewer


class SkippedAfterRejection(Exception):
    """Placeholder attempt for days not requested after Garmin rejected a method."""


class OfflineAPI:
    def __getattr__(self,name):
        def forbidden(*args,**kwargs):raise OfflineNetworkDisabled()
        return forbidden


class ArchiveSync(Exporter):
    def __init__(self,api,plan,cancel=None,event=lambda *a:None):
        super().__init__(api,plan,cancel,event)
        self.blocked_methods=set();self.stats['skipped']=0

    def nested_legacy_response(self,key,day,method,args=(),kwargs=None,binary_format=''):
        # Older versions appended account-* even when that account folder was
        # already selected. Reuse that nested cache after normalizing the root.
        legacy=Path(self.plan.output).expanduser().resolve()/('account-'+self.plan.account)/'cache-v1'
        if legacy==self.cache:return False,None,None
        request=request_spec(key,method,args,kwargs,binary_format)
        suffix=BINARY_SUFFIX.get(binary_format,'.json')
        path=legacy/key/day[:4]/(day+'-'+fingerprint(request)+suffix)
        try:
            if binary_format:
                payload=path.read_bytes()
                return bool(payload),payload,None
            envelope=json.loads(path.read_text(encoding='utf-8'))
            if envelope['request']==json.loads(json.dumps(request)):
                return True,envelope['data'],envelope.get('fetched_utc')
        except (OSError,ValueError,KeyError,TypeError):pass
        return False,None,None

    def available(self,key,day,method,args=(),kwargs=None,binary_format=''):
        from archive import entry_key
        if (key,day) in getattr(self,'force_requests',set()) or self.refresh_day(day):return False
        found=self.archive.db.execute('SELECT 1 FROM responses WHERE entry_key=?',
            (entry_key(key,day,request_spec(key,method,args,kwargs,binary_format)),)).fetchone()
        return bool(found) or super().available(key,day,method,args,kwargs,binary_format) or self.nested_legacy_response(key,day,method,args,kwargs,binary_format)[0]

    def retrieve(self,key,day,method,args=(),kwargs=None,binary_format=''):
        self.check()
        request=request_spec(key,method,args,kwargs,binary_format)
        if method in self.blocked_methods and not self._saving_staged:
            # Not requested again after Garmin rejected the method, but recorded as
            # missing so coverage and the repair queue ("retry failed") include the day.
            with self._lock:self._staged.pop((key,day),None)
            if not self.archive.get(key,day,request):
                self.archive.failed(key,day,request,SkippedAfterRejection(),None);self.stats['skipped']+=1
            return None
        found=self.archive.get(key,day,request)
        refresh=(key,day) in getattr(self,'force_requests',set()) or day=='snapshot' or (self.plan.refresh_days>0 and date.fromisoformat(day)>=date.today()-timedelta(days=self.plan.refresh_days-1))
        cached=found is not None and not refresh
        data=found['data'] if cached else None
        fetched=None
        if not cached and not refresh:
            # Import an existing successful cache response without downloading it again.
            suffix=BINARY_SUFFIX.get(binary_format,'.json')
            old=self.cache/key/day[:4]/(day+'-'+fingerprint(request)+suffix)
            if old.is_file():
                try:
                    if binary_format:
                        data=old.read_bytes()
                        if not data:raise ValueError()
                    else:
                        envelope=json.loads(old.read_text(encoding='utf-8'))
                        if envelope['request']!=json.loads(json.dumps(request)):raise ValueError()
                        data=envelope['data'];fetched=envelope.get('fetched_utc')
                    cached=True
                except (ValueError,KeyError,OSError):pass
            if not cached:
                cached,data,fetched=self.nested_legacy_response(key,day,method,args,kwargs,binary_format)
        try:
            if not cached:
                staged,data=self.take_staged(key,day)
                if not staged:data=self.fetch_data(key,day,method,args,kwargs)
                if binary_format and (not isinstance(data,bytes) or not data):raise ValueError('Invalid binary response')
            self.check()
            ident=found['entry_key'] if found and cached and data is found['data'] else self.archive.put(key,day,request,data,bool(binary_format),fetched)
            self.stats['cached' if cached else 'downloaded']+=1
            if not binary_format and data in (None,[],{}):self.stats['empty']+=1
            self.records.append({'dataset':key,'date':day,'archive_key':ident,'binary':bool(binary_format),
                                 'path':'archive/'+ident+BINARY_SUFFIX.get(binary_format,'.json')})
            self.event('item',key,day,'offline' if cached else 'downloaded')
            return data
        except Cancelled:raise
        except Exception as exc:
            from garminconnect import GarminConnectAuthenticationError,GarminConnectTooManyRequestsError,GarminConnectConnectionError,GarminConnectNotFoundError
            status=error_status(exc)
            self.archive.failed(key,day,request,exc,status)
            self.stats['errors']+=1
            self.errors.append({'dataset':key,'date':day,**diagnostic(exc)})
            self.event('error',key,day,type(exc).__name__+(f' · HTTP {status}' if status else ''))
            if isinstance(exc,(GarminConnectAuthenticationError,GarminConnectTooManyRequestsError)):raise
            if status in (400,405,422):
                self.blocked_methods.add(method);self.warnings.append('unsupported:'+method);return None
            if isinstance(exc,GarminConnectConnectionError) and not isinstance(exc,GarminConnectNotFoundError):raise
            # Disk/database errors must stop rather than claiming an archived response.
            import sqlite3
            if isinstance(exc,(OSError,sqlite3.Error)):raise
            return None

    def has_response(self,key,day,method,args=(),kwargs=None,binary_format=''):
        return bool(self.archive.get(key,day,request_spec(key,method,args,kwargs,binary_format))) or super().has_response(key,day,method,args,kwargs,binary_format) or self.nested_legacy_response(key,day,method,args,kwargs,binary_format)[0]

    def not_requested(self,key,day,method,args,kwargs,binary_format,reason):
        self.archive.mark_inferred(key,day,request_spec(key,method,args,kwargs,binary_format),reason)
        super().not_requested(key,day,method,args,kwargs,binary_format,reason)

    def collect(self):
        return super().collect()

    def activity_exports(self,day,activities):
        return super().activity_exports(day,activities)

    def run_sync(self):
        stopped=False;fatal={}
        with Archive(self.plan.archive_path,self.plan.account) as archive:
            self.archive=archive
            try:
                if callable(getattr(self.api,'get_activity_types',None)):
                    self.retrieve('activity_types','snapshot','get_activity_types')
                self.collect()
            except Cancelled:stopped=True
            except Exception as exc:fatal=diagnostic(exc)
            try:self.save_staged_successes()
            except Exception as exc:
                # Keep the original cause (e.g. 429) so the request pause still applies.
                if not fatal:fatal=diagnostic(exc)
                else:self.warnings.append('save_after_error_failed:'+type(exc).__name__)
            report={'completed':not stopped and not fatal and not self.stats['errors'] and not self.stats.get('not_requested'),'cancelled':stopped,
                    'fatal_diagnostic':fatal,'stats':self.stats,'errors':self.errors,'warnings':self.warnings,
                    'start':self.plan.start,'end':self.plan.end,'datasets':self.plan.datasets,
                    'activity_details':self.plan.activity_details,'activity_files':self.plan.activity_files,
                    'health_detail':self.plan.health_detail,'health_batch_days':self.plan.health_batch_days,
                    'blocked_methods':sorted(self.blocked_methods)}
            report['transfer']=self.transfer
            self.event('eta_end','cancelled' if stopped else 'error' if fatal or self.stats['errors'] else 'complete')
            archive.record_run(report)
            return {'path':str(archive.path),'report':report,'start':self.plan.start,'end':self.plan.end,'coverage':archive.coverage(self.plan.start,self.plan.end)}


class OfflineExporter(Exporter):
    def __init__(self,plan,cancel=None,event=lambda *a:None):
        super().__init__(OfflineAPI(),plan,cancel,event)
        self.archive=Archive(plan.archive_path,plan.account,readonly=True)
        self.type_descendants=type_graph(self.archive.snapshot('activity_types'))

    def call(self,*args,**kwargs):raise OfflineNetworkDisabled()
    def run(self):
        try:return super().run()
        finally:self.archive.close()
    def match_activities(self,activities):
        selected=self.plan.activity_type
        if not selected:return activities
        if not self.type_descendants and 'activity_taxonomy_unavailable_exact_match_only' not in self.warnings:
            self.warnings.append('activity_taxonomy_unavailable_exact_match_only')
            self.event('warning','activity_taxonomy_unavailable_exact_match_only')
        keys=self.type_descendants.get(selected,set())|{selected}
        return [a for a in activities if isinstance(a.get('activityType'),dict) and a['activityType'].get('typeKey') in keys]
    def retrieve(self,key,day,method,args=(),kwargs=None,binary_format=''):
        self.check()
        found=self.archive.get(key,day,request_spec(key,method,args,kwargs,binary_format))
        imported=self.archive.get('activities',day,{'import':'activity-files-v1'}) if key=='activities' else None
        if imported:
            # Imported days are partial, so never claim that the full Garmin day was fetched.
            records=[r for r in (found,imported) if r]
            data={str(a.get('activityId')):a for r in records for a in (r['data'] or [])}
            self.stats['cached']+=1
            self.warnings.append('local_import_partial_day:'+day)
            self.records.append({'dataset':key,'date':day,'binary':False,'archive_keys':[r['entry_key'] for r in records]})
            self.event('item',key,day,'offline')
            return list(data.values())
        if not found and self.archive.inferred(key,day,request_spec(key,method,args,kwargs,binary_format)):
            self.warnings.append('unverified_skip:'+key+':'+day)
        if not found:
            self.stats['errors']+=1
            self.errors.append({'dataset':key,'date':day,'error_type':'OfflineDataMissing','http_status':None})
            self.event('error',key,day,'OfflineDataMissing');return None
        self.stats['cached']+=1
        if found['is_empty']:self.stats['empty']+=1
        suffix=BINARY_SUFFIX.get(binary_format,'.json')
        self.records.append({'dataset':key,'date':day,'path':'archive/'+found['entry_key']+suffix,
                             'binary':bool(binary_format),'archive_key':found['entry_key']})
        self.event('item',key,day,'offline')
        return found['data']
    def iter_payloads(self,records):
        for record in records:
            if 'archive_keys' in record:
                combined={str(a.get('activityId')):a for key in record['archive_keys'] for a in self.archive.by_key(key)['data']}
                payload=list(combined.values())
            else:payload=self.archive.by_key(record['archive_key'])['data']
            if record['dataset']=='activities' and isinstance(payload,list):payload=self.match_activities(payload)
            yield record,payload
    def copy_binary(self,record,target):
        target.write_bytes(self.archive.by_key(record['archive_key'])['data'])


class ArchiveExporter(ArchiveSync):
    """Online export using the same persistent account archive as archive sync."""
    def run(self):
        if self.plan.source!='online':raise OfflineNetworkDisabled()
        if not self.plan.archive_path:raise ValueError('An archive path is required')
        with Archive(self.plan.archive_path,self.plan.account) as archive:
            self.archive=archive
            taxonomy=type_graph(archive.snapshot('activity_types'))
            self.type_descendants=taxonomy or None
            return Exporter.run(self)

    iter_payloads=OfflineExporter.iter_payloads
    copy_binary=OfflineExporter.copy_binary


class OfflinePreviewer(Previewer):
    def __init__(self,plan,cancel=None,event=lambda *a:None):
        super().__init__(OfflineAPI(),plan,cancel,event)
    def call(self,*args,**kwargs):raise OfflineNetworkDisabled()
    def run_preview(self):
        stopped=False
        with Archive(self.plan.archive_path,self.plan.account,readonly=True) as archive:
            self.type_descendants=type_graph(archive.snapshot('activity_types'))
            try:
                for ds in selected_datasets(self.plan):
                    self.check()
                    if ds.binary:self.preview_notes.append('files_not_downloaded');continue
                    found=archive.samples(ds.key,self.plan.start,self.plan.end,3)
                    if not found:
                        self.preview_errors.append({'dataset':ds.key,'error_type':'OfflineDataMissing','http_status':None});continue
                    if ds.key=='activities':
                        data=[]
                        for record in found:
                            data.extend(self.match_activities(record['data'] or []))
                            if len(data)>=self.plan.preview_rows:break
                        data=data[:self.plan.preview_rows]
                        self.store_sample(ds.key,self.plan.start,data)
                        if data:
                            ident=data[0].get('activityId')
                            for method in self.plan.activity_details:
                                key='activity-'+str(ident)+'-'+method.removeprefix('get_activity_')
                                detail=archive.samples(key,self.plan.start,self.plan.end,1)
                                if detail:self.store_sample(key,detail[0]['day'],detail[0]['data'])
                                else:self.preview_errors.append({'dataset':key,'error_type':'OfflineDataMissing','http_status':None})
                    else:
                        for record in found:self.store_sample(ds.key,record['day'],record['data'])
            except Cancelled:stopped=True
        notes=['offline_sample']
        if self.plan.activity_type and not self.type_descendants:notes.append('activity_taxonomy_unavailable_exact_match_only')
        if self.plan.expert_method:notes.append('expert_not_sampled')
        if self.plan.activity_files:notes.append('files_not_downloaded')
        return {'groups':self.make_groups(),'requests':0,'budget_reached':False,'cancelled':stopped,
                'errors':self.preview_errors,'notes':notes+self.preview_notes+self.warnings,'plan':self.plan}
