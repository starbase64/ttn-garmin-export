import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QEvent
from app import MainWindow

QT_APP = QApplication.instance() or QApplication([])

# Explicitly release widgets before QApplication shuts down; tests create several windows.
import pytest
@pytest.fixture(autouse=True)
def clean_up_windows():
    yield
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()



def test_language_preserves_selection_and_dates():
    app=QApplication.instance() or QApplication([])
    window=MainWindow(demo=True)
    window.dataset_checks['hrv'].setChecked(True)
    before=window.make_plan()
    window.toggle_language()
    assert window.lang=='en'
    assert window.tabs.tabText(window.tabs.count()-1)=='User guide'
    assert window.start_button.text()=='Start export'
    after=window.make_plan()
    assert after.language=='en'
    after.language=before.language
    assert after==before
    window.toggle_language()
    assert window.start_button.text()=='Export starten'
    assert 'Anleitung' in window.help.toPlainText()
    window.preview_done({'heartRateValues':[[1,60],[2,None]]})
    assert window.fields_list.count()==2
    window.fields_list.item(0).setCheckState(Qt.CheckState.Checked)
    window.apply_fields()
    assert '*' in window.selectors.toPlainText()
    window.close()


def test_downloads_output_uses_system_download_location_and_health_preset_roundtrip(tmp_path,monkeypatch):
    import json
    import app as app_module
    from PySide6.QtCore import QStandardPaths
    from PySide6.QtWidgets import QFileDialog
    from app import default_output_folder
    native=QStandardPaths.writableLocation
    downloads=tmp_path/'RedirectedDownloads'
    monkeypatch.setattr(QStandardPaths,'writableLocation',lambda kind:str(downloads) if kind==QStandardPaths.StandardLocation.DownloadLocation else native(kind))
    assert default_output_folder()==downloads/'Garmin_Export'
    window=MainWindow(demo=True)
    assert window.output.text()==str(downloads/'Garmin_Export')
    window.health_detail.setCurrentIndex(window.health_detail.findData('daily'));window.health_batch_days.setValue(14)
    window.toggle_language()
    assert window.health_detail.currentData()=='daily' and window.make_plan().health_batch_days==14
    assert window.health_detail.currentText()=='Daily statistics (faster)'
    assert window.dataset_checks['heart_rate'].text()=='Heart rate – daily statistics'
    preset=tmp_path/'preset.json'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:(str(preset),''))
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(str(preset),''))
    window.save_preset()
    assert json.loads(preset.read_text())['settings']['health_detail']=='daily'
    window.health_detail.setCurrentIndex(0);window.health_batch_days.setValue(28)
    window.load_preset()
    assert window.make_plan().health_detail=='daily' and window.make_plan().health_batch_days==14
    window.close()


def test_login_without_remember_uses_temporary_session(monkeypatch,tmp_path):
    import app as app_module
    from types import SimpleNamespace
    from pathlib import Path
    qt_app=QApplication.instance() or QApplication([])
    window=MainWindow()
    observed={}
    class FakeGarmin:
        def __init__(self,**kwargs):
            observed['retry_attempts']=kwargs.get('retry_attempts')
            self.client=SimpleNamespace(_tokenstore_path=None)
        def login(self,path):
            observed['session']=path
            self.client._tokenstore_path=path
            Path(path,'garmin_tokens.json').write_text('not-a-real-token')
    monkeypatch.setenv('GARMINTOKENS',str(tmp_path/'must-not-be-used'))
    monkeypatch.setattr(app_module,'Garmin',FakeGarmin)
    window.email.setText('test@example.invalid');window.password.setText('not-a-real-password')
    class FakeWorker:
        def ask_mfa(self):return '000000'
    window.run_worker=lambda op,done:done(op(FakeWorker()))
    window.login()
    assert not Path(observed['session']).exists()
    assert window.api.client._tokenstore_path is None
    assert not (tmp_path/'must-not-be-used').exists()
    assert window.password.text()==''
    assert observed['retry_attempts']==0
    assert window.signed_email=='test@example.invalid'
    window.close()


def test_sport_dropdown_preserves_keys_across_language_refresh_and_presets():
    from PySide6.QtWidgets import QComboBox
    window=MainWindow(demo=True)
    assert isinstance(window.sport,QComboBox)
    assert window.make_plan().activity_type==''
    assert window.sport.currentText()=='Alle Sportarten'
    window.sports_done(['running','hiking','cycling','new_garmin_sport'])
    assert window.sport.count()==5
    window.set_sport('hiking')
    assert window.sport.currentText()=='Wandern (hiking)'
    assert window.make_plan().activity_type=='hiking'
    window.toggle_language()
    assert window.sport.currentText()=='Hiking (hiking)'
    assert window.make_plan().activity_type=='hiking'
    assert window.sport.itemText(0)=='All sport types'
    # A loaded preset can contain a custom or newly introduced Garmin key.
    window.set_sport('custom_legacy_key')
    window.sports_done(['running','cycling'])
    assert window.make_plan().activity_type=='custom_legacy_key'
    window.sport.setEditText('manually_entered_type')
    window.toggle_language()
    assert window.make_plan().activity_type=='manually_entered_type'
    window.set_sport('')
    assert window.make_plan().activity_type==''
    window.close()


