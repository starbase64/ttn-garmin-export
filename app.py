"""Local bilingual desktop app. Run with --demo for clearly labelled synthetic data."""
from __future__ import annotations
import inspect
import json
import logging
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import math
import re
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from PySide6.QtCore import Qt, QDate, QThread, Signal, QUrl, QStandardPaths, QTimer
from PySide6.QtGui import QDesktopServices,QImage,QPainter,QPen,QColor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QLineEdit, QPushButton, QTabWidget, QCheckBox, QDateEdit,
    QComboBox, QDoubleSpinBox, QSpinBox, QFileDialog, QGroupBox, QScrollArea, QTextBrowser,
    QPlainTextEdit, QProgressBar, QMessageBox, QInputDialog, QListWidget, QListWidgetItem,
    QSplitter, QLayout, QDialog, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)
from garminconnect import Garmin, GarminConnectTooManyRequestsError, GarminConnectAuthenticationError
from catalog import DATASETS, ACTIVITY_DETAILS, selected_datasets, all_definitions
from engine import Plan, Exporter, account_key, leaves, read_methods, substitute, field_patterns, MAX_WORKERS, atomic_json, account_root
from help_content import HELP
from sports import FALLBACK_SPORTS, activity_type_keys, sport_label
from activity_table import SOURCE_PATHS
from preview import Previewer
from failures import diagnostic, RatePause
from archive import Archive,OfflineArchiveError,OfflineDataMissing
from offline import ArchiveSync,OfflineExporter,OfflinePreviewer,ArchiveExporter
from sessionlog import SessionLog

logging.getLogger('garminconnect').setLevel(logging.CRITICAL)
logging.getLogger('curl_cffi').setLevel(logging.CRITICAL)
VERSION = '0.5.4-preview'
# Lower bound for long archive syncs, even with a 0 s interval in the options.
MIN_SYNC_INTERVAL = 0.25

def default_output_folder():
    downloads=QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    return (Path(downloads) if downloads else Path.home()/'Downloads')/'Garmin_Export'

class Worker(QThread):
    event = Signal(object)
    result = Signal(object)
    failed = Signal(object)
    mfa = Signal()

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.cancel = threading.Event()
        self.answers = queue.Queue()

    def ask_mfa(self):
        self.mfa.emit()
        while not self.cancel.is_set():
            try:
                code = self.answers.get(timeout=.2)
                if code is None:
                    raise RuntimeError('MFA cancelled')
                return code
            except queue.Empty:
                continue
        raise RuntimeError('MFA cancelled')

    def run(self):
        try:
            self.result.emit(self.operation(self))
        except Exception as exc:
            self.failed.emit(diagnostic(exc))
        finally:
            self.operation = None  # Release credential closures after the operation.


