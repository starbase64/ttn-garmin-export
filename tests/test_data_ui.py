import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt,QDate,QEvent
from PySide6.QtTest import QTest
import pytest
from app import MainWindow
from sessionlog import SessionLog

QT_APP=QApplication.instance() or QApplication([])

@pytest.fixture(autouse=True)
def cleanup():
    yield
    for widget in QApplication.topLevelWidgets():widget.close();widget.deleteLater()
    QApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);QApplication.processEvents()

def sample():
    data={'activities':[],'tracks':{},'health':[{'date':'2020-01-01','steps':3000,'sleep_hours':7}],'account':'test'}
    for i in range(3):
        key=str(i+1)
        data['activities'].append({'activityId':i+1,'_key':key,'_day':'2020-01-0'+key,'activityName':'Ride '+key,'activityType':{'typeKey':'cycling'},'startTimeLocal':'2020-01-0'+key+' 10:00:00','distance':(i+1)*10000,'duration':3600})
        data['tracks'][key]=[(51+i*.01,8),(51.05+i*.01,8.05)] if i!=1 else []
    return data

def test_real_ctrl_shift_click_selection_map_sync_filters_and_language(monkeypatch):
    window=MainWindow(demo=True);window.resize(1450,1000);window.tabs.setCurrentWidget(window.data_page);window.show();view=window.data_page
    calls=[];monkeypatch.setattr(view.map.network,'get',lambda *a:calls.append(a))
    view.loaded(sample());QApplication.processEvents()
    def click(row,mod=Qt.KeyboardModifier.NoModifier):
        index=view.table.model().index(row,3)
        QTest.mouseClick(view.table.viewport(),Qt.MouseButton.LeftButton,mod,view.table.visualRect(index).center());QApplication.processEvents()
    click(0);assert len(view.selected())==1 and len(view.map.routes)==1
    click(2,Qt.KeyboardModifier.ControlModifier);assert len(view.selected())==2 and len(view.map.routes)==2
    click(0);click(2,Qt.KeyboardModifier.ShiftModifier)
    assert len(view.selected())==3 and len(view.map.routes)==2
    assert '1 ohne' in view.selection_note.text()
    # Checkbox and row selection share one model.
    item=view.table.item(0,0);item.setCheckState(Qt.CheckState.Unchecked)
    assert len(view.selected())==2
    view.search.setText('Ride 1');assert view.table.rowCount()==1 and not view.selected() and not view.map.routes
    view.table.selectAll();assert len(view.map.routes)==1
    view.heat.setChecked(True);assert max(view.map.density[0].values())==1
    window.toggle_language();assert view.views.tabText(1)=='Daily data' and window.tabs.tabText(window.tabs.indexOf(view))=='Data'
    assert not calls

def test_session_log_retains_more_than_display_and_clear(tmp_path):
    log=SessionLog(tmp_path)
    for i in range(510):log.appendPlainText('entry '+str(i))
    assert log.document().blockCount()==500
    log.clear();log.appendPlainText('after clear')
    text=log.path.read_text(encoding='utf-8')
    assert 'entry 0\n' in text and 'entry 509\n' in text and 'after clear' in text
    other=SessionLog(tmp_path);assert other.path!=log.path

def test_oldest_activity_suggestion_is_bounded_and_preserves_user_date(monkeypatch,tmp_path):
    import app as module
    calls=[]
    class API:
        def __init__(self,**kwargs):self.client=SimpleNamespace(_tokenstore_path=None)
        def login(self,path):pass
        def get_activity_types(self):return []
        def count_activities(self):calls.append('count');return 9876
        def get_activities(self,start,limit):calls.append((start,limit));return [{'startTimeLocal':'2011-06-01 12:00:00'}]
    monkeypatch.setattr(module,'Garmin',API)
    window=MainWindow();window.data_dir=tmp_path;window.email.setText('test@example.invalid');window.message=lambda *a:None
    window.run_worker=lambda op,done:done(op(SimpleNamespace(ask_mfa=lambda:'000000')))
    window.login();assert window.start_date.date()==QDate(2011,6,1)
    assert calls==['count',(9875,1)]
    window.start_date.setDate(QDate(2022,1,1));window.login()
    assert window.start_date.date()==QDate(2022,1,1)

def test_poster_and_privacy_mask_create_real_png(tmp_path):
    from PySide6.QtGui import QImage
    window=MainWindow(demo=True);view=window.data_page;view.loaded(sample());view.table.selectAll()
    view.map.resize(650,450)
    path=tmp_path/'tour.png';view.map.poster(str(path),'Synthetic test','60 km')
    image=QImage(str(path));assert not image.isNull() and image.height()==900
    view.privacy.setValue(10000);assert not view.map.routes