def test_sport_refresh_loads_from_api_and_keeps_selection():
    window=MainWindow(demo=True)
    class FakeGarmin:
        def get_activity_types(self):
            return [{'typeKey':'running','typeId':1},{'typeKey':'hiking','typeId':3}]
    window.api=FakeGarmin()
    window.set_sport('hiking')
    window.run_worker=lambda operation,done:done(operation(None))
    window.refresh_sports()
    assert window.sports_loaded
    assert window.sport.count()==3
    assert window.make_plan().activity_type=='hiking'
    window.close()


def test_activity_list_shortcut_preserves_date_and_sport():
    window=MainWindow(demo=True)
    window.set_sport('e_bike_mountain')
    window.file_checks['CSV'].setChecked(True)
    window.detail_checks['get_activity_laps'].setChecked(True)
    start=window.start_date.date()
    window.activity_list_preset()
    p=window.make_plan()
    assert p.datasets==['activities'] and p.formats==['csv']
    assert p.activity_summary and not p.activity_files and not p.activity_details
    assert not p.split_year and p.activity_type=='e_bike_mountain'
    assert window.start_date.date()==start
    window.close()


def test_export_preview_dialog_uses_actual_headers_and_sample_limits(tmp_path):
    from PySide6.QtWidgets import QTableWidget
    from preview import Previewer
    window=MainWindow(demo=True);window.activity_list_preset()
    window.preview_rows.setValue(3);window.preview_requests.setValue(2)
    window.output.setText(str(tmp_path));p=window.make_plan()
    result=Previewer(window.api,p).run_preview()
    window.export_preview_done(result)
    tables=window.export_preview_dialog.findChildren(QTableWidget)
    assert len(tables)==1 and tables[0].rowCount()==3
    assert tables[0].horizontalHeaderItem(5).text()=='Distanz (km)'
    assert tables[0].item(0,5).text()=='15,67'
    assert list(tmp_path.iterdir())==[]
    window.export_preview_dialog.close();window.close()


def test_rate_pause_prevents_any_new_worker_requests(tmp_path):
    from failures import RatePause
    window=MainWindow(demo=True)
    window.rate_pause=RatePause(tmp_path/'pause.json')
    window.rate_pause.record({'reason':'rate_limit','retry_after_seconds':600})
    messages=[];window.message=lambda de,en:messages.append(de)
    calls=[]
    window.run_worker(lambda worker:calls.append('network'),lambda result:None)
    assert not calls and window.worker is None
    assert messages and window.cooldown_label.text()
    window.close()


def test_worker_emits_safe_diagnostic():
    from app import Worker
    from garminconnect import GarminConnectConnectionError
    def fail(worker):raise GarminConnectConnectionError('API Error 403 - secret-account')
    worker=Worker(fail);details=[];worker.failed.connect(details.append);worker.run()
    assert details[0]['http_status']==403
    assert 'secret-account' not in str(details)


def test_offline_ui_exports_without_signin_and_bypasses_network_pause(tmp_path):
    from dataclasses import replace
    from engine import Plan,account_key
    from offline import ArchiveSync
    from demo_data import DemoGarmin
    from failures import RatePause
    from PySide6.QtCore import QDate
    from types import SimpleNamespace
    import threading
    p=Plan('2020-01-01','2020-01-01',['activities'],str(tmp_path/'unused'),account_key('saved'),
        archive_path=str(tmp_path/'data.sqlite'),delay=0,refresh_days=0)
    assert ArchiveSync(DemoGarmin(),p).run_sync()['report']['completed']
    window=MainWindow();assert window.api is None
    window.archive_path.setText(p.archive_path);window.use_offline.setChecked(True)
    window.activity_list_preset();window.output.setText(str(tmp_path/'results'))
    window.start_date.setDate(QDate(2020,1,1));window.end_date.setDate(QDate(2020,1,1))
    window.rate_pause=RatePause(tmp_path/'pause.json');window.rate_pause.record({'reason':'rate_limit'})
    window.message=lambda *a:None
    modes=[]
    def run(operation,done,network=True):
        modes.append(network)
        assert not network
        done(operation(SimpleNamespace(cancel=threading.Event(),event=SimpleNamespace(emit=lambda *a:None))))
    window.run_worker=run
    window.start_export()
    assert modes==[False] and window.last_output
    assert window.make_plan().account==p.account
    window.close()