class MainWindow(QMainWindow):
    def __init__(self, demo=False):
        super().__init__()
        self.demo = demo
        self.lang = 'de'
        self.bindings = []
        self.api = None
        self.identity = None
        self.worker = None
        self._closing=False
        self._restoring=False
        self.last_output = ''
        self.completed = 0
        self.field_checks = []
        self.sport_keys = list(FALLBACK_SPORTS)
        self.sports_loaded = False
        self.date_user_set = False
        self.date_account=None;self.first_activity_date=None
        self.resize(1180, 870)
        self.setMinimumSize(900, 650)
        self.setWindowTitle('TTN Garmin Export · ' + VERSION)
        self.data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tick=self.data_dir/'checkbox-tick.png'
        icon=QImage(24,24,QImage.Format.Format_ARGB32);icon.fill(Qt.GlobalColor.transparent)
        painter=QPainter(icon);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen=QPen(QColor('white'),3);pen.setCapStyle(Qt.PenCapStyle.RoundCap);painter.setPen(pen)
        painter.drawLine(5,12,10,17);painter.drawLine(10,17,20,6);painter.end();icon.save(str(tick))
        self.tick_style=f'QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {{ image:url({json.dumps(tick.as_posix())}); }}'
        self.rate_pause=RatePause(self.data_dir/'rate-pause.json')
        self.setStyleSheet('''
          QMainWindow, QWidget { background: #f4f7fb; color:#192b43; font-size:14px; }
          QLabel#title { font-size:27px; font-weight:700; }
          QLabel#subtitle { color:#53677f; }
          QTabWidget::pane { border:1px solid #dbe2ec; border-radius:8px; background:white; }
          QTabBar::tab { padding:12px 20px; background:#e7edf5; }
          QTabBar::tab:selected { background:#ffffff; color:#076fa0; font-weight:bold; }
          QGroupBox { border:1px solid #d8e1ec; border-radius:8px; margin-top:18px; padding:16px; font-weight:600; }
          QGroupBox::title { subcontrol-origin:margin; left:14px; padding:0 5px; }
          QLineEdit, QDateEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QListWidget, QTextBrowser { background:white; border:1px solid #c7d3e2; border-radius:5px; padding:6px; }
          QPushButton { background:#e3ebf4; border:0; border-radius:6px; padding:9px 15px; }
          QPushButton:hover { background:#d1e3f1; }
          QPushButton#primary { background:#087eaa; color:white; font-weight:bold; }
          QPushButton:disabled { background:#e4e9ee; color:#95a0af; }
          QCheckBox { spacing:8px; padding:3px; }
          QCheckBox::indicator { width:24px; height:24px; border:2px solid #58718e; border-radius:4px; background:white; }
          QCheckBox::indicator:checked { background:#087eaa; border-color:#075f82; }
          QCheckBox::indicator:disabled { border-color:#a3afbd; background:#e0e5ec; }
          QCheckBox:focus { border:1px dashed #087eaa; border-radius:4px; }
          QTableView::indicator { width:22px; height:22px; border:2px solid #58718e; border-radius:3px; background:white; }
          QTableView::indicator:checked { background:#087eaa; border-color:#075f82; }
          QProgressBar { border:0; border-radius:5px; background:#dce6ef; text-align:center; }
          QProgressBar::chunk { background:#0998af; border-radius:5px; }
        ''')
        self.setStyleSheet(self.styleSheet()+self.tick_style)
        central = QWidget(); outer = QVBoxLayout(central); outer.setContentsMargins(24,18,24,18)
        top = QHBoxLayout(); heading = QVBoxLayout()
        title = QLabel('TTN Garmin Export'); title.setObjectName('title'); heading.addWidget(title)
        self.subtitle = self.label('Deine Daten. Dein Zeitraum. Lokal auf deinem Rechner.', 'Your data. Your date range. On your computer.')
        self.subtitle.setObjectName('subtitle'); heading.addWidget(self.subtitle); top.addLayout(heading); top.addStretch()
        self.language = QPushButton('English'); self.language.clicked.connect(self.toggle_language); top.addWidget(self.language)
        outer.addLayout(top)
        if demo:
            warning = self.label('DEMO · Nur künstliche Beispieldaten, kein Garmin-Zugriff', 'DEMO · Synthetic samples only, no Garmin access')
            warning.setStyleSheet('background:#fff1d0; padding:8px; color:#815000; font-weight:bold;'); outer.addWidget(warning)
        self.connection = self.label('Nicht angemeldet', 'Not signed in'); outer.addWidget(self.connection)
        self.source_label=QLabel();outer.addWidget(self.source_label)
        self.cooldown_label=QLabel();self.cooldown_label.setWordWrap(True);self.cooldown_label.setStyleSheet('color:#926000');outer.addWidget(self.cooldown_label)
        self.tabs = QTabWidget(); outer.addWidget(self.tabs, 1)
        self.config_tabs = []
        self.account_page(); self.export_page(); self.advanced_page(); self.fields_page(); self.expert_page(); self.offline_page()
        from data_page import DataPage
        self.data_page=DataPage(self)
        self.add_tab(self.data_page,'Daten','Data')
        self.help_page()
        self.tabs.setUsesScrollButtons(True)
        bottom = QHBoxLayout()
        self.start_button = self.button('Export starten', 'Start export', self.start_export, primary=True)
        self.stop_button = self.button('Abbrechen', 'Stop', self.stop); self.stop_button.setEnabled(False)
        self.open_button = self.button('Ergebnisordner öffnen', 'Open results folder', self.open_results); self.open_button.setEnabled(False)
        self.export_preview_button=self.button('Vorschau anzeigen','Show preview',self.show_export_preview)
        bottom.addWidget(self.export_preview_button); bottom.addWidget(self.start_button); bottom.addWidget(self.stop_button); bottom.addStretch(); bottom.addWidget(self.open_button)
        outer.addLayout(bottom)
        self.progress = QProgressBar(); self.progress.setValue(0); outer.addWidget(self.progress)
        self.status = self.label('Bereit · Vorschauversion', 'Ready · Preview version'); outer.addWidget(self.status)
        self.eta_label=QLabel();self.eta_label.setWordWrap(True);self.eta_label.setStyleSheet('font-weight:600; color:#087eaa');outer.addWidget(self.eta_label)
        self.eta_snapshot=None;self.eta_received=0.;self.eta_active=False;self.retry_until=0.;self.request_count=0
        self.local_transfer=False
        self.eta_timer=QTimer(self);self.eta_timer.timeout.connect(self.update_eta);self.eta_timer.start(1000)
        log_actions=QHBoxLayout();log_actions.addWidget(self.button('Log öffnen','Open log',self.open_log));log_actions.addWidget(self.button('Log der heutigen Sitzung exportieren','Export current session log',self.export_log));log_actions.addStretch();outer.addLayout(log_actions)
        self.log = SessionLog(self.data_dir/'logs'); self.log.setMaximumHeight(110); outer.addWidget(self.log)
        self.setCentralWidget(central)
        # Set image rules on the controls themselves: inherited Qt stylesheet images
        # can otherwise disappear inside nested widgets on some platform styles.
        for checkbox in self.findChildren(QCheckBox):checkbox.setStyleSheet(self.tick_style)
        self.data_page.table.setStyleSheet(self.tick_style);self.fields_list.setStyleSheet(self.tick_style)
        self.cooldown_timer=QTimer(self);self.cooldown_timer.timeout.connect(self.update_cooldown_label);self.cooldown_timer.start(10000);self.update_cooldown_label()
        if demo:
            from demo_data import DemoGarmin
            self.api = DemoGarmin(); self.identity = account_key('demo@example.invalid')
            self.ensure_archive_path()
            self.connection.setText('DEMO · demo@example.invalid')
            self.tabs.setCurrentIndex(1)
        else:
            self.load_saved_account()
        self.remember.toggled.connect(self.remember_changed)

    @property
    def saved_account_path(self):return self.data_dir/'remembered-account.json'

    def date_values(self,account):
        if not account or self.demo:return {}
        try:
            path=self.data_dir/'date-preferences'/(account_key(str(account))+'.json')
            values=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(values,dict):return {}
            result={}
            for key in ('first_activity','export_from','data_from'):
                value=values.get(key)
                day=QDate.fromString(value,'yyyy-MM-dd') if isinstance(value,str) else QDate()
                if day.isValid() and QDate(1990,1,1)<=day<=QDate.currentDate():result[key]=value
            return result
        except (OSError,ValueError,TypeError):return {}

    def save_date_values(self,account,**changes):
        if not account or self.demo:return
        values=self.date_values(account);values.update(changes)
        try:atomic_json(self.data_dir/'date-preferences'/(account_key(str(account))+'.json'),values)
        except OSError:self.log.appendPlainText(self.tr('Datumsauswahl konnte nicht gespeichert werden. Schreibrechte prüfen.','Could not save date selection. Check write permissions.'))

    def export_date_changed(self,*args):
        self.date_user_set=True
        self.save_date_values(self.date_account,export_from=self.start_date.date().toString('yyyy-MM-dd'))

    def activate_date_account(self,account):
        previous=self.date_account
        if previous and previous!=account:
            self.date_user_set=False
            self.start_date.blockSignals(True);self.start_date.setDate(QDate.currentDate().addDays(-6));self.start_date.blockSignals(False)
        self.date_account=account;values=self.date_values(account)
        self.first_activity_date=values.get('first_activity')
        self.reset_start_button.setEnabled(bool(self.first_activity_date))
        chosen=values.get('export_from')
        # A date changed before a manual login still takes precedence.
        if chosen and not (previous is None and self.date_user_set):
            self.start_date.blockSignals(True);self.start_date.setDate(QDate.fromString(chosen,'yyyy-MM-dd'));self.start_date.blockSignals(False)
            self.date_user_set=True
        elif self.first_activity_date and not self.date_user_set:
            self.start_date.blockSignals(True);self.start_date.setDate(QDate.fromString(self.first_activity_date,'yyyy-MM-dd'));self.start_date.blockSignals(False)
        if self.date_user_set:self.save_date_values(account,export_from=self.start_date.date().toString('yyyy-MM-dd'))
        if self.data_page.date_account in (None,account):self.data_page.restore_date_filter(account)

    def reset_activity_start(self):
        if not self.first_activity_date:return
        first=QDate.fromString(self.first_activity_date,'yyyy-MM-dd')
        if self.end_date.date()<first:self.end_date.setDate(first)
        self.start_date.setDate(first)
        self.save_date_values(self.date_account,export_from=self.first_activity_date)

    def load_saved_account(self):
        try:
            saved=json.loads(self.saved_account_path.read_text(encoding='utf-8'))
            email=saved['email'];china=saved['china']
            if saved.get('schema')!=1 or not isinstance(email,str) or not email.strip() or len(email)>320 or type(china) is not bool:return
        except (OSError,ValueError,KeyError,TypeError):return
        self.email.setText(email);self.china.setChecked(china)
        self.activate_date_account(account_key(email,china))
        auto_restore=saved.get('auto_restore',True) is True
        self.remember.setChecked(auto_restore)
        if not auto_restore:return
        self._restore_account=(email,china)
        QTimer.singleShot(0,self.restore_saved_session)

    def restore_saved_session(self):
        if self._closing or self.api or not self.remember.isChecked():return
        if self.worker and self.worker.isRunning():return
        if (self.email.text().strip(),self.china.isChecked())!=getattr(self,'_restore_account',None):return
        if self.rate_pause.remaining():
            self.log.appendPlainText(self.tr('Gespeicherte Sitzung: Wiederherstellung wegen aktiver Abrufpause ausgesetzt.','Saved session: restore skipped during the active request pause.'));return
        self.log.appendPlainText(self.tr('Gespeicherte Sitzung wird wiederhergestellt …','Restoring saved session …'))
        self.login(restore=True)

    def restore_failed(self,detail):
        self.register_rate_limit(detail)
        rejected=detail.get('reason') in ('authentication','forbidden') or detail.get('error_type')=='GarminConnectAuthenticationError'
        if rejected:
            # Keep account selection, but do not retry rejected tokens on every start.
            try:atomic_json(self.saved_account_path,{'schema':1,'email':self.email.text().strip(),'china':self.china.isChecked(),'auto_restore':False})
            except OSError:
                self.log.appendPlainText(self.tr('Automatische Anmeldung konnte nicht dauerhaft deaktiviert werden. Schreibrechte prüfen.','Could not persist disabled automatic sign-in. Check write permissions.'))
            self.remember.blockSignals(True);self.remember.setChecked(False);self.remember.blockSignals(False)
            text=self.tr('Gespeicherte Sitzung wurde abgelehnt. Bitte mit Passwort neu anmelden; zum Speichern „Sitzung lokal merken“ wieder aktivieren.',
                         'Saved session was rejected. Please sign in with your password; enable Remember session to save it again.')
            self.tabs.setCurrentIndex(0);self.password.setFocus()
        else:
            text=self.tr('Sitzung konnte gerade nicht wiederhergestellt werden. Konto bleibt für den nächsten Start gemerkt. ',
                         'Could not restore the session right now. Account remains remembered for the next start. ')+self.error_description(detail).replace('\n',' · ')
        self.log.appendPlainText(text)

    def remember_changed(self,checked):
        if not checked and not self.demo:
            try:self.saved_account_path.unlink(missing_ok=True)
            except OSError:self.message('Die automatische Anmeldung konnte nicht deaktiviert werden. Schreibrechte prüfen.','Could not disable automatic sign-in. Check write permissions.')

    def tr(self, de, en): return de if self.lang == 'de' else en
    def bind(self, widget, de, en, setter='setText'):
        self.bindings.append((widget, de, en, setter)); getattr(widget, setter)(self.tr(de, en)); return widget
    def label(self, de, en):
        obj = self.bind(QLabel(), de, en); obj.setWordWrap(True); return obj
    def button(self, de, en, callback, primary=False):
        obj = self.bind(QPushButton(), de, en); obj.clicked.connect(callback)
        if primary: obj.setObjectName('primary')
        return obj
    def check(self, de, en, checked=False):
        obj = self.bind(QCheckBox(), de, en); obj.setChecked(checked);obj.setStyleSheet(self.tick_style);return obj
    def group(self, de, en): return self.bind(QGroupBox(), de, en, 'setTitle')
    def add_tab(self, widget, de, en):
        idx = self.tabs.addTab(widget, self.tr(de,en)); self.config_tabs.append((idx,de,en))
    def row(self, form, de, en, widget): form.addRow(self.label(de,en), widget)
    def combo(self, entries):
        c = QComboBox()
        for de, en, key in entries: c.addItem(self.tr(de,en), key)
        c.setProperty('entries', entries)
        return c

    def account_page(self):
        self.account_widget = QWidget(); v = QVBoxLayout(self.account_widget)
        g = self.group('Garmin-Konto', 'Garmin account'); f=QFormLayout(g)
        self.email = QLineEdit(); self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.row(f,'E-Mail','Email',self.email); self.row(f,'Passwort','Password',self.password)
        self.china = self.check('China-Konto (garmin.cn)', 'China account (garmin.cn)'); f.addRow(self.china)
        self.remember = self.check('Sitzung lokal merken (Anmeldeschlüssel wird gespeichert)', 'Remember session locally (stores an authentication token)'); f.addRow(self.remember)
        self.remember_hint=self.label('Abwählen deaktiviert die automatische Anmeldung. Gespeicherte Schlüssel bleiben erhalten; mit „Lokale Sitzung entfernen“ löschen.',
                                     'Unchecking disables automatic sign-in. Stored tokens remain; delete them with Remove local session.')
        f.addRow(self.remember_hint)
        self.detect_first = self.check('Erste Aktivität als Anfangsdatum vorschlagen','Suggest first activity as start date',True);f.addRow(self.detect_first)
        actions = QHBoxLayout(); self.login_button = self.button('Anmelden', 'Sign in', self.login); actions.addWidget(self.login_button)
        self.forget_button = self.button('Lokale Sitzung entfernen', 'Remove local session', self.forget); actions.addWidget(self.forget_button); f.addRow(actions)
        v.addWidget(g)
        v.addWidget(self.label('Passwort wird nicht gespeichert. Ein gemerkter Anmeldeschlüssel ist nicht zusätzlich verschlüsselt. Details stehen in der Anleitung.', 'Passwords are not saved. Remembered authentication tokens have no additional encryption. See the user guide.'))
        self.session_path_label = QLabel(str(self.data_dir / 'sessions')); self.session_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); v.addWidget(self.session_path_label)
        v.addStretch(); self.add_tab(self.account_widget,'Konto','Account')

    def export_page(self):
        self.export_widget=QWidget(); main=QVBoxLayout(self.export_widget)
        main.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        dates=QHBoxLayout()
        self.start_date=QDateEdit(QDate.currentDate().addDays(-6)); self.end_date=QDateEdit(QDate.currentDate())
        for d in (self.start_date,self.end_date): d.setCalendarPopup(True); d.setDisplayFormat('yyyy-MM-dd'); d.setMinimumDate(QDate(1990,1,1)); d.setMaximumDate(QDate.currentDate())
        self.start_date.dateChanged.connect(self.export_date_changed)
        dates.addWidget(self.label('Von','From')); dates.addWidget(self.start_date); dates.addWidget(self.label('Bis einschließlich','Through')); dates.addWidget(self.end_date)
        self.reset_start_button=self.button('Erste Aktivität','First activity',self.reset_activity_start)
        self.reset_start_button.setEnabled(False);dates.addWidget(self.reset_start_button)
        self.bind(self.reset_start_button,'Anfangsdatum auf die erste ermittelte Aktivität dieses Kontos zurücksetzen. Wird nach der Kontoabfrage verfügbar.','Reset start to the first detected activity of this account. Available after the account lookup.',setter='setToolTip')
        for count in (7,30,365):
            btn=self.button(f'{count} Tage',f'{count} days',lambda checked=False,n=count:self.quick_dates(n)); dates.addWidget(btn)
        main.addLayout(dates)
        main.addWidget(self.label('Mehrere Jahre sind möglich. Kategorien enthalten alle verfügbaren Felder und Verläufe.', 'Multiple years are supported. Categories include all available fields and timelines.'))
        controls=QHBoxLayout(); controls.addWidget(self.button('Alle auswählen','Select all',lambda:self.select_all(True))); controls.addWidget(self.button('Auswahl leeren','Select none',lambda:self.select_all(False))); controls.addWidget(self.button('Nur Aktivitätsliste (CSV)','Activity list only (CSV)',self.activity_list_preset)); controls.addStretch(); main.addLayout(controls)
        area=QScrollArea(); area.setWidgetResizable(True); content=QWidget(); columns=QHBoxLayout(content); left=QVBoxLayout(); right=QVBoxLayout(); columns.addLayout(left); columns.addLayout(right)
        self.dataset_checks={}
        for idx, ds in enumerate(DATASETS):
            cb=self.check(ds.de,ds.en,ds.key in ('summary','heart_rate','sleep')); self.dataset_checks[ds.key]=cb
            (left if idx<14 else right).addWidget(cb)
        left.addStretch(); right.addStretch(); area.setWidget(content); body=QHBoxLayout(); body.addWidget(area,3)
        outputs=self.group('Ausgabe', 'Output'); outputs.setObjectName('outputGroup')
        outputs.setStyleSheet('QGroupBox#outputGroup QWidget { font-weight:400; }')
        form=QFormLayout(outputs); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        form.setVerticalSpacing(10)
        self.output=QLineEdit(str(default_output_folder())); line=QHBoxLayout(); line.addWidget(self.output); line.addWidget(self.button('Ordner wählen','Choose folder',self.choose_folder)); self.row(form,'Zielordner','Output folder',line)
        fmt=QHBoxLayout(); self.format_checks={}
        for key in ('csv','xlsx','json'):
            cb=QCheckBox(key.upper()); cb.setChecked(key=='csv'); self.format_checks[key]=cb; fmt.addWidget(cb)
        fmt.addStretch(); self.row(form,'Formate','Formats',fmt)
        self.layout_combo=self.combo([('Langformat · Detailwerte','Long layout · leaf values','long'),('Breitformat · Datensätze','Wide layout · records','wide')]); self.row(form,'Tabellenaufbau','Table layout',self.layout_combo)
        self.activity_summary=self.check('Aktivitäten: eine Tabellenzeile pro Aktivität','Activities: one table row per activity',True); form.addRow(self.activity_summary)
        self.split=self.check('Dateien nach Jahr trennen','Split files by year',True); form.addRow(self.split)
        self.export_history=self.check('Separate Exportordner behalten','Keep separate export folders');form.addRow(self.export_history)
        form.addRow(self.label('Standard: ein aktueller Export pro Account unter exports/current. Ein neuer Export ersetzt dort die erzeugten Dateien. Heruntergeladene Daten bleiben im gemeinsamen Account-Bestand. Alte Exportordner bleiben erhalten.',
                              'Default: one current export per account in exports/current. A new export replaces the generated files there. Downloaded data stays in the shared account archive. Existing export folders are retained.'))
        self.health_detail=self.combo([('Vollständige Messverläufe','Full measurement timelines','full'),('Tageskennzahlen (schneller)','Daily statistics (faster)','daily')])
        self.health_detail.currentIndexChanged.connect(self.update_health_labels)
        self.row(form,'Gesundheitsdaten','Health data',self.health_detail)
        form.addRow(self.label('Tageskennzahlen enthalten keine Messverläufe. Gilt für Puls, Schlaf, Stress, Body Battery, Schritte, Etagen, Intensitätsminuten, Atmung, Trinkmenge und Ruhepuls. Andere Kategorien und Aktivitäten bleiben unverändert.',
                              'Daily statistics contain no timelines. Applies to heart rate, sleep, stress, Body Battery, steps, floors, intensity minutes, respiration, hydration and resting heart rate. Other categories and activities are unchanged.'))
        # Never squeeze text or checkbox indicators when the available height is
        # reduced by display scaling, a small window or transfer status rows.
        # Use each styled control's font-aware size hint, not a fixed pixel height.
        for control in outputs.findChildren(QWidget):
            if isinstance(control,(QLineEdit,QPushButton,QComboBox,QCheckBox)):
                control.ensurePolished()
                control.setMinimumHeight(max(control.sizeHint().height(),control.minimumSizeHint().height()))
        body.addWidget(outputs,2); main.addLayout(body,1)
        self.export_scroll=QScrollArea(); self.export_scroll.setWidgetResizable(True)
        self.export_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.export_scroll.setWidget(self.export_widget)
        self.add_tab(self.export_scroll,'Export','Export')

    def update_health_labels(self):
        definitions={d.key:d for d in all_definitions()}
        for key,checkbox in self.dataset_checks.items():
            d=definitions.get(key+'-daily',definitions[key]) if self.health_detail.currentData()=='daily' else definitions[key]
            checkbox.setText(self.tr(d.de,d.en))

    def advanced_page(self):
        self.advanced_widget=QScrollArea(); self.advanced_widget.setWidgetResizable(True)
        content=QWidget(); self.advanced_widget.setWidget(content); v=QVBoxLayout(content)
        v.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        activity=self.group('Aktivitäten – Zusätze', 'Activity extras'); f=QFormLayout(activity)
        self.sport=QComboBox(); self.sport.setEditable(True); self.sport.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.sport.setMaxVisibleItems(18)
        self.sport.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.sport.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.populate_sports()
        sport_row=QHBoxLayout(); sport_row.addWidget(self.sport,1)
        self.sports_button=self.button('Liste aktualisieren','Refresh list',self.refresh_sports); sport_row.addWidget(self.sports_button)
        self.row(f,'Sportart','Sport type',sport_row)
        self.sports_hint=QLabel(); self.sports_hint.setWordWrap(True); f.addRow(self.sports_hint)
        self.update_sports_hint()
        detail_row=QVBoxLayout(); self.detail_checks={}
        for key,(de,en) in ACTIVITY_DETAILS.items():
            cb=self.check(de,en); self.detail_checks[key]=cb; detail_row.addWidget(cb)
        f.addRow(detail_row)
        file_row=QHBoxLayout(); self.file_checks={}
        for key in ('ORIGINAL','GPX','TCX','KML','CSV'):
            cb=self.check('CSV (Runden)','CSV (laps)') if key=='CSV' else QCheckBox(key); file_row.addWidget(cb); self.file_checks[key]=cb
        self.row(f,'Originaldateien','Original downloads',file_row)
        v.addWidget(activity)
        network=self.group('Abruf und Tabellen', 'Retrieval and tables'); f=QVBoxLayout(network)
        f.setSpacing(10)
        def option_row(de,en,widget):
            caption=self.label(de,en); caption.setWordWrap(False)
            f.addWidget(caption); f.addWidget(widget)
        self.delay=QDoubleSpinBox(); self.delay.setRange(0,60); self.delay.setDecimals(2); self.delay.setSingleStep(0.1); self.delay.setValue(0.5); option_row('Abrufpause (Sekunden)','Request interval (seconds)',self.delay)
        self.workers=QSpinBox(); self.workers.setRange(1,MAX_WORKERS); self.workers.setValue(3); option_row('Parallele Abfragen','Parallel requests',self.workers)
        f.addWidget(self.label('Mehrere Abfragen laufen gleichzeitig; die Abrufpause gilt trotzdem für alle zusammen. Höhere Werte sind schneller, erhöhen aber das Risiko einer Garmin-Ratenbegrenzung (HTTP 429). Dann stoppt der Lauf und kann später fortgesetzt werden. Bereits gestartete Anfragen können noch zu Ende laufen.','Several requests run at the same time; the request interval still applies to all of them together. Higher values are faster but increase the risk of a Garmin rate limit (HTTP 429). The run then stops and can be resumed later. Requests already in flight may still finish.'))
        self.skip_empty=self.check('Vermutlich leere Abfragen überspringen (experimentell)','Skip likely empty requests (experimental)',False);f.addWidget(self.skip_empty)
        f.addWidget(self.label('Standard: aus. Leere Tageskennzahlen sind kein Beweis für fehlende Detaildaten. Übersprungenes bleibt ungeprüft und lässt sich unter Offline-Daten gezielt nachladen. Benötigt die Tagesübersicht; fehlende Felder werden normal abgefragt.',
                               'Default: off. Empty daily statistics do not prove that detail data is absent. Skipped requests remain unverified and can be retrieved under Offline data. Requires daily summary; missing fields are requested normally.'))
        self.optimize=self.check('Zeitraumabfragen bündeln (empfohlen)','Batch supported date ranges (recommended)',True);f.addWidget(self.optimize)
        self.activity_batch_days=QSpinBox();self.activity_batch_days.setRange(1,365);self.activity_batch_days.setValue(31);option_row('Aktivitäten: Tage je Zeitraumabfrage','Activities: days per range query',self.activity_batch_days)
        self.health_batch_days=QSpinBox();self.health_batch_days.setRange(1,28);self.health_batch_days.setValue(28);option_row('Gesundheit: Tage je Zeitraumabfrage','Health: days per range query',self.health_batch_days)
        f.addWidget(self.label('Tagesübersichten und Tageskennzahlen werden in bis zu 28 Tagen gebündelt. Body-Battery-Verläufe: bis zu 7 Tage. Detaillierte Puls- und Schlafverläufe bleiben tagweise. Den Detailgrad wählst du auf Export → Ausgabe.','Daily summaries and daily statistics batch up to 28 days. Body Battery timelines: up to 7 days. Detailed heart rate and sleep timelines remain daily. Choose the detail level under Export → Output.'))
        self.retries=QSpinBox(); self.retries.setRange(0,5); self.retries.setValue(2); option_row('Wiederholungen bei Netzwerkfehlern','Network error retries',self.retries)
        self.refresh=QSpinBox(); self.refresh.setRange(0,36500); self.refresh.setValue(2); option_row('Letzte Tage neu laden','Refresh recent days',self.refresh)
        f.addWidget(self.label('Anzahl der Tage, die erneut abgerufen werden. 0 = vorhandenen Cache nutzen.', 'Number of recent days to retrieve again. 0 = reuse the existing cache.'))
        self.delimiter=self.combo([('Semikolon (;)','Semicolon (;)',';'),('Komma (,)','Comma (,)',','),('Tabulator','Tab','\t')]); option_row('CSV-Trennzeichen','CSV separator',self.delimiter)
        self.preview_rows=QSpinBox(); self.preview_rows.setRange(1,50); self.preview_rows.setValue(10); option_row('Vorschau: Zeilen je Tabelle','Preview: rows per table',self.preview_rows)
        self.preview_requests=QSpinBox(); self.preview_requests.setRange(1,50); self.preview_requests.setValue(12); option_row('Vorschau: maximale Abfragen','Preview: maximum queries',self.preview_requests)
        v.addWidget(network)
        presets=QHBoxLayout(); presets.addWidget(self.button('Auswahl als Vorlage speichern','Save selection preset',self.save_preset)); presets.addWidget(self.button('Vorlage laden','Load preset',self.load_preset)); presets.addStretch(); v.addLayout(presets)
        v.addWidget(self.label('Aktivitätszusätze benötigen die Kategorie „Aktivitäten – Übersicht“. Originaldateien und Rohdaten-JSON werden nicht nach einzelnen Feldern gefiltert.', 'Activity extras require Activities – summary. Original downloads and raw JSON are not filtered by field selection.'))
        v.addStretch(); self.add_tab(self.advanced_widget,'Optionen','Options')

    def sport_value(self):
        index=self.sport.currentIndex()
        text=self.sport.currentText().strip()
        if index>=0 and text==self.sport.itemText(index):
            return self.sport.itemData(index) or ''
        return text  # Keep support for manually entered new Garmin keys.

    def set_sport(self,key):
        key=key.strip()
        index=self.sport.findData(key)
        if index<0:
            self.sport.addItem(sport_label(key,self.lang),key)
            index=self.sport.count()-1
        self.sport.setCurrentIndex(index)

    def populate_sports(self):
        selected=self.sport_value() if self.sport.count() else ''
        self.sport.blockSignals(True)
        self.sport.clear()
        self.sport.addItem(self.tr('Alle Sportarten','All sport types'),'')
        for key in sorted(set(self.sport_keys),key=lambda k:sport_label(k,self.lang).casefold()):
            self.sport.addItem(sport_label(key,self.lang),key)
        self.set_sport(selected)
        self.sport.blockSignals(False)

    def update_sports_hint(self):
        self.sports_hint.setText(self.tr(
            'Garmin-Liste geladen. Diese enthält auch Sportarten ohne eigene Aktivitäten.' if self.sports_loaded else 'Standardauswahl. Nach der Anmeldung wird die vollständige Garmin-Liste geladen.',
            'Garmin list loaded. This also includes sports without any of your own activities.' if self.sports_loaded else 'Default selection. The full Garmin list loads after sign-in.'))

    def refresh_sports(self):
        if self.use_offline.isChecked():
            def read_types(worker):
                with Archive(self.archive_path.text(),readonly=True) as db:return activity_type_keys(db.snapshot('activity_types'))
            self.run_worker(read_types,self.sports_done,network=False);return
        if not self.api:
            self.message('Bitte zuerst anmelden.','Please sign in first.'); return
        self.run_worker(lambda worker:activity_type_keys(self.api.get_activity_types()),self.sports_done)

    def sports_done(self,keys):
        if keys:
            self.sport_keys=keys; self.sports_loaded=True
            self.populate_sports(); self.update_sports_hint()
        else:
            self.message('Keine Sportarten empfangen. Die bisherige Auswahl bleibt verfügbar.',
                         'No sport types received. The previous selection remains available.')

    def fields_page(self):
        self.fields_widget=QWidget(); v=QVBoxLayout(self.fields_widget)
        v.addWidget(self.label('Optional: Nur ausgewählte Felder in CSV/Excel ausgeben. Leer = alle. Filter gelten für alle gewählten Kategorien; JSON und Cache bleiben vollständig.', 'Optional: restrict fields in CSV/Excel. Blank = all. Filters apply to all selected categories; JSON and cache remain complete.'))
        bar=QHBoxLayout(); self.preview_button=self.button('Felder laden (Starttag, erste Kategorie)','Load fields (first date and category)',self.preview); bar.addWidget(self.preview_button)
        bar.addWidget(self.button('Markierte Felder übernehmen','Apply checked fields',self.apply_fields)); v.addLayout(bar)
        self.fields_list=QListWidget(); v.addWidget(self.fields_list,1)
        v.addWidget(self.label('Pfadmuster · ein Muster pro Zeile · z. B. /totalSteps oder /heartRateValues/*/*','Path patterns · one per line · e.g. /totalSteps or /heartRateValues/*/*'))
        self.selectors=QPlainTextEdit(); self.selectors.setMaximumHeight(140); v.addWidget(self.selectors)
        self.add_tab(self.fields_widget,'Felder','Fields')

    def expert_page(self):
        self.expert_widget=QWidget(); v=QVBoxLayout(self.expert_widget)
        self.expert_enable=self.check('Zusätzliche Leseabfrage aktivieren','Enable an additional read query'); v.addWidget(self.expert_enable)
        v.addWidget(self.label('Erweiterter Zugriff auf get_*-Methoden der Bibliothek. Pflichtparameter und Paginierung beachten. Details in der Anleitung.', 'Advanced access to library get_* methods. Check required parameters and pagination. See the user guide.'))
        self.method=QComboBox(); self.method.setEditable(True); self.method.addItems(sorted(read_methods(Garmin))); self.method.currentTextChanged.connect(self.method_changed); v.addWidget(self.method)
        self.method_doc=QPlainTextEdit(); self.method_doc.setReadOnly(True); v.addWidget(self.method_doc,1)
        v.addWidget(self.label('Parameter als JSON · Platzhalter: {date}, {start}, {end}','Parameters as JSON · placeholders: {date}, {start}, {end}'))
        self.kwargs=QPlainTextEdit('{"cdate": "{date}"}'); self.kwargs.setMaximumHeight(160); v.addWidget(self.kwargs)
        self.expert_daily=self.check('Pro Tag ausführen (bei Bereichsabfragen ausschalten)','Run per day (disable for range queries)',True); v.addWidget(self.expert_daily)
        self.method.setCurrentText('get_naps'); self.method_changed('get_naps')
        self.add_tab(self.expert_widget,'Experten','Expert')

    def help_page(self):
        self.help=QTextBrowser(); self.help.setOpenExternalLinks(False); self.help.setHtml(HELP[self.lang]); self.add_tab(self.help,'Anleitung','User guide')

    def offline_page(self):
        self.offline_widget=QScrollArea();self.offline_widget.setWidgetResizable(True)
        content=QWidget();self.offline_widget.setWidget(content);v=QVBoxLayout(content);v.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        v.addWidget(self.label('Optionaler lokaler Datenbestand (SQLite). Einmal sichern, später ohne Anmeldung oder Garmin-Anfragen exportieren.',
                               'Optional local SQLite archive. Download once, export later without sign-in or Garmin requests.'))
        v.addWidget(self.label('„Alle“ umfasst die 27 unterstützten Datenarten im Zeitraum der Exportseite. Zusätzliche Aktivitätsdetails und Originaldateien sind wählbar. Kein vollständiger Export aller denkbaren Garmin-Kontodaten.',
                               '“All” covers the 27 supported datasets for the dates on the Export tab. Activity details and original files are optional. This is not a complete backup of every possible Garmin account feature.'))
        form=QFormLayout()
        self.archive_path=QLineEdit();line=QHBoxLayout();line.addWidget(self.archive_path,1);line.addWidget(self.button('Datei wählen / anlegen','Choose / create file',self.choose_archive))
        self.row(form,'Offline-Datenbank','Offline database',line);v.addLayout(form)
        self.use_offline=self.check('Für Vorschau und Export nur Offline-Daten verwenden','Use offline data only for previews and exports');v.addWidget(self.use_offline)
        options=QHBoxLayout()
        self.archive_details=self.check('Aktivitätsdetails mitsichern','Include activity details',True)
        self.archive_original=self.check('Original-Aktivitätsdateien mitsichern (größer)','Include original activity files (larger)',True)
        options.addWidget(self.archive_details);options.addWidget(self.archive_original);v.addLayout(options)
        extra_files=QHBoxLayout();extra_files.addWidget(self.label('Weitere Formate mitsichern:','Also archive these formats:'));self.archive_format_checks={}
        for fmt in ('GPX','TCX','KML','CSV'):
            cb=self.check('CSV (Runden)','CSV (laps)') if fmt=='CSV' else QCheckBox(fmt)
            self.archive_format_checks[fmt]=cb;extra_files.addWidget(cb)
        extra_files.addStretch();v.addLayout(extra_files)
        actions=QHBoxLayout()
        self.sync_button=self.button('Alle unterstützten Daten sichern / ergänzen','Download / update all supported data',self.start_archive_sync,primary=True)
        actions.addWidget(self.sync_button);actions.addWidget(self.button('Gespeicherten Bestand prüfen','Inspect stored data',self.inspect_archive));v.addLayout(actions)
        v.addWidget(self.button('Übersprungene Abfragen trotzdem laden','Retrieve skipped requests anyway',self.retrieve_skipped))
        v.addWidget(self.button('Nur gewählte Daten sichern / ergänzen','Download / update selected datasets',self.start_selected_archive_sync))
        v.addWidget(self.label('Zeitraum: Von/Bis auf der Exportseite. Fehlgeschlagene oder fehlende Daten werden ausgewiesen. Große Erstbefüllungen können lange dauern. Die bestehende Abrufpause gilt auch hier.',
                               'Dates: From/Through on the Export tab. Failed or missing data is reported. Large initial downloads can take a long time. Existing request pauses also apply here.'))
        self.archive_summary=QLabel();self.archive_summary.setWordWrap(True);v.addWidget(self.archive_summary)
        self.coverage_table=QTableWidget();self.coverage_table.setMinimumHeight(250);self.coverage_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.coverage_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        v.addWidget(self.coverage_table,1)
        self.archive_path.textChanged.connect(self.offline_source_changed)
        self.use_offline.toggled.connect(self.offline_source_changed)
        self.add_tab(self.offline_widget,'Offline-Daten','Offline data');self.offline_source_changed()

    def ensure_archive_path(self):
        if not self.identity:return
        expected=account_root(self.output.text(),self.identity)/'garmin-offline.sqlite'
        current=self.archive_path.text().strip()
        managed=False
        if current:
            path=Path(current).expanduser().resolve()
            managed=(path.name=='garmin-offline.sqlite' and re.fullmatch(r'account-[a-f0-9]{20}',path.parent.name)
                     and path.parent.name!='account-'+self.identity and not self.use_offline.isChecked())
        if not current or managed:self.archive_path.setText(str(expected))

    def choose_archive(self):
        self.ensure_archive_path()
        path,_=QFileDialog.getSaveFileName(self,self.tr('Datenbank auswählen oder neuen Dateinamen angeben','Choose database or enter a new filename'),
            self.archive_path.text(),'SQLite (*.sqlite *.db)',options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            if Path(path).suffix.lower() not in ('.sqlite','.db'):path+='.sqlite'
            self.archive_path.setText(path)

    def offline_source_changed(self,*args):
        offline=self.use_offline.isChecked()
        self.source_label.setText(self.tr('Export-Datenquelle: Offline-Datenbank','Export source: offline database') if offline else self.tr('Export-Datenquelle: Garmin Connect (online)','Export source: Garmin Connect (online)'))
        if offline and self.archive_path.text().strip():
            try:
                with Archive(self.archive_path.text(),readonly=True) as db:
                    keys=activity_type_keys(db.snapshot('activity_types'))
                    account=db.account
                if hasattr(self,'data_page'):self.activate_date_account(account)
                if keys:self.sport_keys=keys;self.sports_loaded=True;self.populate_sports();self.update_sports_hint()
            except Exception:pass
        elif not offline and self.identity and self.date_account!=self.identity:
            self.activate_date_account(self.identity)

    def retrieve_skipped(self):
        from repairs import repair_plan,RepairSync
        if not self.api:
            self.message('Zum Nachladen bitte anmelden.','Please sign in to retrieve data.');return
        try:
            p=replace(self.make_plan(for_sync=True),skip_empty=False,refresh_days=0,account=self.identity,expert_method='')
            tasks=repair_plan(p.archive_path,self.identity,3,None,p)
            if not tasks:
                self.message('Keine übersprungenen Abfragen im gewählten Zeitraum.','No skipped requests in the selected date range.');return
            self.run_worker(lambda w:RepairSync(self.api,p,w.cancel,lambda *a:w.event.emit(a)).run_tasks(tasks),self.archive_sync_done)
        except Exception as exc:
            text=self.error_description(diagnostic(exc));self.message(text,text)

    def start_selected_archive_sync(self):self.start_archive_sync(selected_only=True)

    def start_archive_sync(self,checked=False,selected_only=False):
        if not self.api:
            self.message('Für das erstmalige Sichern oder Ergänzen bitte bei Garmin anmelden. Offline-Exporte benötigen keine Anmeldung.',
                         'Sign in to Garmin to download or update data. Offline exports do not require sign-in.');return
        self.ensure_archive_path()
        if not self.archive_path.text().strip():
            self.message('Bitte eine Datenbankdatei angeben.','Please select a database file.');return
        try:
            base=self.make_plan(for_sync=True)
            plan=replace(base,source='online',account=self.identity,datasets=base.datasets if selected_only else [d.key for d in DATASETS if d.full_sync],
                health_detail=base.health_detail if selected_only else 'full',
                activity_type='',selectors=[],formats=['json'],expert_method='',expert_kwargs={},
                activity_details=list(ACTIVITY_DETAILS) if self.archive_details.isChecked() else [],
                activity_files=(['ORIGINAL'] if self.archive_original.isChecked() else [])+[k for k,cb in self.archive_format_checks.items() if cb.isChecked()],delay=max(MIN_SYNC_INTERVAL,base.delay),retries=max(1,base.retries))
            if 'activities' not in plan.datasets:plan=replace(plan,activity_details=[],activity_files=[])
            plan.validate()
            if Path(plan.archive_path).exists():
                with Archive(plan.archive_path,plan.account,readonly=True):pass
        except Exception as exc:
            text=self.error_description(diagnostic(exc));self.message(text,text);return
        self.completed=0;self.log.clear()
        self.run_worker(lambda w:ArchiveSync(self.api,plan,w.cancel,lambda *args:w.event.emit(args)).run_sync(),self.archive_sync_done)

    def archive_sync_done(self,result):
        report=result['report']
        if report.get('fatal_diagnostic'):self.register_rate_limit(report['fatal_diagnostic'])
        self.archive_path.setText(result['path'])
        self.render_coverage({'path':result['path'],'coverage':result['coverage'],'start':result['start'],'end':result['end']})
        s=report['stats']
        text=self.tr(f"Offline-Datenbank: {s['downloaded']} neue Antworten, {s['cached']} wiederverwendet, {s['errors']} Fehler.",
                     f"Offline database: {s['downloaded']} new responses, {s['cached']} reused, {s['errors']} errors.")
        if s.get('not_requested'):text+=self.tr(f" Ungeprüft übersprungen: {s['not_requested']}.",f" Skipped, unverified: {s['not_requested']}.")
        if s.get('skipped'):text+=self.tr(f" {s['skipped']} Tage nach abgelehnter Abfrageart übersprungen (als fehlend vermerkt).",f" {s['skipped']} days skipped after a rejected query type (recorded as missing).")
        if report['cancelled']:text+='\n'+self.tr('Abgebrochen. Bereits gespeicherte Daten bleiben erhalten.','Stopped. Responses already saved remain available.')
        elif not report['completed']:text+='\n'+self.tr('Der Bestand ist unvollständig. Übersicht und Fehlermeldungen prüfen.','The archive is incomplete. Check coverage and errors.')
        if report.get('fatal_diagnostic'):text+='\n'+self.error_description(report['fatal_diagnostic'])
        self.log.appendPlainText(text);self.message(text,text)

    def inspect_archive(self):
        path=self.archive_path.text().strip()
        start=self.start_date.date().toString('yyyy-MM-dd');end=self.end_date.date().toString('yyyy-MM-dd')
        def read(worker):
            with Archive(path,readonly=True) as db:return {'path':str(db.path),'coverage':db.coverage(start,end),'start':start,'end':end}
        self.run_worker(read,self.render_coverage,network=False)

    def render_coverage(self,result):
        self.last_coverage_result=result
        start=result.get('start',self.start_date.date().toString('yyyy-MM-dd'));end=result.get('end',self.end_date.date().toString('yyyy-MM-dd'))
        self.archive_summary.setText(self.tr('Gespeicherter Bestand im gewählten Zeitraum: ','Stored data in the selected date range: ')+result['path']+' · '+start+' – '+end+'\n'+self.tr('Gespeicherte Tage sind keine Garantie für vollständige Messreihen. Fehlende Tage werden nicht online ergänzt.','Stored days do not guarantee complete measurement timelines. Missing days are not fetched online.'))
        actual={r['dataset']:r for r in result['coverage']}
        keys=[d.key for d in DATASETS]+[k for k in actual if k not in {d.key for d in DATASETS}]
        headers=self.tr(['Datenart','Tage gespeichert','Ungeprüft übersprungen (Tage)','Tage fehlen','Antworten','Leere Antworten','Offene Fehler','Erster Tag','Letzter Tag','Letzter Abruf (UTC)'],
                        ['Dataset','Days stored','Unverified skipped days','Days missing','Responses','Empty responses','Unresolved errors','First day','Last day','Last fetch (UTC)'])
        self.coverage_table.clear();self.coverage_table.setColumnCount(len(headers));self.coverage_table.setHorizontalHeaderLabels(headers);self.coverage_table.setRowCount(len(keys))
        definitions={d.key:d for d in all_definitions()};n=(date.fromisoformat(end)-date.fromisoformat(start)).days+1
        for i,key in enumerate(keys):
            r=actual.get(key,{});d=definitions.get(key)
            title=(d.de if self.lang=='de' else d.en) if d else {'activity_types':self.tr('Sportartenkatalog','Sport taxonomy'),'activity_extras':self.tr('Aktivitätsdetails / Originaldateien','Activity details / original files')}.get(key,key)
            missing=max(0,n-r.get('stored_days',0)) if d and d.kind!='account' else '—'
            values=[title,r.get('stored_days',0),r.get('unverified_days',0),missing,r.get('responses',0),r.get('empty_responses',0),r.get('failed_requests',0),r.get('first_day',''),r.get('last_day',''),r.get('fetched_utc','')]
            for j,value in enumerate(values):self.coverage_table.setItem(i,j,QTableWidgetItem(str(value)))
        self.coverage_table.resizeColumnsToContents();self.coverage_table.setColumnWidth(0,280)

    def method_changed(self, name):
        method=getattr(Garmin,name,None)
        self.method_doc.setPlainText((name+str(inspect.signature(method))+'\n\n'+(inspect.getdoc(method) or '')) if name in read_methods(Garmin) else self.tr('Keine gültige Leseabfrage','Not a supported reader'))

    def toggle_language(self):
        self.lang='en' if self.lang=='de' else 'de'
        for obj,de,en,setter in self.bindings: getattr(obj,setter)(self.tr(de,en))
        for idx,de,en in self.config_tabs: self.tabs.setTabText(idx,self.tr(de,en))
        for c in (self.layout_combo,self.delimiter,self.health_detail):
            for idx,(de,en,_) in enumerate(c.property('entries')): c.setItemText(idx,self.tr(de,en))
        self.update_health_labels()
        self.populate_sports()
        self.update_sports_hint()
        self.language.setText('English' if self.lang=='de' else 'Deutsch')
        self.help.setHtml(HELP[self.lang]); self.update_connection();self.update_cooldown_label();self.offline_source_changed()
        if hasattr(self,'last_coverage_result'):self.render_coverage(self.last_coverage_result)
        self.data_page.retranslate()

    def update_connection(self):
        if self.api:
            self.connection.setText(('DEMO · ' if self.demo else '')+self.tr('Angemeldet · ','Signed in · ')+self.signed_email)
        else: self.connection.setText(self.tr('Nicht angemeldet','Not signed in'))

    @property
    def signed_email(self): return getattr(self,'_signed_email','demo@example.invalid')

    def quick_dates(self,n): self.end_date.setDate(QDate.currentDate()); self.start_date.setDate(QDate.currentDate().addDays(1-n))
    def activity_list_preset(self):
        self.select_all(False); self.dataset_checks['activities'].setChecked(True)
        for key,cb in self.format_checks.items():cb.setChecked(key=='csv')
        for cb in [*self.detail_checks.values(),*self.file_checks.values()]:cb.setChecked(False)
        self.activity_summary.setChecked(True); self.split.setChecked(False)
        self.selectors.clear(); self.expert_enable.setChecked(False)

    def select_all(self,value):
        for cb in self.dataset_checks.values(): cb.setChecked(value)
    def choose_folder(self):
        path=QFileDialog.getExistingDirectory(self,self.tr('Zielordner','Output folder'),self.output.text())
        if path: self.output.setText(path)
    def message(self,de,en): QMessageBox.information(self,'TTN Garmin Export',self.tr(de,en))

    def set_busy(self,busy):
        for page in (self.account_widget,self.export_widget,self.advanced_widget,self.fields_widget,self.expert_widget,self.offline_widget,self.data_page): page.setEnabled(not busy)
        self.export_preview_button.setEnabled(not busy)
        self.start_button.setEnabled(not busy); self.stop_button.setEnabled(busy or bool(self.data_page.track_worker and self.data_page.track_worker.isRunning()))
        self.status.setText(self.tr('Bitte warten …','Working …') if busy else self.tr('Bereit','Ready'))

    def run_worker(self,operation,done,network=True):
        if self.worker and self.worker.isRunning(): return
        if network and self.rate_pause.remaining():
            self.update_cooldown_label()
            self.message('Die lokale Abrufpause ist noch aktiv. Bitte warten; es wurde keine Anfrage gesendet.','The local request pause is still active. Please wait; no request was sent.');return
        self.set_busy(True); self.progress.setRange(0,0)
        self.eta_snapshot=None;self.eta_active=False;self.retry_until=0.;self.request_count=0;self.local_transfer=False;self.eta_label.clear()
        self.worker=Worker(operation)
        self.worker.event.connect(self.on_event)
        self.worker.mfa.connect(self.mfa_dialog)
        self.worker.result.connect(done)
        self.worker.failed.connect(self.worker_failed)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def worker_finished(self):
        self._restoring=False
        self.set_busy(False)
        if self.eta_active:self.end_eta('error')
        if self.progress.maximum()==0: self.progress.setRange(0,1); self.progress.setValue(0)

    def update_cooldown_label(self):
        remaining=self.rate_pause.remaining()
        self.cooldown_label.setText(self.tr(
            f'Garmin-Anfragen pausiert: noch etwa {(remaining+59)//60} Minuten. Danach erfolgt kein automatischer Neustart.',
            f'Garmin requests paused: about {(remaining+59)//60} minutes remaining. No automatic restart afterwards.') if remaining else '')
        self.cooldown_label.setVisible(bool(remaining))

    def end_eta(self,state):
        self.eta_active=False;self.retry_until=0
        text={'complete':self.tr('Download beendet','Download finished'),'cancelled':self.tr('Download abgebrochen','Download stopped'),'error':self.tr('Download mit Fehlern beendet','Download ended with errors')}.get(state,state)
        if self.local_transfer:
            text=self.tr('Lokaler Export beendet','Local export finished') if state=='complete' else self.tr('Lokaler Export beendet / abgebrochen','Local export ended / stopped')
        elif self.rate_pause.remaining():text=self.tr('Download gestoppt · Abrufpause aktiv','Download stopped · request pause active')
        self.eta_label.setText(text+self.tr(f' · {self.request_count} Garmin-Abfragen',f' · {self.request_count} Garmin requests'))

    def update_eta(self):
        if not self.eta_active:return
        if self.retry_until>time.monotonic():
            self.eta_label.setText(self.tr(f'Wiederholung in {math.ceil(self.retry_until-time.monotonic())} s · Restzeit wird aktualisiert',f'Retry in {math.ceil(self.retry_until-time.monotonic())} s · updating estimate'));return
        snapshot=self.eta_snapshot or {};seconds=snapshot.get('seconds')
        if seconds is None:text=self.tr('Restzeit wird berechnet …','Calculating remaining time …')
        else:
            # Do not count down to zero during a request longer than its estimate.
            remaining=max(1,seconds-(time.monotonic()-self.eta_received)) if snapshot.get('pending') else 0
            if remaining<60:duration=self.tr('unter 1 Minute','under 1 minute')
            else:
                minutes=math.ceil(remaining/60);hours,minutes=divmod(minutes,60)
                duration=(f'{hours} h {minutes} min' if hours else f'{minutes} min')
            text=self.tr('Geschätzte Restzeit: ','Estimated time remaining: ')+duration
        if snapshot.get('provisional'):text+=self.tr(' · vorläufig, Aktivitätsumfang noch offen',' · provisional, activity workload still unknown')
        self.eta_label.setText(text+self.tr(f' · {self.request_count} Garmin-Abfragen',f' · {self.request_count} Garmin requests'))

    def error_description(self,detail):
        reasons={
            'rate_limit':('Garmin begrenzt die Anfragen. Vorerst nicht erneut anmelden oder abrufen.','Garmin is limiting requests. Do not sign in or fetch again for now.'),
            'challenge':('Garmin verlangt möglicherweise eine Browser-/Sicherheitsprüfung. Keine automatischen Wiederholungen.','Garmin may require a browser/security check. No automatic retries.'),
            'authentication':('Die Anmeldung oder Sitzung wurde abgelehnt.','The sign-in or session was rejected.'),
            'forbidden':('Garmin hat den Zugriff abgelehnt. Eine Anmeldung allein bestätigt nicht den Zugriff auf alle Daten.','Garmin denied access. Signing in does not confirm access to all data.'),
            'invalid_request':('Garmin hat die Abfrage abgelehnt. Einstellungen oder Schnittstelle müssen geprüft werden.','Garmin rejected the query. Settings or the API must be checked.'),
            'not_found':('Die angefragte Ressource wurde nicht gefunden.','The requested resource was not found.'),
            'server':('Garmin meldet einen Serverfehler.','Garmin returned a server error.'),
            'timeout':('Zeitüberschreitung bei der Verbindung.','The connection timed out.'),
            'tls':('Die verschlüsselte Verbindung konnte nicht geprüft werden.','The secure connection could not be verified.'),
            'connection':('Verbindungsfehler; ohne HTTP-Status ist die genaue Ursache noch offen.','Connection failure; without an HTTP status the exact cause is still unknown.'),
            'unknown':('Die genaue Ursache ist noch nicht erkennbar.','The exact cause is not yet known.'),
        }
        local={
            'OfflineDataMissing':('Diese Daten fehlen im lokalen Bestand. Es wurde nichts online nachgeladen.','These data are missing from the local archive. Nothing was fetched online.'),
            'OfflineAccountMismatch':('Diese Datenbank gehört zu einem anderen Garmin-Konto. Eine andere Datei wählen.','This database belongs to another Garmin account. Choose another file.'),
            'OfflineArchiveError':('Keine gültige oder unterstützte Offline-Datenbank. Datei und Pfad prüfen.','Invalid or unsupported offline archive. Check the file and path.'),
        }
        de,en=local.get(detail.get('error_type'),reasons.get(detail.get('reason'),reasons['unknown']))
        text=detail.get('error_type','Error')
        if detail.get('http_status'):text+=f" · HTTP {detail['http_status']}"
        return text+'\n'+self.tr(de,en)

    def register_rate_limit(self,detail):
        if detail.get('reason')=='rate_limit' or detail.get('http_status')==429:
            self.rate_pause.record(detail);self.update_cooldown_label()
            if hasattr(self,'eta_label') and not self.local_transfer:self.end_eta('error')

    def worker_failed(self,detail):
        if isinstance(detail,str):detail={'error_type':detail,'reason':'unknown'}
        if self._restoring:
            self._restoring=False;self.restore_failed(detail);return
        self.register_rate_limit(detail)
        if self.eta_snapshot is not None or self.local_transfer:self.end_eta('error')
        text=self.error_description(detail)
        self.log.appendPlainText(text)
        self.message(text,text)

    def mfa_dialog(self):
        code,ok=QInputDialog.getText(self,self.tr('Zwei-Faktor-Anmeldung','Two-factor authentication'),self.tr('Garmin-Bestätigungscode:','Garmin verification code:'))
        self.worker.answers.put(code.strip() if ok else None)

    def login(self,restore=False):
        if self.worker and self.worker.isRunning():return
        if self.demo:
            self.message('Demo-Modus: Für echte Konten ohne --demo starten.','Demo mode: restart without --demo for real accounts.'); return
        email=self.email.text().strip(); password='' if restore else self.password.text(); remember=self.remember.isChecked(); china=self.china.isChecked()
        if not email:
            self.message('Bitte E-Mail-Adresse eingeben.','Please enter your email.'); return
        ident=account_key(email,china); session=self.data_dir/'sessions'/ident
        known_first=self.date_values(ident).get('first_activity','')
        detect_first=self.detect_first.isChecked() and not known_first
        self.password.clear();self._restoring=restore
        def op(worker):
            api=Garmin(email=email,password=password or None,is_cn=china,prompt_mfa=worker.ask_mfa,retry_attempts=0)
            persistence={'remember':remember,'china':china,'saved':False,'error':{}}
            if remember:
                api.login(str(session))
                try:
                    # The library suppresses dump errors during login. Verify the
                    # explicit save so the UI cannot silently promise persistence.
                    api.client.dump(str(session));persistence['saved']=True
                except Exception as exc:persistence['error']=diagnostic(exc)
            else:
                # Explicit temporary path avoids the library's GARMINTOKENS fallback.
                with tempfile.TemporaryDirectory(prefix='garmin-session-') as temp_session:
                    try:
                        api.login(temp_session)
                    finally:
                        api.client._tokenstore_path = None
            optional_error={}
            try:
                sport_keys=activity_type_keys(api.get_activity_types())
            except Exception as exc:
                sport_keys=[]  # An optional sport-list failure must not block a valid login.
                optional_error=diagnostic(exc)
            first=known_first;first_error={}
            if detect_first and not optional_error and callable(getattr(api,'count_activities',None)):
                try:
                    cancel=getattr(worker,'cancel',None)
                    if cancel and cancel.wait(2):raise RuntimeError('Cancelled')
                    count=api.count_activities()
                    if isinstance(count,int) and count>0:
                        if cancel and cancel.wait(2):raise RuntimeError('Cancelled')
                        items=api.get_activities(start=count-1,limit=1)
                        if isinstance(items,list) and items:
                            first=(items[0].get('startTimeLocal') or '')[:10]
                except Exception as exc:first_error=diagnostic(exc)
            return api,ident,email,sport_keys,first,first_error or optional_error,persistence
        self.run_worker(op,self.login_done)

    def login_done(self,result):
        self._restoring=False
        self.api,self.identity,self._signed_email=result[:3]
        self.activate_date_account(self.identity)
        if len(result)>6:
            persistence=result[6]
            if persistence['remember'] and persistence['saved']:
                try:atomic_json(self.saved_account_path,{'schema':1,'email':result[2],'china':persistence['china']})
                except OSError:persistence['saved']=False
            if persistence['remember'] and not persistence['saved']:
                text=self.tr('Anmeldung erfolgreich, aber die Sitzung konnte nicht für den nächsten Start gespeichert werden. Schreibrechte im angezeigten Profilordner prüfen.','Signed in, but the session could not be saved for the next start. Check write permissions in the displayed profile folder.')
                self.log.appendPlainText(text);self.message(text,text)
            elif persistence['remember']:
                self.log.appendPlainText(self.tr('Sitzung für automatische Anmeldung gespeichert.','Session saved for automatic sign-in.'))
        self.sport_keys=list(FALLBACK_SPORTS); self.sports_loaded=False
        if len(result)>3 and result[3]:
            self.sport_keys=result[3]; self.sports_loaded=True
        self.populate_sports(); self.update_sports_hint()
        self.demo=False
        self.ensure_archive_path()
        self.update_connection(); self.tabs.setCurrentIndex(1)
        if len(result)>4 and result[4]:
            first=QDate.fromString(result[4],'yyyy-MM-dd')
            if first.isValid() and QDate(1990,1,1)<=first<=QDate.currentDate():
                self.first_activity_date=result[4];self.reset_start_button.setEnabled(True)
                self.save_date_values(self.identity,first_activity=result[4])
                if not self.date_user_set:
                    self.start_date.blockSignals(True);self.start_date.setDate(first);self.start_date.blockSignals(False)
                if self.data_page.date_account in (None,self.identity):self.data_page.restore_date_filter(self.identity)
            self.log.appendPlainText(self.tr('Älteste verfügbare Aktivität: ','Oldest available activity: ')+result[4])
        if len(result)>5 and result[5]:
            self.register_rate_limit(result[5]);self.log.appendPlainText(self.tr('Optionale Kontoabfrage nicht verfügbar: ','Optional account lookup unavailable: ')+self.error_description(result[5]))
        if self.use_offline.isChecked():self.offline_source_changed()

    def forget(self):
        ident=self.identity or (account_key(self.email.text().strip(),self.china.isChecked()) if self.email.text().strip() else None)
        if not ident:
            self.message('E-Mail-Adresse des zu entfernenden Kontos eingeben.','Enter the email of the account whose session you want to remove.');return
        session=self.data_dir/'sessions'/ident
        try:
            if session.exists():shutil.rmtree(session)
            self.saved_account_path.unlink(missing_ok=True)
        except OSError:
            self.message('Sitzung konnte nicht vollständig entfernt werden. Schreibrechte prüfen.','Could not fully remove the session. Check write permissions.');return
        self.remember.setChecked(False);self.password.clear()
        self.api=None; self.identity=None; self.update_connection()
        self.message('Lokale Sitzung entfernt.','Local session removed.')

    def make_plan(self,for_sync=False):
        offline=self.use_offline.isChecked() and not for_sync
        identity=self.identity or account_key('unsigned')
        if offline:
            with Archive(self.archive_path.text(),readonly=True) as db:identity=db.account
        return Plan(start=self.start_date.date().toString('yyyy-MM-dd'),end=self.end_date.date().toString('yyyy-MM-dd'),
            datasets=[k for k,v in self.dataset_checks.items() if v.isChecked()],output=self.output.text(),account=identity,
            formats=[k for k,v in self.format_checks.items() if v.isChecked()],layout=self.layout_combo.currentData(),delimiter=self.delimiter.currentData(),
            split_year=self.split.isChecked(),refresh_days=self.refresh.value(),delay=self.delay.value(),retries=self.retries.value(),activity_type=self.sport_value(),
            activity_details=[k for k,v in self.detail_checks.items() if v.isChecked()],activity_files=[k for k,v in self.file_checks.items() if v.isChecked()],
            selectors=[x.strip() for x in self.selectors.toPlainText().splitlines() if x.strip()],
            expert_method=self.method.currentText() if self.expert_enable.isChecked() and not for_sync else '',
            expert_kwargs=json.loads(self.kwargs.toPlainText()) if self.expert_enable.isChecked() and not for_sync else {},expert_daily=self.expert_daily.isChecked(),
            activity_summary=self.activity_summary.isChecked(),language=self.lang,
            preview_rows=self.preview_rows.value(),preview_requests=self.preview_requests.value(),
            source='offline' if offline else 'online',archive_path=self.archive_path.text().strip(),optimize=self.optimize.isChecked(),activity_batch_days=self.activity_batch_days.value(),
            health_detail=self.health_detail.currentData(),health_batch_days=self.health_batch_days.value(),workers=self.workers.value(),export_history=self.export_history.isChecked(),skip_empty=self.skip_empty.isChecked())

    def show_export_preview(self):
        if not self.api and not self.use_offline.isChecked():
            self.tabs.setCurrentIndex(0); self.message('Bitte zuerst anmelden.','Please sign in first.'); return
        try:
            if not self.use_offline.isChecked():self.ensure_archive_path()
            plan=self.make_plan(); plan.validate()
        except Exception as exc:
            if isinstance(exc,OfflineArchiveError):
                text=self.error_description(diagnostic(exc));self.message(text,text);return
            self.message('Bitte Zeitraum, Datenarten und Formate prüfen.','Check dates, datasets and formats.'); return
        self.run_worker(lambda w:(OfflinePreviewer(plan,w.cancel,lambda *args:w.event.emit(args)) if plan.source=='offline' else Previewer(self.api,plan,w.cancel,lambda *args:w.event.emit(args))).run_preview(),self.export_preview_done,network=plan.source!='offline')

    def export_preview_done(self,result):
        plan=result['plan'];dialog=QDialog(self)
        dialog.setWindowTitle(self.tr('Exportvorschau · Stichprobe','Export preview · Sample'))
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose,True)
        dialog.resize(1100,650);dialog.setWindowModality(Qt.WindowModality.WindowModal)
        layout=QVBoxLayout(dialog)
        info=QLabel(self.tr(
            f"{plan.start} bis {plan.end} · maximal {plan.preview_rows} Zeilen je Tabelle · {result['requests']}/{plan.preview_requests} Abfragen · "+', '.join(f.upper() for f in plan.formats),
            f"{plan.start} through {plan.end} · up to {plan.preview_rows} rows per table · {result['requests']}/{plan.preview_requests} queries · "+', '.join(f.upper() for f in plan.formats)))
        info.setWordWrap(True);layout.addWidget(info)
        notice=QLabel(self.tr(
            'Stichprobe: Aktivitäten aus höchstens drei Listenseiten; andere Tagesdaten aus höchstens den ersten drei Tagen. Es wurde kein Export gespeichert. Weitere Zeilen oder Spalten sind möglich.',
            'Sample: activities from up to three list pages; other daily data from up to the first three days. No export was saved. More rows or columns may exist.'))
        notice.setWordWrap(True);layout.addWidget(notice)
        if result['budget_reached'] or result['cancelled']:
            text=self.tr('Abfragelimit erreicht oder Vorschau abgebrochen – es wird nur die bisher geladene Stichprobe gezeigt.', 'Query limit reached or preview stopped – only samples retrieved so far are shown.')
            label=QLabel(text);label.setWordWrap(True);label.setStyleSheet('color:#926000');layout.addWidget(label)
        if result['notes']:
            messages={
                'daily_statistics_without_timelines':self.tr('Detailgrad: Tageskennzahlen. Die betroffenen Tabellen enthalten keine Messverläufe.','Detail level: daily statistics. Affected tables contain no measurement timelines.'),
                'stopped_after_connection_error':self.tr('Nach dem Verbindungsfehler gestoppt; keine weiteren Kategorien abgerufen.','Stopped after the connection failure; no further categories queried.'),
                'offline_sample':self.tr('Quelle: lokale Offline-Datenbank. Keine Garmin-Anfragen.','Source: local offline database. No Garmin requests.'),
                'expert_not_sampled':self.tr('Expertenabfragen werden in dieser Vorschau nicht ausgeführt.','Expert queries are not executed in this preview.'),
                'files_not_downloaded':self.tr('Originaldateien (z. B. GPX/FIT) werden für die Vorschau nicht heruntergeladen.','Original files (e.g. GPX/FIT) are not downloaded for preview.'),
                'columns_truncated':self.tr('Tabellenansicht auf 100 Spalten begrenzt.','Table display limited to 100 columns.'),
                'activity_taxonomy_unavailable_exact_match_only':self.tr('Sportartenhierarchie nicht verfügbar; genauer Typvergleich.','Sport hierarchy unavailable; matching exact types.'),
                'repeated_page':self.tr('Garmin hat eine Listenseite wiederholt; Stichprobe dort beendet.','Garmin repeated a list page; sampling stopped there.'),
                'activity_reader_unavailable':self.tr('Begrenzter Aktivitätenabruf nicht verfügbar.','Bounded activity retrieval unavailable.'),
            }
            for code in set(result['notes']):
                label=QLabel(messages.get(code,code));label.setWordWrap(True);layout.addWidget(label)
        for err in result['errors']:
            text=err['dataset']+': '+self.error_description(err)
            label=QLabel(text);label.setStyleSheet('color:#a23535');label.setWordWrap(True);layout.addWidget(label)
        tabs=QTabWidget();layout.addWidget(tabs,1)
        labels={d.key:(d.de if self.lang=='de' else d.en) for d in all_definitions()}
        for group in result['groups']:
            page=QWidget();v=QVBoxLayout(page)
            if not group['rows']:
                label=QLabel(self.tr('Keine passenden Tabellenzeilen in der Stichprobe. Der gesamte Zeitraum wurde nicht durchsucht.','No matching table rows in this sample. The full date range was not scanned.'));label.setWordWrap(True);v.addWidget(label)
            views=QTabWidget();v.addWidget(views,1)
            if set(plan.formats)&{'csv','xlsx'}:
                table=QTableWidget(len(group['rows']),len(group['fields']))
                table.setHorizontalHeaderLabels(group['fields'])
                table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
                table.setAlternatingRowColors(True)
                table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
                table.horizontalHeader().setDefaultSectionSize(180)
                for i,row in enumerate(group['rows']):
                    for j,field in enumerate(group['fields']):
                        value=row.get(field);text='' if value is None else str(value)
                        table.setItem(i,j,QTableWidgetItem(text if len(text)<=4000 else text[:4000]+'…'))
                views.addTab(table,self.tr('Tabelle','Table'))
            if 'json' in plan.formats:
                raw=QPlainTextEdit();raw.setReadOnly(True);raw.setPlainText(json.dumps(group['raw'],ensure_ascii=False,indent=2));views.addTab(raw,self.tr('JSON-Ausschnitt','JSON sample'))
            tabs.addTab(page,labels.get(group['key'],group['key']))
        if not result['groups']:
            empty=QLabel(self.tr('Keine Stichprobe geladen. Einstellungen und Hinweise oben prüfen.','No sample loaded. Check settings and the notes above.'));empty.setWordWrap(True);layout.addWidget(empty)
        close=QPushButton(self.tr('Schließen','Close'));close.clicked.connect(dialog.close);layout.addWidget(close)
        self.export_preview_dialog=dialog;dialog.show()

    def start_export(self):
        if not self.api and not self.use_offline.isChecked():
            self.tabs.setCurrentIndex(0); self.message('Bitte zuerst anmelden.','Please sign in first.'); return
        try:
            if not self.use_offline.isChecked():self.ensure_archive_path()
            plan=self.make_plan(); plan.validate()
            if not self.output.text().strip(): raise ValueError()
            if plan.expert_method and plan.source=='offline':
                self.message('Expertenabfragen sind für Offline-Exporte derzeit nicht verfügbar. Bitte die normalen Datenarten verwenden.','Expert queries are not available for offline exports yet. Use the standard datasets.');return
            if plan.expert_method:
                if plan.expert_method not in read_methods(Garmin): raise ValueError()
                inspect.signature(getattr(self.api,plan.expert_method)).bind(**substitute(plan.expert_kwargs,plan.start,plan.start,plan.end))
        except Exception as exc:
            if isinstance(exc,OfflineArchiveError):
                text=self.error_description(diagnostic(exc));self.message(text,text);return
            self.message('Bitte Zeitraum, Kategorien, Formate, Aktivitätsauswahl und Expertenparameter prüfen. Start muss vor Ende liegen; Ende darf nicht in der Zukunft liegen.','Check dates, categories, formats, activity options and expert parameters. Start must not follow end; end must not be in the future.'); return
        self.completed=0; self.log.clear()
        self.run_worker(lambda w:(OfflineExporter(plan,w.cancel,lambda *args:w.event.emit(args)) if plan.source=='offline' else ArchiveExporter(self.api,plan,w.cancel,lambda *args:w.event.emit(args))).run(),self.export_done,network=plan.source!='offline')

    def export_done(self,result):
        self.last_output,report=result; self.open_button.setEnabled(True)
        if report.get('fatal_diagnostic'):
            self.register_rate_limit(report['fatal_diagnostic'])
            self.log.appendPlainText(self.error_description(report['fatal_diagnostic']))
        s=report['stats']
        if 'activities' in report.get('config',{}).get('datasets',[]):
            self.log.appendPlainText(self.tr('Aktivitäten in der Liste: ','Activities in list: ')+str(s.get('activities',0)))
        text=self.tr(f"Neu: {s['downloaded']} · Cache: {s['cached']} · Leere Antworten: {s['empty']} · Abruffehler: {s['errors']}",f"Downloaded: {s['downloaded']} · Cached: {s['cached']} · Empty responses: {s['empty']} · Retrieval errors: {s['errors']}")
        if s.get('not_requested'):text+=self.tr(f" · Ungeprüft übersprungen: {s['not_requested']}",f" · Skipped, unverified: {s['not_requested']}")
        self.log.appendPlainText(text+'\n'+self.last_output)
        if report.get('fatal_diagnostic'):
            text+='\n'+self.error_description(report['fatal_diagnostic'])
        elif report['fatal_error_type'] or report['export_error_type']:
            text+='\n'+self.tr('Lauf oder Ausgabe fehlgeschlagen: ','Run or output failed: ')+(report['fatal_error_type'] or report['export_error_type'])
        elif report['cancelled']: text+='\n'+self.tr('Abgebrochen. Geladene Daten wurden ausgegeben.','Stopped. Retrieved data was exported.')
        elif s.get('not_requested'):text+='\n'+self.tr('Export enthält ungeprüft übersprungene Abfragen. Bei Bedarf unter Offline-Daten nachladen.','Export contains unverified skipped requests. Retrieve them under Offline data if needed.')
        elif s['errors']: text+='\n'+self.tr('Mit Abruffehlern beendet. Bericht prüfen.','Finished with retrieval errors. Check report.')
        else: text+='\n'+self.tr('Export beendet. Datenumfang im Bericht prüfen.','Export finished. Check coverage in the report.')
        self.message(text,text)

    def on_event(self,args):
        kind,*values=args
        if kind=='rate_limit':self.register_rate_limit(values[0])
        elif kind=='eta':
            if self.worker and self.worker.cancel.is_set():return
            self.eta_snapshot=values[0];self.eta_received=time.monotonic();self.eta_active=True;self.update_eta()
        elif kind=='eta_end':self.end_eta(values[0])
        elif kind=='request':self.request_count=values[0];self.update_eta()
        elif kind=='retry_wait':self.retry_until=time.monotonic()+values[0] if values[0] else 0;self.update_eta()
        elif kind=='phase':
            if values[0]=='planning':self.eta_label.setText(self.tr('Download wird geplant · vorhandene Daten werden geprüft …','Planning download · checking existing data …'))
            elif values[0]=='offline':
                self.local_transfer=True;self.eta_label.setText(self.tr('Lokaler Export · keine Garmin-Abfragen','Local export · no Garmin requests'))
        elif kind=='plan':
            info=values[0]
            self.log.appendPlainText(self.tr(f"Geplant: {info['requests']} offene Abrufe, {info['stored']} gespeicherte Jobs; Aktivitätszusätze folgen.",f"Planned: {info['requests']} open requests, {info['stored']} stored jobs; activity extras follow."))
        elif kind=='batch':self.log.appendPlainText(self.tr('Zeitraumabruf','Range request')+f': {values[0]} · {values[1]} … {values[2]}')
        elif kind=='batch_fallback':self.log.appendPlainText(self.tr('Zeitraum nicht vollständig nutzbar; tagweise fortgesetzt: ','Range not usable; continuing daily: ')+values[0])
        elif kind=='total': self.progress.setRange(0,max(1,values[0])); self.progress.setValue(0)
        elif kind=='total_extra':self.progress.setMaximum(self.progress.maximum()+values[0])
        elif kind=='progress': self.completed+=1; self.progress.setValue(self.completed)
        elif kind=='writing':
            self.eta_active=False;self.eta_label.setText(self.tr('Abruf beendet · Exportdateien werden geschrieben …','Retrieval ended · writing export files …'))
            self.status.setText(self.tr('Dateien werden geschrieben …','Writing files …')); self.stop_button.setEnabled(False)
        elif kind=='warning':
            self.log.appendPlainText(self.tr('Hinweis: Sportartenhierarchie nicht verfügbar; es wird nur die genaue Sportart berücksichtigt.', 'Warning: sport hierarchy unavailable; matching the exact sport type only.'))
        elif kind in ('item','error'):
            key,day,status=values
            status={'cached':self.tr('Cache','Cached'),'downloaded':self.tr('Geladen','Downloaded'),'not_requested':self.tr('Ungeprüft übersprungen (Regel)','Skipped, unverified (rule)'),'offline':self.tr('Offline gespeichert','Stored offline')}.get(status,status)
            self.log.appendPlainText(f'{day} · {key} · {status}')
        elif kind=='import':
            self.status.setText(self.tr(f'Import: {values[0]} neu · {values[1]} Duplikate · {values[2]} Fehler',f'Import: {values[0]} new · {values[1]} duplicates · {values[2]} errors'))

    def open_log(self):
        if self.log.path.exists():QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log.path)))
        else:self.message('Keine Logdatei vorhanden.','No log file available.')

    def export_log(self):
        path,_=QFileDialog.getSaveFileName(self,self.tr('Sitzungslog speichern','Save session log'),self.log.path.name,'Log (*.log);;Text (*.txt)')
        if path:
            try:
                if Path(path).resolve()!=self.log.path.resolve():shutil.copyfile(self.log.path,path)
            except OSError:self.message('Logdatei konnte nicht gespeichert werden.','Could not save log file.')

    def preview(self):
        if not self.api and not self.use_offline.isChecked(): self.message('Bitte zuerst anmelden.','Please sign in first.'); return
        try:chosen=next(iter(selected_datasets(self.make_plan())),None)
        except Exception as exc:
            text=self.error_description(diagnostic(exc));self.message(text,text);return
        if not chosen: self.message('Zuerst eine Kategorie auswählen.','Select a category first.'); return
        if chosen.binary: self.message('Diese Datenart wird als unveränderte Datei gespeichert und hat keine Tabellenfelder.','This dataset is stored as an unmodified file and has no table fields.'); return
        day=self.start_date.date().toString('yyyy-MM-dd')
        args=() if chosen.kind=='account' else (day,day) if chosen.kind=='range' else (day,)
        self.preview_activity_summary=(chosen.key=='activities' and self.activity_summary.isChecked())
        if self.use_offline.isChecked():
            try:plan=self.make_plan()
            except Exception as exc:
                text=self.error_description(diagnostic(exc));self.message(text,text);return
            def read_fields(worker):
                reader=OfflineExporter(plan,worker.cancel)
                try:
                    data=reader.retrieve(chosen.key,'snapshot' if chosen.kind=='account' else day,chosen.method,args,{'aggregation':'daily'} if chosen.key=='endurance' else {})
                    if reader.stats['errors']:raise OfflineDataMissing()
                    return data
                finally:reader.archive.close()
            self.run_worker(read_fields,self.preview_done,network=False)
        else:self.run_worker(lambda w:getattr(self.api,chosen.method)(*args),self.preview_done)

    def preview_done(self,payload):
        paths=({'/'+path for path in SOURCE_PATHS.values()} if getattr(self,'preview_activity_summary',False) else set(field_patterns(payload)))
        self.fields_list.clear()
        for path in sorted(paths):
            item=QListWidgetItem(path); item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Unchecked); self.fields_list.addItem(item)
        self.tabs.setCurrentIndex(3)

    def apply_fields(self):
        selected=[self.fields_list.item(i).text() for i in range(self.fields_list.count()) if self.fields_list.item(i).checkState()==Qt.CheckState.Checked]
        self.selectors.setPlainText('\n'.join(selected))

    def stop(self):
        if self.data_page.track_worker:
            self.data_page.track_worker.cancel.set();self.data_page.pending_tracks=[];self.data_page.table.clearSelection()
        if self.worker:
            self.worker.cancel.set(); self.status.setText(self.tr('Abbruch angefordert …','Stop requested …'))
            if self.eta_active:self.end_eta('cancelled')
    def open_results(self):
        if self.last_output: QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_output))

    def save_preset(self):
        try: config=vars(self.make_plan()).copy()
        except Exception: self.message('Ungültige Expertenparameter.','Invalid expert parameters.'); return
        for key in ('account','output'): config.pop(key,None)
        path,_=QFileDialog.getSaveFileName(self,self.tr('Vorlage speichern','Save preset'),'garmin-export-preset.json','JSON (*.json)')
        if path:
            try: Path(path).write_text(json.dumps({'version':1,'settings':config},ensure_ascii=False,indent=2),encoding='utf-8')
            except OSError: self.message('Vorlage konnte nicht gespeichert werden.','Could not save the preset.')

    def load_preset(self):
        path,_=QFileDialog.getOpenFileName(self,self.tr('Vorlage laden','Load preset'),'','JSON (*.json)')
        if not path:return
        try:
            data=json.loads(Path(path).read_text(encoding='utf-8'))
            if data['version']!=1: raise ValueError()
            settings=data['settings']; p=Plan(**{**settings,'account':self.identity or account_key('unsigned'),'output':self.output.text()}); p.validate()
            for date_edit,value in ((self.start_date,p.start),(self.end_date,p.end)):date_edit.setDate(QDate.fromString(value,'yyyy-MM-dd'))
            for checks,values in ((self.dataset_checks,p.datasets),(self.format_checks,p.formats),(self.detail_checks,p.activity_details),(self.file_checks,p.activity_files)):
                for key,cb in checks.items():cb.setChecked(key in values)
            self.layout_combo.setCurrentIndex(self.layout_combo.findData(p.layout)); self.delimiter.setCurrentIndex(self.delimiter.findData(p.delimiter))
            self.archive_path.setText(p.archive_path);self.use_offline.setChecked(p.source=='offline');self.activity_summary.setChecked(p.activity_summary); self.preview_rows.setValue(p.preview_rows); self.preview_requests.setValue(p.preview_requests); self.split.setChecked(p.split_year); self.delay.setValue(p.delay); self.refresh.setValue(p.refresh_days); self.retries.setValue(p.retries); self.set_sport(p.activity_type)
            self.skip_empty.setChecked(p.skip_empty)
            self.optimize.setChecked(p.optimize);self.activity_batch_days.setValue(p.activity_batch_days)
            self.health_detail.setCurrentIndex(self.health_detail.findData(p.health_detail));self.health_batch_days.setValue(p.health_batch_days)
            self.export_history.setChecked(p.export_history)
            if 'workers' in settings:self.workers.setValue(p.workers)  # Older presets keep the current value.
            self.selectors.setPlainText('\n'.join(p.selectors));self.expert_enable.setChecked(bool(p.expert_method));self.method.setCurrentText(p.expert_method or 'get_naps');self.kwargs.setPlainText(json.dumps(p.expert_kwargs,indent=2));self.expert_daily.setChecked(p.expert_daily)
        except Exception: self.message('Vorlage ungültig oder nicht lesbar.','Preset is invalid or unreadable.')

    def closeEvent(self,event):
        if self.data_page.track_worker and self.data_page.track_worker.isRunning():
            self.data_page.track_worker.cancel.set();self.data_page.pending_tracks=[];event.ignore()
            self.message('GPS-Lesen wird beendet. Danach bitte erneut schließen.','Stopping GPS read. Close again when finished.');return
        if self.worker and self.worker.isRunning():
            self.stop();event.ignore();self.message('Abbruch angefordert. Bitte warten, bis der laufende Vorgang beendet ist, dann schließen.','Stop requested. Please wait until the running operation finishes, then close.');return
        self._closing=True
        self.log.appendPlainText('TTN Garmin Export · session ended');event.accept()


