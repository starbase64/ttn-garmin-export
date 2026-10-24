"""One current export per account, and one reusable source for sync/online export."""
import json
from pathlib import Path
from dataclasses import replace
import pytest
from engine import Plan,Exporter,account_key,account_root
from offline import ArchiveSync,ArchiveExporter
from archive import Archive,OfflineAccountMismatch
from demo_data import DemoGarmin
from exportfiles import publish_export

class Counting(DemoGarmin):
    def __init__(self):self.calls=[]
    def get_heart_rates(self,day):
        self.calls.append(day);return super().get_heart_rates(day)


def plan(tmp_path,**extra):
    values=dict(start='2020-01-01',end='2020-01-03',datasets=['heart_rate'],output=str(tmp_path/'output'),
                account=account_key('storage@example.invalid'),formats=['json'],delay=0,refresh_days=0,
                archive_path=str(tmp_path/'account.sqlite'))
    return Plan(**(values|extra))


def test_sync_and_online_export_reuse_same_archive_without_download(tmp_path):
    api=Counting();p=plan(tmp_path)
    assert ArchiveSync(api,p).run_sync()['report']['completed'] and len(api.calls)==3
    first,report=ArchiveExporter(api,p).run()
    assert report['completed'] and report['stats']['downloaded']==0 and report['stats']['cached']==3
    assert len(api.calls)==3 and Path(first).name=='current'
    second,again=ArchiveExporter(api,replace(p,formats=['csv'],workers=3)).run()
    assert second==first and again['stats']['downloaded']==0 and len(api.calls)==3
    assert not list(Path(second).glob('*.json.tmp')) and not list(Path(second).glob('heart_rate-*.json'))
    assert list(Path(second).glob('heart_rate-*.csv'))


def test_online_export_archives_new_data_and_later_sync_reuses_it(tmp_path):
    api=Counting();p=plan(tmp_path)
    assert ArchiveExporter(api,p).run()[1]['completed'] and len(api.calls)==3
    report=ArchiveSync(api,replace(p,end='2020-01-04')).run_sync()['report']
    assert report['completed'] and len(api.calls)==4 and api.calls[-1]=='2020-01-04'
    # A narrower export rewrites only the current result, retaining all source days.
    ArchiveExporter(api,replace(p,start='2020-01-02')).run()
    with Archive(p.archive_path,readonly=True) as db:
        assert db.db.execute("SELECT COUNT(*) FROM responses WHERE dataset='heart_rate'").fetchone()[0]==4
    assert len(api.calls)==4


def test_legacy_cache_import_needs_no_new_requests(tmp_path):
    api=Counting();p=plan(tmp_path)
    Exporter(api,p).run();assert len(api.calls)==3
    report=ArchiveExporter(api,p).run()[1]
    assert report['completed'] and report['stats']['downloaded']==0 and len(api.calls)==3
    with Archive(p.archive_path,readonly=True) as db:
        assert db.db.execute('SELECT COUNT(*) FROM responses').fetchone()[0]==3


def test_current_export_cleans_stale_generated_files_and_preserves_manual_files(tmp_path,monkeypatch):
    api=Counting();p=plan(tmp_path,formats=['csv','json'])
    folder,_=Exporter(api,p).run();folder=Path(folder)
    (folder/'my-notes.txt').write_text('keep')
    old=folder/'heart_rate-2020.json';assert old.exists()
    Exporter(api,replace(p,formats=['csv'])).run()
    assert not old.exists() and (folder/'my-notes.txt').read_text()=='keep'
    before={f.name:f.read_bytes() for f in folder.iterdir() if f.is_file()}
    exporter=Exporter(api,p)
    def broken(destination):
        (destination/'partial.csv').write_text('partial');raise OSError('disk error')
    monkeypatch.setattr(exporter,'write_outputs',broken)
    _,report=exporter.run()
    assert report['export_error_type']=='OSError' and not report['completed']
    assert before=={f.name:f.read_bytes() for f in folder.iterdir() if f.is_file()}