def test_full_archive_button_ignores_export_filters_and_selects_supported_data(tmp_path,monkeypatch):
    import app as app_module
    from types import SimpleNamespace
    import threading
    from catalog import DATASETS,ACTIVITY_DETAILS
    window=MainWindow(demo=True)
    window.archive_path.setText(str(tmp_path/'new.sqlite'))
    window.use_offline.setChecked(True)  # Creating the archive still uses the signed-in online account.
    window.activity_list_preset();window.set_sport('e_bike_mountain')
    window.archive_format_checks['GPX'].setChecked(True)
    window.health_detail.setCurrentIndex(window.health_detail.findData('daily'))
    window.expert_enable.setChecked(True);window.kwargs.setPlainText('invalid expert parameters are irrelevant to bulk sync')
    captured=[]
    class Sync:
        def __init__(self,api,plan,*args):captured.append(plan)
        def run_sync(self):return None
    monkeypatch.setattr(app_module,'ArchiveSync',Sync)
    def run(operation,done,network=True):
        assert network
        operation(SimpleNamespace(cancel=threading.Event(),event=SimpleNamespace(emit=lambda *a:None)))
    window.run_worker=run
    window.start_archive_sync()
    p=captured[0]
    assert p.datasets==[d.key for d in DATASETS if d.full_sync] and 'wellness_fit' not in p.datasets and p.activity_type=='' and not p.selectors
    assert p.health_detail=='full'
    # Transient network/5xx errors are retried; 4xx/429 never are (see Exporter.call).
    assert p.source=='online' and p.retries>=1 and p.delay>=app_module.MIN_SYNC_INTERVAL and p.workers==window.workers.value()
    assert p.activity_details==list(ACTIVITY_DETAILS) and p.activity_files==['ORIGINAL','GPX']
    window.close()


def test_online_ui_reuses_archive_and_current_folder_and_exposes_history(tmp_path):
    import threading
    from types import SimpleNamespace
    from PySide6.QtCore import QDate
    from engine import Plan,account_key
    from demo_data import DemoGarmin
    from offline import ArchiveSync
    ident=account_key('reuse@example.invalid');database=tmp_path/'data.sqlite'
    p=Plan('2020-01-01','2020-01-01',['summary'],str(tmp_path/'output'),ident,
           archive_path=str(database),delay=0,refresh_days=0)
    assert ArchiveSync(DemoGarmin(),p).run_sync()['report']['completed']
    class NoRequests:
        def __getattr__(self,name):
            def fail(*args,**kwargs):raise AssertionError('No Garmin traffic expected')
            return fail
    window=MainWindow();window.api=NoRequests();window.identity=ident;window._signed_email='reuse@example.invalid'
    window.output.setText(p.output);window.archive_path.setText(str(database))
    window.select_all(False);window.dataset_checks['summary'].setChecked(True)
    window.start_date.setDate(QDate(2020,1,1));window.end_date.setDate(QDate(2020,1,1));window.refresh.setValue(0)
    worker=SimpleNamespace(cancel=threading.Event(),event=SimpleNamespace(emit=lambda *args:None))
    results=[];window.run_worker=lambda operation,done,network=True:results.append(operation(worker))
    window.start_export();window.start_export()
    assert results[0][1]['completed'] and results[1][1]['completed']
    assert results[0][0]==results[1][0] and results[1][1]['stats']['downloaded']==0
    assert results[1][1]['stats']['cached']==1
    assert not window.make_plan().export_history
    window.export_history.setChecked(True);assert window.make_plan().export_history
    window.close()


def test_account_switch_updates_managed_default_archive_without_changing_custom_path(tmp_path):
    from engine import account_key,account_root
    window=MainWindow();window.output.setText(str(tmp_path))
    a=account_key('first@example.invalid');b=account_key('second@example.invalid')
    window.identity=a;window.ensure_archive_path()
    first=window.archive_path.text()
    assert first==str(account_root(tmp_path,a)/'garmin-offline.sqlite')
    window.identity=b;window.ensure_archive_path()
    assert window.archive_path.text()==str(account_root(tmp_path,b)/'garmin-offline.sqlite')
    custom=str(tmp_path/'my-custom.sqlite');window.archive_path.setText(custom)
    window.identity=a;window.ensure_archive_path()
    assert window.archive_path.text()==custom
    window.close()


def test_inferred_skip_option_and_recovery_button_are_bilingual():
    from PySide6.QtWidgets import QPushButton
    window=MainWindow(demo=True)
    assert not window.skip_empty.isChecked() and not window.make_plan().skip_empty
    window.skip_empty.setChecked(True)
    assert window.make_plan().skip_empty
    assert any(b.text()=='Übersprungene Abfragen trotzdem laden' for b in window.findChildren(QPushButton))
    window.toggle_language()
    assert window.make_plan().skip_empty
    assert any(b.text()=='Retrieve skipped requests anyway' for b in window.findChildren(QPushButton))
    assert 'Experimental' in window.help.toPlainText()
    window.close()