def main():
    app=QApplication(sys.argv);app.setOrganizationName('TTNTools');app.setApplicationName('GarminExportStudio')
    if '--smoke-test' in sys.argv:
        from demo_data import DemoGarmin
        with tempfile.TemporaryDirectory(prefix='garmin-smoke-') as folder:
            plan=Plan('2020-01-01','2020-01-02',['summary','heart_rate','sleep','activities'],folder,account_key('smoke@example.invalid'),formats=['csv','json','xlsx'],delay=0)
            _,report=Exporter(DemoGarmin(),plan).run()
            if not report['completed'] or report['stats']['downloaded']!=8 or report['stats']['activities']!=60:
                sys.exit(1)
            archive_plan=replace(plan,archive_path=str(Path(folder)/'smoke.sqlite'),refresh_days=0)
            synced=ArchiveSync(DemoGarmin(),archive_plan).run_sync()
            if not synced['report']['completed']:sys.exit(3)
            _,offline_report=OfflineExporter(replace(archive_plan,source='offline')).run()
            if not offline_report['completed'] or offline_report['stats']['downloaded']!=0:sys.exit(4)
            if OfflinePreviewer(replace(archive_plan,source='offline')).run_preview()['requests']!=0:sys.exit(5)
            from datahub import import_files,load_archive,backup_archive,validate_archive
            gpx=Path(folder)/'demo.gpx'
            gpx.write_text('<gpx><trk><type>cycling</type><trkseg><trkpt lat="51.1" lon="8.1"><time>2020-01-01T10:00:00Z</time></trkpt><trkpt lat="51.11" lon="8.11"><time>2020-01-01T10:10:00Z</time></trkpt></trkseg></trk></gpx>',encoding='utf-8')
            local=Path(folder)/'import.sqlite'
            if import_files(local,[gpx],account_key('smoke-import'))['imported']!=1:sys.exit(6)
            data=load_archive(local);backup_archive(local,Path(folder)/'backup.sqlite')
            if validate_archive(Path(folder)/'backup.sqlite')['integrity']!='ok':sys.exit(7)
            window=MainWindow(demo=True); window.toggle_language()
            if window.start_button.text()!='Start export': sys.exit(2)
            window.data_page.loaded(data);window.data_page.table.selectAll()
            if len(window.data_page.map.routes)!=1:sys.exit(8)
            window.data_page.map.poster(str(Path(folder)/'poster.png'),'Synthetic smoke test','Test')
            window.close()
        sys.exit(0)
    window=MainWindow('--demo' in sys.argv);window.show();sys.exit(app.exec())

if __name__=='__main__':main()