def test_history_is_optional_and_ignores_download_tuning(tmp_path):
    p=plan(tmp_path,export_history=True)
    a=Exporter(Counting(),p).run()[0]
    b=Exporter(Counting(),replace(p,workers=4,delay=0.01,retries=0)).run()[0]
    c=Exporter(Counting(),replace(p,formats=['csv'])).run()[0]
    assert a==b and c!=a and Path(a).name!='current'


def test_account_directory_is_not_nested_and_archive_account_must_match(tmp_path):
    p=plan(tmp_path);base=account_root(p.output,p.account)
    assert account_root(base,p.account)==base
    api=Counting();other=account_key('other@example.invalid')
    with Archive(p.archive_path,other):pass
    with pytest.raises(OfflineAccountMismatch):ArchiveExporter(api,p).run()
    assert not api.calls


def test_publication_failure_rolls_back_prior_generated_files(tmp_path,monkeypatch):
    import exportfiles
    current=tmp_path/'current';stage=tmp_path/'stage';current.mkdir();stage.mkdir()
    old={'app':'ttn-garmin-export','generated_files':['old.csv']}
    (current/'report.json').write_text(json.dumps(old));(current/'old.csv').write_text('old')
    (stage/'new.csv').write_text('new');(stage/'report.json').write_text('{}')
    original=exportfiles.os.replace
    def fail_once(src,dst):
        if Path(src)==stage/'new.csv':raise OSError('publication failed')
        return original(src,dst)
    monkeypatch.setattr(exportfiles.os,'replace',fail_once)
    with pytest.raises(OSError):publish_export(stage,current)
    assert (current/'old.csv').read_text()=='old'
    assert json.loads((current/'report.json').read_text())==old and not (current/'new.csv').exists()


def test_previous_manifest_cannot_remove_files_outside_export(tmp_path):
    current=tmp_path/'current';stage=tmp_path/'stage';current.mkdir();stage.mkdir()
    outside=tmp_path/'outside.csv';outside.write_text('keep')
    (current/'report.json').write_text(json.dumps({'app':'ttn-garmin-export','generated_files':['../outside.csv']}))
    (stage/'report.json').write_text('{}')
    with pytest.raises(ValueError):publish_export(stage,current)
    assert outside.read_text()=='keep'


def test_account_folder_switch_uses_sibling_instead_of_nested_account(tmp_path):
    a=account_key('a@example.invalid');b=account_key('b@example.invalid')
    assert account_root(tmp_path/('account-'+a),b)==tmp_path/('account-'+b)


def test_online_binary_exports_use_archive_payloads_and_resume(tmp_path):
    class Binary(DemoGarmin):
        def __init__(self):self.downloads=0
        def download_activity(self,ident,dl_fmt=None):
            self.downloads+=1;return b'synthetic-file-'+str(ident).encode()
    p=plan(tmp_path,datasets=['activities'],end='2020-01-01',activity_files=['GPX'])
    api=Binary();folder,report=ArchiveExporter(api,p).run()
    count=api.downloads
    assert report['completed'] and count>0
    assert len(list(Path(folder,'activities').glob('*.gpx')))==count
    again,report=ArchiveExporter(api,p).run()
    assert again==folder and report['completed'] and api.downloads==count and report['stats']['downloaded']==0


def test_old_nested_account_cache_is_imported_without_new_downloads(tmp_path):
    p=plan(tmp_path);account_folder=account_root(p.output,p.account)
    p=replace(p,output=str(account_folder))
    api=Counting();legacy=Exporter(api,p)
    legacy.root=account_folder/('account-'+p.account);legacy.cache=legacy.root/'cache-v1'
    assert legacy.run()[1]['completed'] and len(api.calls)==3
    result,report=ArchiveExporter(api,p).run()
    assert report['completed'] and report['stats']['downloaded']==0 and len(api.calls)==3
    assert Path(result)==account_folder/'exports'/'current'
