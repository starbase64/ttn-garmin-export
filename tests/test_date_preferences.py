"""Account-specific first-activity dates and user-selected starts survive restart."""
import json
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QDate,QTimer,QStandardPaths,QEvent
from app import MainWindow
from engine import account_key

QT_APP=QApplication.instance() or QApplication([])

@pytest.fixture
def windows(monkeypatch,tmp_path):
    monkeypatch.setattr(QStandardPaths,'writableLocation',lambda kind:str(tmp_path))
    monkeypatch.setattr(QTimer,'singleShot',lambda *args:None)
    created=[]
    def create():
        w=MainWindow();w.message=lambda *args:None;created.append(w);return w
    yield create
    for w in created:w.close();w.deleteLater()
    QApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def login(w,email='dates@example.invalid',first='2012-06-03'):
    ident=account_key(email)
    w.login_done((object(),ident,email,[],first,{}))
    w.saved_account_path.write_text(json.dumps({'schema':1,'email':email,'china':False}))
    return ident


def test_first_date_survives_restart_in_export_and_data_tabs(windows):
    first=windows();ident=login(first)
    assert first.start_date.date()==QDate(2012,6,3)
    assert first.data_page.date_from.date()==QDate(2012,6,3)
    first.close()
    second=windows()
    assert second.start_date.date()==QDate(2012,6,3)
    assert second.data_page.date_from.date()==QDate(2012,6,3)
    assert second.reset_start_button.isEnabled() and second.data_page.reset_date_button.isEnabled()
    assert second.date_values(ident)['first_activity']=='2012-06-03'


def test_custom_dates_and_reset_survive_restart_without_overwriting_first(windows):
    w=windows();ident=login(w)
    w.start_date.setDate(QDate(2020,1,2));w.data_page.date_from.setDate(QDate(2021,2,3));w.close()
    second=windows()
    assert second.start_date.date()==QDate(2020,1,2)
    assert second.data_page.date_from.date()==QDate(2021,2,3)
    assert second.first_activity_date=='2012-06-03'
    second.reset_start_button.click();second.data_page.reset_date_button.click();second.close()
    third=windows()
    assert third.start_date.date()==QDate(2012,6,3)
    assert third.data_page.date_from.date()==QDate(2012,6,3)
    assert third.date_values(ident)['first_activity']=='2012-06-03'


def test_dates_are_account_specific_and_local_subset_does_not_replace_account_first(windows):
    w=windows();a=login(w);w.start_date.setDate(QDate(2020,1,2))
    b=login(w,'other@example.invalid','2018-04-05')
    assert w.start_date.date()==QDate(2018,4,5)
    assert w.date_values(a)['first_activity']=='2012-06-03'
    assert w.date_values(a)['export_from']=='2020-01-02'
    w.data_page.loaded({'account':a,'activities':[], 'tracks':{},'health':[]})
    assert w.data_page.first_activity_date=='2012-06-03'
    w.data_page.reset_date_button.click()
    assert w.data_page.date_from.date()==QDate(2012,6,3)
    assert w.first_activity_date=='2018-04-05' and w.date_account==b


def test_missing_or_invalid_dates_leave_reset_disabled(windows):
    w=windows();ident=account_key('unknown@example.invalid')
    w.save_date_values(ident,first_activity='not-a-date',export_from='9999-01-01')
    w.activate_date_account(ident)
    assert not w.reset_start_button.isEnabled() and not w.data_page.reset_date_button.isEnabled()
    assert w.date_values(ident)=={}


def test_offline_export_uses_archive_account_dates_and_restores_online_choice(windows,tmp_path):
    from archive import Archive
    w=windows();a=login(w);w.start_date.setDate(QDate(2020,1,2))
    b=account_key('offline@example.invalid');w.save_date_values(b,first_activity='2016-07-08',export_from='2022-02-03')
    path=tmp_path/'other.sqlite'
    with Archive(path,b):pass
    w.archive_path.setText(str(path));w.use_offline.setChecked(True)
    assert w.date_account==b and w.start_date.date()==QDate(2022,2,3)
    w.reset_start_button.click();assert w.start_date.date()==QDate(2016,7,8)
    w.use_offline.setChecked(False)
    assert w.date_account==a and w.start_date.date()==QDate(2020,1,2)
