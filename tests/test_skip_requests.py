from dataclasses import replace
import json
import pytest
from engine import Plan,request_spec,account_key
from archive import Archive
from offline import ArchiveSync,ArchiveExporter,OfflineExporter
from repairs import repair_plan,RepairSync
from skip_requests import summary_reason,activity_reason

class API:
    def __init__(self):self.calls=[]
    def get_user_summary(self,day):
        self.calls.append(('summary',day));return {'calendarDate':day,'averageSpo2':None,'lowestSpo2':None,'latestSpo2':None}
    def get_spo2_data(self,day):
        self.calls.append(('spo2',day));return {'calendarDate':day,'values':[95]}

def plan(tmp_path,**kwargs):
    return Plan(**(dict(start='2020-01-01',end='2020-01-02',datasets=['summary','spo2'],output=str(tmp_path/'out'),
        archive_path=str(tmp_path/'archive.sqlite'),account=account_key('test@example.invalid'),delay=0,refresh_days=0,optimize=False,formats=['json'])|kwargs))

@pytest.mark.parametrize('workers',[1,3])
def test_opt_in_records_inference_and_recovery_gets_real_data(tmp_path,workers):
    api=API();p=plan(tmp_path,skip_empty=True,workers=workers)
    report=ArchiveSync(api,p).run_sync()['report']
    assert not report['completed'] and report['stats']['not_requested']==2
    assert not any(k=='spo2' for k,d in api.calls)
    with Archive(p.archive_path,readonly=True) as db:
        assert not db.get('spo2',p.start,request_spec('spo2','get_spo2_data',(p.start,)))
        coverage={r['dataset']:r for r in db.coverage(p.start,p.end)}
        assert coverage['spo2']['unverified_days']==2
    assert len(repair_plan(p.archive_path,p.account,2,None,p))==2  # still missing
    result=OfflineExporter(replace(p,source='offline')).run()[1]
    assert not result['completed'] and result['stats']['errors']==2
    assert any(w.startswith('unverified_skip:') for w in result['warnings'])
    tasks=repair_plan(p.archive_path,p.account,3,None,p)
    assert len(tasks)==2
    assert RepairSync(api,p).run_tasks(tasks)['report']['completed']
    assert len([1 for k,d in api.calls if k=='spo2'])==2
    assert repair_plan(p.archive_path,p.account,3,None,p)==[]
    assert OfflineExporter(replace(p,source='offline')).run()[1]['completed']


def test_default_off_fetches_data_despite_empty_summary(tmp_path):
    api=API();p=plan(tmp_path)
    assert not p.skip_empty
    assert ArchiveExporter(api,p).run()[1]['completed']
    assert len([1 for k,d in api.calls if k=='spo2'])==2


def test_existing_data_is_never_replaced_by_inference_during_refresh(tmp_path):
    api=API();p=plan(tmp_path)
    ArchiveSync(api,p).run_sync()
    report=ArchiveExporter(api,replace(p,skip_empty=True,refresh_days=100000)).run()[1]
    assert not report['stats'].get('not_requested') and report['completed']


def test_turning_option_off_recovers_previously_skipped_days(tmp_path):
    p=plan(tmp_path,skip_empty=True);ArchiveSync(API(),p).run_sync()
    api=API();result=ArchiveSync(api,replace(p,skip_empty=False)).run_sync()['report']
    assert result['completed'] and len([1 for k,d in api.calls if k=='spo2'])==2

@pytest.mark.parametrize('value',[{}, {'averageSpo2':None},{'averageSpo2':None,'lowestSpo2':None,'latestSpo2':95},{'includesWellnessData':False}])
def test_missing_or_positive_summary_fields_never_skip(value):
    assert summary_reason('spo2',value) is None


def test_activity_rules_require_all_fields_no_sport_name_guessing():
    assert activity_reason('get_activity_weather',{'hasPolyline':False}) is None
    assert activity_reason('get_activity_hr_in_timezones',{'averageHR':None}) is None
    assert activity_reason('get_activity_exercise_sets',{'activityType':{'typeKey':'cycling'}}) is None
    assert activity_reason('get_activity_weather',{'hasPolyline':False,'startLatitude':None,'startLongitude':None})
    assert activity_reason('get_activity_hr_in_timezones',{'averageHR':None,'maxHR':None})


def test_old_readonly_archive_remains_readable(tmp_path):
    p=plan(tmp_path)
    with Archive(p.archive_path,p.account) as db:
        db.db.execute('DROP TABLE inferred_skips');db.db.commit()
    with Archive(p.archive_path,readonly=True) as db:
        assert not db.has_inferred_skips and not db.inferred('spo2',p.start,{})
        assert db.coverage(p.start,p.end)==[]


def test_retry_respects_selected_date_range_and_account(tmp_path):
    p=plan(tmp_path,skip_empty=True);ArchiveSync(API(),p).run_sync()
    assert len(repair_plan(p.archive_path,p.account,3,None,replace(p,end=p.start)))==1
    with pytest.raises(Exception):repair_plan(p.archive_path,account_key('other'),3,None,p)


def test_activity_inference_retries_exact_extras_and_keeps_exercise_sets(tmp_path):
    from demo_data import DemoGarmin
    class Activities(DemoGarmin):
        def __init__(self):self.calls=[]
        def search_activities(self,start=0,limit=100,**filters):
            if start:return []
            return [{'activityId':123,'startTimeLocal':filters['startDate']+' 10:00:00','activityType':{'typeKey':'cycling'},
                'hasPolyline':False,'startLatitude':None,'startLongitude':None,'averageHR':None,'maxHR':None}]
        def get_activity_weather(self,ident):self.calls.append('weather');return {'value':1}
        def get_activity_hr_in_timezones(self,ident):self.calls.append('zones');return {'value':1}
        def get_activity_exercise_sets(self,ident):self.calls.append('sets');return {'value':1}
    api=Activities();p=plan(tmp_path,start='2020-01-01',end='2020-01-01',datasets=['activities'],skip_empty=True,
        activity_details=['get_activity_weather','get_activity_hr_in_timezones','get_activity_exercise_sets'])
    report=ArchiveExporter(api,p).run()[1]
    assert report['stats']['not_requested']==2 and api.calls==['sets'] and not report['completed']
    tasks=repair_plan(p.archive_path,p.account,3,None,p)
    assert len(tasks)==2 and RepairSync(api,p).run_tasks(tasks)['report']['completed']
    assert sorted(api.calls)==['sets','weather','zones']


def test_failed_recovery_stays_marked_and_retryable(tmp_path):
    from garminconnect import GarminConnectConnectionError
    p=plan(tmp_path,skip_empty=True);ArchiveSync(API(),p).run_sync()
    class Broken(API):
        def get_spo2_data(self,day):raise GarminConnectConnectionError('offline')
    tasks=repair_plan(p.archive_path,p.account,3,None,p)
    result=RepairSync(Broken(),replace(p,retries=0)).run_tasks(tasks)['report']
    assert not result['completed']
    assert len(repair_plan(p.archive_path,p.account,3,None,p))==2