def test_lazy_track_worker_reads_selected_local_file_and_finishes(tmp_path):
    from datahub import import_files,load_archive
    from engine import account_key
    from tests.test_datahub import GPX
    file=tmp_path/'ride.gpx';file.write_bytes(GPX);archive=tmp_path/'data.sqlite'
    import_files(archive,[file],account_key('local-test'))
    window=MainWindow();window.archive_path.setText(str(archive));view=window.data_page
    data=load_archive(archive,with_tracks=False);assert not data['tracks']
    view.loaded(data);view.table.selectAll()
    for _ in range(100):
        QTest.qWait(20)
        if view.track_worker is None:break
    assert view.track_worker is None and len(view.map.routes)==1
    assert '1 Strecken' in view.selection_note.text()
    window.toggle_language();assert len(view.selected())==1 and len(view.map.routes)==1

def test_eta_display_retry_rate_pause_and_offline_completion(tmp_path):
    from failures import RatePause
    window=MainWindow();window.rate_pause=RatePause(tmp_path/'pause.json')
    window.on_event(('eta',{'seconds':180,'pending':10,'provisional':True}));window.on_event(('request',12))
    assert 'Restzeit' in window.eta_label.text() and 'vorläufig' in window.eta_label.text() and '12 Garmin' in window.eta_label.text()
    window.on_event(('retry_wait',5));assert 'Wiederholung' in window.eta_label.text()
    window.on_event(('retry_wait',0));window.toggle_language();window.update_eta();assert 'Estimated' in window.eta_label.text()
    window.register_rate_limit({'reason':'rate_limit','retry_after_seconds':60});assert not window.eta_active and 'pause' in window.eta_label.text()
    window.on_event(('phase','offline'));window.on_event(('eta_end','complete'))
    assert 'Local export finished' in window.eta_label.text() and 'pause' not in window.eta_label.text()

def test_selected_archive_sync_honors_export_categories_and_excludes_activity_extras(tmp_path,monkeypatch):
    import app as module
    window=MainWindow(demo=True);window.archive_path.setText(str(tmp_path/'selected.sqlite'))
    window.select_all(False);window.dataset_checks['sleep'].setChecked(True);window.optimize.setChecked(False)
    captured=[]
    class Sync:
        def __init__(self,api,plan,*args):captured.append(plan)
        def run_sync(self):pass
    monkeypatch.setattr(module,'ArchiveSync',Sync)
    window.run_worker=lambda op,done:op(SimpleNamespace(cancel=threading.Event(),event=SimpleNamespace(emit=lambda *a:None)))
    import threading
    window.start_selected_archive_sync();p=captured[0]
    assert p.datasets==['sleep'] and not p.activity_files and not p.activity_details and not p.optimize


def test_data_map_occupies_full_height_right_pane_and_guide_is_last():
    from PySide6.QtCore import QPoint
    window=MainWindow(demo=True);window.resize(1500,1100)
    window.tabs.setCurrentWidget(window.data_page);window.show();QApplication.processEvents()
    view=window.data_page
    assert window.tabs.tabText(window.tabs.count()-1)=='Anleitung'
    assert view.map_panel.mapTo(view,QPoint(0,0)).y()<20
    assert view.map.height()>view.height()*.6
    # On Windows the left content can be wider than its scroll viewport.
    # Check the visible pane boundary, not an intentionally clipped child.
    left_scroll=view.browser_splitter.widget(0)
    assert view.left_panel.isAncestorOf(view.cache_button)
    assert left_scroll.mapTo(view,QPoint(left_scroll.width(),0)).x()<=view.map_panel.mapTo(view,QPoint(0,0)).x()
    window.toggle_language()
    assert window.tabs.tabText(window.tabs.count()-1)=='User guide'
    window.close()


def test_missing_activities_message_is_not_a_filter_warning_and_translates():
    window=MainWindow(demo=True);view=window.data_page
    data={'activities':[],'tracks':{},'health':[],'activity_status':{'responses':0,'empty_responses':0,'failed_requests':0}}
    view.loaded(data)
    assert 'Keine Aktivitätsübersichten' in view.note.text()
    window.toggle_language();assert 'No activity summaries saved' in view.note.text()
    data['activity_status']={'responses':5,'empty_responses':5,'failed_requests':0};view.loaded(data)
    assert 'only empty lists' in view.note.text()
    window.close()
