"""Bilingual desktop data browser, summaries, import, backup and sharing."""
import json
import threading
from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import Qt,QDate,QItemSelectionModel,QUrl,QTimer,QThread,Signal
from PySide6.QtGui import QDesktopServices,QColor
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QComboBox,
 QDateEdit,QDoubleSpinBox,QSpinBox,QTableWidget,QTableWidgetItem,QAbstractItemView,QHeaderView,
 QSplitter,QTabWidget,QFileDialog,QDialog,QInputDialog,QMenu,QCheckBox,QScrollArea)
from archive import Archive
from activity_table import table_row,headers,type_graph
from sports import sport_label
from datahub import load_archive,import_files,aggregate,daily_health,write_csv,write_kml,backup_archive,validate_archive,trim_track,HEALTH_COLUMNS
from mapview import TrackMap,COLORS

class TrackLoader(QThread):
    result=Signal(object)
    def __init__(self,path,activities,generation,parent):
        super().__init__(parent);self.path=path;self.activities=activities;self.generation=generation;self.cancel=threading.Event()
    def run(self):
        from datahub import load_tracks
        try:self.result.emit((self.generation,load_tracks(self.path,self.activities,self.cancel),''))
        except Exception as exc:self.result.emit((self.generation,{},type(exc).__name__))

class DataPage(QWidget):
    def __init__(self,host):
        super().__init__();self.host=host;self.data={'activities':[],'tracks':{},'health':[]};self.by_key={};self.visible=[];self.summary_rows=[]
        self.loading=False;self.filter_initialized=False;self.presets_path=host.data_dir/'data-view-presets.json'
        self.date_account=None;self.first_activity_date=None
        self.track_worker=None;self.track_generation=0;self.pending_tracks=[]
        self.persisted_columns=[]
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0)
        self.browser_splitter=QSplitter(Qt.Orientation.Horizontal);outer.addWidget(self.browser_splitter)
        self.left_panel=QWidget();layout=QVBoxLayout(self.left_panel)
        left_scroll=QScrollArea();left_scroll.setWidgetResizable(True);left_scroll.setWidget(self.left_panel)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setMinimumWidth(360);self.browser_splitter.addWidget(left_scroll)
        bar=QHBoxLayout()
        self.source=QLineEdit();self.source.setPlaceholderText('SQLite');bar.addWidget(self.source,1)
        bar.addWidget(host.button('Datei öffnen','Open file',self.choose_archive));bar.addWidget(host.button('Daten laden','Load data',self.reload))
        layout.addLayout(bar)
        self.source.editingFinished.connect(lambda:host.archive_path.setText(self.source.text()))
        action=QHBoxLayout()
        self.tools_button=host.bind(QPushButton(),'Werkzeuge','Tools');tools=QMenu(self.tools_button)
        for de,en,fn in [('Dateien importieren','Import files',self.import_dialog),('Sicherung erstellen','Create backup',self.backup_dialog),('Datenbank prüfen','Check database',self.check_database),('Fehlende Daten ergänzen','Fill missing data',self.repair_dialog)]:
            item=host.bind(tools.addAction(de),de,en);item.triggered.connect(fn)
        self.tools_button.setMenu(tools);action.addWidget(self.tools_button)
        self.cache_button=host.button('Exportcache übernehmen','Import export cache',self.import_cache_dialog)
        action.addWidget(self.cache_button,1);layout.addLayout(action)
        self.note=host.label('Lokale Daten öffnen. Kartenhintergründe werden nur bei eingeschalteter Onlinekarte geladen.','Open local data. Basemaps are downloaded only when online map is enabled.');layout.addWidget(self.note)
        self.views=QTabWidget();layout.addWidget(self.views,1)
        self.activities_tab=QWidget();v=QVBoxLayout(self.activities_tab)
        self.search=QLineEdit();self.search.setMinimumWidth(70)
        self.sport=QComboBox();self.sport.setMinimumWidth(110);self.sport.setMinimumContentsLength(12)
        self.sport.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        search_row=QHBoxLayout();search_row.addWidget(host.label('Suche','Search'));search_row.addWidget(self.search,2)
        search_row.addWidget(host.label('Sport','Sport'));search_row.addWidget(self.sport,1);v.addLayout(search_row)
        self.date_from=QDateEdit(QDate(1990,1,1));self.date_to=QDateEdit(QDate.currentDate())
        dates=QHBoxLayout()
        for label,d in ((host.label('Von','From'),self.date_from),(host.label('Bis','Through'),self.date_to)):
            d.setCalendarPopup(True);d.setDisplayFormat('yyyy-MM-dd');d.setDateRange(QDate(1990,1,1),QDate.currentDate())
            dates.addWidget(label);dates.addWidget(d,1)
        v.addLayout(dates)
        self.reset_date_button=host.button('Auf erste Aktivität zurücksetzen','Reset to first activity',self.reset_activity_start)
        self.reset_date_button.setEnabled(False);v.addWidget(self.reset_date_button)
        host.bind(self.reset_date_button,'Setzt den Anfang auf das gespeicherte erste Aktivitätsdatum dieses Kontos zurück. Wird nach dessen Ermittlung verfügbar.','Reset start to this account’s saved first activity date. Available after it has been detected.',setter='setToolTip')
        self.min_km=QDoubleSpinBox();self.min_km.setRange(0,100000);self.min_km.setSuffix(' km')
        extra=QHBoxLayout();extra.addWidget(host.label('Mindestdistanz','Minimum distance'));extra.addWidget(self.min_km)
        extra.addWidget(host.button('Spalten','Columns',self.column_menu))
        selection_button=host.bind(QPushButton(),'Auswahl','Selection');selection_menu=QMenu(selection_button)
        for de,en,fn in [('Alle gefilterten auswählen','Select all filtered',lambda:self.table.selectAll()),('Auswahl leeren','Clear selection',lambda:self.table.clearSelection()),('Auswahl exportieren','Export selection',self.export_selection),('Vergleichen','Compare',self.compare)]:
            item=host.bind(selection_menu.addAction(de),de,en);item.triggered.connect(fn)
        selection_button.setMenu(selection_menu);extra.addWidget(selection_button);v.addLayout(extra)
        presets=QHBoxLayout();self.preset=QComboBox();presets.addWidget(self.preset,1)
        presets.addWidget(host.button('Filter speichern','Save filter',self.save_preset));presets.addWidget(host.button('Filter laden','Load filter',self.load_preset));v.addLayout(presets)
        self.table=QTableWidget();self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.horizontalHeader().setSectionsMovable(True)
        self.table.itemSelectionChanged.connect(self.selection_changed);self.table.itemChanged.connect(self.checkbox_changed)
        self.table.setMinimumHeight(180);v.addWidget(self.table,1)
        self.map_panel=QWidget();mv=QVBoxLayout(self.map_panel);mv.setContentsMargins(8,8,8,8)
        controls=QHBoxLayout();self.heat=host.check('Heatmap','Heatmap');self.online=host.check('Onlinekarte (OSM)','Online map (OSM)')
        controls.addWidget(self.heat);controls.addWidget(self.online);controls.addWidget(host.button('Einpassen','Fit',self.selection_changed));mv.addLayout(controls)
        self.map=TrackMap(host.data_dir/'map-cache');mv.addWidget(self.map,1)
        self.map_hint=host.label('Klick: eine Tour · Strg: einzelne · Shift: Bereich. Mausrad: Zoom, Ziehen: verschieben.','Click: one trip · Ctrl: individual · Shift: range. Wheel: zoom, drag: pan.');mv.addWidget(self.map_hint)
        share=QHBoxLayout();self.privacy=QSpinBox();self.privacy.setRange(0,10000);self.privacy.setSingleStep(100);self.privacy.setSuffix(' m')
        share.addWidget(host.label('Start/Ende verbergen','Hide start/end'));share.addWidget(self.privacy)
        share.addWidget(host.button('Tourenbild','Tour image',self.poster));share.addWidget(host.button('KML teilen','Share KML',self.share_kml));mv.addLayout(share)
        external=QHBoxLayout();external.addWidget(host.button('Google Maps','Google Maps',self.google_map));external.addWidget(host.button('Google My Maps','Google My Maps',lambda:QDesktopServices.openUrl(QUrl('https://www.google.com/mymaps'))));external.addStretch();mv.addLayout(external)
        map_scroll=QScrollArea();map_scroll.setWidgetResizable(True);map_scroll.setWidget(self.map_panel)
        map_scroll.setMinimumWidth(300);self.browser_splitter.addWidget(map_scroll)
        self.browser_splitter.setChildrenCollapsible(False)
        self.browser_splitter.setStretchFactor(0,1);self.browser_splitter.setStretchFactor(1,1)
        self.browser_splitter.setSizes([660,760])
        self.selection_note=QLabel();self.selection_note.setWordWrap(True);v.addWidget(self.selection_note)
        self.views.addTab(self.activities_tab,'')
        self.health_table=QTableWidget();self.health_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        health=QWidget();hv=QVBoxLayout(health);hv.addWidget(host.label('Ein Tag pro Zeile. Leere Felder bedeuten: kein unterstützter Wert gespeichert. Keine medizinische Bewertung. Die Datumsfilter der Aktivitätsseite gelten auch hier.','One row per day. Blank means no supported stored value. No medical interpretation. Date filters from Activities also apply here.'))
        hv.addWidget(host.button('Tagesübersicht exportieren','Export daily summary',self.export_health));hv.addWidget(self.health_table,1);self.views.addTab(health,'')
        trends=QWidget();tv=QVBoxLayout(trends);tb=QHBoxLayout();self.period=QComboBox();self.period.addItems(['Monat','Jahr']);tb.addWidget(self.period)
        tb.addWidget(host.button('Auswertung exportieren','Export summary',self.export_summary));tb.addWidget(host.button('Wiederkehrende Strecken','Recurring routes',self.recurring));tb.addStretch();tv.addLayout(tb)
        self.trend_note=host.label('Auswertung der aktuell gefilterten Aktivitäten; fehlende Messwerte zählen nicht als gemessene Null.','Summary of currently filtered activities; missing readings do not represent measured zero.');tv.addWidget(self.trend_note)
        self.trends=QTableWidget();self.trends.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);tv.addWidget(self.trends,1);self.views.addTab(trends,'')
        self.search.textChanged.connect(self.apply_filters);self.sport.currentIndexChanged.connect(self.apply_filters);self.date_from.dateChanged.connect(self.apply_filters);self.date_to.dateChanged.connect(self.apply_filters);self.min_km.valueChanged.connect(self.apply_filters)
        self.date_from.dateChanged.connect(self.remember_date_filter)
        self.period.currentIndexChanged.connect(self.refresh_summaries);self.heat.toggled.connect(self.selection_changed);self.online.toggled.connect(self.map.set_online);self.privacy.valueChanged.connect(self.selection_changed)
        host.archive_path.textChanged.connect(self.source.setText)
        self.retranslate();self.read_presets()

    def t(self,de,en):return self.host.tr(de,en)
    def restore_date_filter(self,account,apply=True):
        if self.date_account!=account:
            self.filter_initialized=False
            self.date_from.blockSignals(True);self.date_from.setDate(QDate(1990,1,1));self.date_from.blockSignals(False)
        self.date_account=account;values=self.host.date_values(account)
        self.first_activity_date=values.get('first_activity')
        self.reset_date_button.setEnabled(bool(self.first_activity_date))
        chosen=values.get('data_from') or self.first_activity_date
        if chosen:
            self.date_from.blockSignals(True);self.date_from.setDate(QDate.fromString(chosen,'yyyy-MM-dd'));self.date_from.blockSignals(False)
            self.filter_initialized=True
        if apply:self.apply_filters()

    def remember_date_filter(self,*args):
        self.filter_initialized=True
        self.host.save_date_values(self.date_account,data_from=self.date_from.date().toString('yyyy-MM-dd'))

    def reset_activity_start(self):
        if not self.first_activity_date:return
        first=QDate.fromString(self.first_activity_date,'yyyy-MM-dd')
        if self.date_to.date()<first:self.date_to.setDate(first)
        self.date_from.setDate(first)
        self.host.save_date_values(self.date_account,data_from=self.first_activity_date)
    def error(self,exc):
        # Class only: import errors and paths must not leak server data or credentials.
        self.host.message(self.t('Vorgang fehlgeschlagen: ','Operation failed: ')+type(exc).__name__,self.t('Vorgang fehlgeschlagen: ','Operation failed: ')+type(exc).__name__)
    def path(self):
        path=self.source.text().strip() or self.host.archive_path.text().strip()
        if not path:raise ValueError('Choose archive')
        return path
    def choose_archive(self):
        path,_=QFileDialog.getOpenFileName(self,self.t('Offline-Datenbank öffnen','Open offline archive'),'','SQLite (*.sqlite *.db)')
        if path:self.host.archive_path.setText(path);self.reload()
    def reload(self):
        try:path=self.path()
        except Exception as exc:self.error(exc);return
        self.host.run_worker(lambda w:load_archive(path,w.cancel,with_tracks=False),self.loaded,network=False)
    def loaded(self,data):
        self.track_generation+=1
        if self.track_worker and self.track_worker.isRunning():self.track_worker.cancel.set()
        self.data=data;self.by_key={a['_key']:a for a in data['activities']}
        account=data.get('account') or self.host.identity
        if account:self.restore_date_filter(account,apply=False)
        current=self.sport.currentData();self.sport.blockSignals(True);self.sport.clear();self.sport.addItem(self.t('Alle Sportarten','All sports'),'')
        for key in sorted({(a.get('activityType') or {}).get('typeKey','other') for a in data['activities']}):self.sport.addItem(sport_label(key,self.host.lang)+' ('+key+')',key)
        self.sport.setCurrentIndex(max(0,self.sport.findData(current)));self.sport.blockSignals(False)
        if data['activities'] and not self.filter_initialized:
            days=sorted(a['_day'] for a in data['activities']);self.date_from.blockSignals(True);self.date_to.blockSignals(True)
            self.date_from.setDate(QDate.fromString(days[0],'yyyy-MM-dd'));self.date_to.setDate(QDate.fromString(days[-1],'yyyy-MM-dd'))
            self.date_from.blockSignals(False);self.date_to.blockSignals(False);self.filter_initialized=True
        self.note.setText(self.t(f"{len(data['activities'])} lokale Aktivitäten · {len(data['health'])} Tage mit Tageswerten · GPS wird bei Auswahl lokal gelesen",f"{len(data['activities'])} local activities · {len(data['health'])} days with daily values · GPS is read locally when selected"))
        status=data.get('activity_status',{})
        if not data['activities']:
            if not status.get('responses',0):
                detail=self.t('Keine Aktivitätsübersichten in dieser Datenbank gespeichert. Ein Haken auf Export startet keinen Download. Nur „Aktivitäten – Übersicht“ wählen und unter Offline-Daten die gewählten Daten sichern; vorhandene Exporte ggf. über den Exportcache übernehmen.',
                              'No activity summaries saved in this database. Selecting a checkbox on Export does not download data. Select only Activities – summary and sync selected data on Offline data; import an existing export cache if needed.')
            elif status.get('empty_responses',0)==status.get('responses'):
                detail=self.t('Aktivitätsabrufe sind gespeichert, enthalten aber nur leere Listen. Zeitraum, Konto und letzte Abruffehler unter Offline-Daten prüfen.',
                              'Activity requests are saved but contain only empty lists. Check dates, account and recent errors on Offline data.')
            else:
                detail=self.t('Aktivitätsantworten sind gespeichert, konnten aber nicht als Aktivitäten gelesen werden. Datenbank und Abrufbericht prüfen.',
                              'Activity responses are saved but could not be read as activities. Check the database and transfer report.')
            self.note.setText(self.note.text()+'\n'+detail)
        if status:
            self.note.setText(self.note.text()+self.t(f"\nAktivitätsantworten: {status['responses']} · davon leer: {status['empty_responses']} · offene Abruffehler: {status['failed_requests']}",
                                                      f"\nActivity responses: {status['responses']} · empty: {status['empty_responses']} · unresolved errors: {status['failed_requests']}"))
        self.apply_filters()

    def apply_filters(self,*args):
        start=self.date_from.date().toString('yyyy-MM-dd');end=self.date_to.date().toString('yyyy-MM-dd');sport=self.sport.currentData();text=self.search.text().strip().casefold()
        self.visible=[a for a in self.data['activities'] if start<=a['_day']<=end and (not sport or (a.get('activityType') or {}).get('typeKey')==sport) and (a.get('distance') or 0)>=self.min_km.value()*1000 and (not text or text in ' '.join(str(a.get(k) or '') for k in ('activityName','description','locationName')).casefold())]
        self.table.blockSignals(True);self.table.setSortingEnabled(False);self.table.clear();self.table.setColumnCount(1+len(headers(self.host.lang)));self.table.setHorizontalHeaderLabels(['✓']+headers(self.host.lang));self.table.setRowCount(len(self.visible))
        for row,a in enumerate(self.visible):
            check=QTableWidgetItem();check.setFlags(check.flags()|Qt.ItemFlag.ItemIsUserCheckable);check.setCheckState(Qt.CheckState.Unchecked);check.setData(Qt.ItemDataRole.UserRole,a['_key']);self.table.setItem(row,0,check)
            for col,value in enumerate(table_row(a,a['_day'],self.host.lang).values(),1):
                item=QTableWidgetItem();item.setData(Qt.ItemDataRole.DisplayRole,'' if value is None else value);self.table.setItem(row,col,item)
        self.table.setSortingEnabled(True);self.table.sortItems(1,Qt.SortOrder.DescendingOrder);self.table.resizeColumnsToContents();self.table.setColumnWidth(0,36);self.table.setColumnWidth(3,220)
        self.table.blockSignals(False);self.selection_changed();self.refresh_summaries()

    def selected(self):
        return [self.by_key[self.table.item(index.row(),0).data(Qt.ItemDataRole.UserRole)] for index in sorted(self.table.selectionModel().selectedRows(),key=lambda x:x.row())]
    def checkbox_changed(self,item):
        if item.column()!=0:return
        model=self.table.selectionModel();index=self.table.model().index(item.row(),0)
        flag=QItemSelectionModel.SelectionFlag.Select if item.checkState()==Qt.CheckState.Checked else QItemSelectionModel.SelectionFlag.Deselect
        model.select(index,flag|QItemSelectionModel.SelectionFlag.Rows)
    def selection_changed(self,*args):
        selected=self.selected();keys={a['_key'] for a in selected}
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item=self.table.item(row,0)
            if item:item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in keys else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        routes=[];missing=0
        for a in selected:
            points=trim_track(self.data['tracks'].get(a['_key'],[]),self.privacy.value())
            if any(p is not None for p in points):routes.append((a.get('activityName',''),points))
            else:missing+=1
        self.map.lang=self.host.lang;self.map.set_routes(routes,self.heat.isChecked())
        unknown=[a for a in selected if a['_key'] not in self.data['tracks']]
        self.pending_tracks=unknown
        self.selection_note.setText(self.t(f'{len(selected)} ausgewählt · {len(routes)} Strecken · {missing} ohne sichtbare GPS-Strecke',f'{len(selected)} selected · {len(routes)} tracks · {missing} without visible GPS track') if not unknown else self.t(f'{len(unknown)} GPS-Strecken werden lokal gelesen …',f'Reading {len(unknown)} GPS tracks locally …'))
        if unknown:self.start_track_loader()
        # Names remain visible in the table; colors identify tracks in multi-selection.
        for i,a in enumerate(selected):
            for row in self.table.selectionModel().selectedRows():
                if self.table.item(row.row(),0).data(Qt.ItemDataRole.UserRole)==a['_key']:
                    self.table.item(row.row(),3).setForeground(QColor(COLORS[i%len(COLORS)]));break

    def start_track_loader(self):
        if self.track_worker is not None:return
        if not self.pending_tracks:return
        try:path=self.path()
        except Exception:return
        self.track_worker=TrackLoader(path,list(self.pending_tracks),self.track_generation,self)
        self.track_worker.result.connect(self.tracks_loaded);self.track_worker.finished.connect(self.track_finished)
        self.host.stop_button.setEnabled(True);self.track_worker.start()
    def tracks_loaded(self,result):
        generation,tracks,error=result
        if generation!=self.track_generation:return
        self.data['tracks'].update(tracks)
        selected_keys={a['_key'] for a in self.selected()}
        if len(self.data['tracks'])>len(selected_keys)+200:
            keep=selected_keys|set(list(self.data['tracks'])[-200:])
            self.data['tracks']={k:v for k,v in self.data['tracks'].items() if k in keep}
        if error:
            if error=='Cancelled':return
            for a in self.track_worker.activities:self.data['tracks'].setdefault(a['_key'],[])
            self.host.log.appendPlainText(self.t('GPS-Lesen beendet: ','GPS read finished: ')+error)
        self.selection_changed()
    def track_finished(self):
        old=self.track_worker;self.track_worker=None
        if old:old.deleteLater()
        if not (self.host.worker and self.host.worker.isRunning()):self.host.stop_button.setEnabled(False)
        if self.pending_tracks:QTimer.singleShot(0,self.start_track_loader)

    def tracks_ready(self):
        if any(a['_key'] not in self.data['tracks'] for a in self.selected()):
            self.host.message('GPS wird noch gelesen. Bitte kurz warten.','GPS tracks are still being read. Please wait.');return False
        return True

    def fill_table(self,table,rows,columns):
        table.setSortingEnabled(False);table.clear();table.setColumnCount(len(columns));table.setHorizontalHeaderLabels([label for _,label in columns]);table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,(key,label) in enumerate(columns):
                item=QTableWidgetItem();item.setData(Qt.ItemDataRole.DisplayRole,row.get(key,'') if row.get(key) is not None else '');table.setItem(i,j,item)
        table.resizeColumnsToContents();table.setSortingEnabled(True)
    def refresh_summaries(self,*args):
        if not hasattr(self,'health_table'):return
        start=self.date_from.date().toString('yyyy-MM-dd');end=self.date_to.date().toString('yyyy-MM-dd')
        self.health_rows=[r for r in self.data['health'] if start<=r['date']<=end]
        self.fill_table(self.health_table,self.health_rows,[(k,de if self.host.lang=='de' else en) for k,de,en in HEALTH_COLUMNS])
        self.summary_rows=aggregate(self.visible,'year' if self.period.currentIndex()==1 else 'month')
        columns=[('period',self.t('Zeitraum','Period')),('sport',self.t('Sportart','Sport')),('count',self.t('Aktivitäten','Activities')),('km','km'),('hours',self.t('Stunden','Hours')),('ascent',self.t('Anstieg (m)','Ascent (m)'))]
        self.fill_table(self.trends,self.summary_rows,columns)

    def export_rows(self,rows,name):
        if not rows:self.host.message('Keine Daten für diese Auswahl.','No data for this selection.');return
        path,fmt=QFileDialog.getSaveFileName(self,self.t('Tabelle speichern','Save table'),name+'.csv','CSV (*.csv);;Excel (*.xlsx);;JSON (*.json)')
        if path:
            try:
                from datahub import write_table
                if Path(path).suffix.lower() not in ('.csv','.xlsx','.json'):path+=('.xlsx' if fmt.startswith('Excel') else '.json' if fmt.startswith('JSON') else '.csv')
                write_table(path,rows,self.host.lang);self.host.log.appendPlainText(self.t('Tabelle gespeichert.','Table saved.'))
            except Exception as exc:self.error(exc)
    def export_selection(self):self.export_rows([table_row(a,a['_day'],self.host.lang) for a in self.selected()],'activities')
    def export_health(self):self.export_rows([{(de if self.host.lang=='de' else en):r.get(k) for k,de,en in HEALTH_COLUMNS} for r in self.health_rows],'daily-summary')
    def export_summary(self):self.export_rows(self.summary_rows,'activity-summary')

    def compare(self):
        selected=self.selected()
        if len(selected)<2 or len(selected)>20:self.host.message('Bitte 2 bis 20 Aktivitäten auswählen.','Select 2 to 20 activities.');return
        dialog=QDialog(self);dialog.setWindowTitle(self.t('Aktivitäten vergleichen','Compare activities'));dialog.resize(950,650);v=QVBoxLayout(dialog);table=QTableWidget();v.addWidget(table)
        vals=[table_row(a,a['_day'],self.host.lang) for a in selected];table.setColumnCount(len(selected)+1);table.setHorizontalHeaderLabels([self.t('Kennzahl','Metric')]+[a.get('activityName','') for a in selected]);table.setRowCount(len(vals[0]))
        for row,key in enumerate(vals[0]):
            table.setItem(row,0,QTableWidgetItem(key))
            for col,values in enumerate(vals,1):table.setItem(row,col,QTableWidgetItem(str(values.get(key) if values.get(key) is not None else '')))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);table.resizeColumnsToContents();dialog.exec()

    def recurring(self):
        from datahub import similar_routes,load_tracks
        activities=list(self.visible)
        try:path=self.path()
        except Exception as exc:self.error(exc);return
        self.host.run_worker(lambda w:similar_routes(activities,load_tracks(path,activities,w.cancel),w.cancel),self.recurring_done,network=False)
    def recurring_done(self,rows):
        dialog=QDialog(self);dialog.setWindowTitle(self.t('Ähnliche Strecken','Similar routes'));dialog.resize(800,500);v=QVBoxLayout(dialog)
        v.addWidget(QLabel(self.t('Ungefähre Übereinstimmung auf Kartenrastern (ca. 250 m am Äquator, 85 % Überlappung). Kein Beweis identischer Strecken; kurze Touren werden nicht verglichen.','Approximate map-cell matching (~250 m at equator, 85% overlap). Not proof of identical routes; short trips are excluded.')))
        table=QTableWidget();v.addWidget(table);self.fill_table(table,rows,[('trips',self.t('Fahrten','Trips')),('example',self.t('Beispiel','Example')),('first',self.t('Erste','First')),('last',self.t('Letzte','Last'))]);dialog.exec()

    def poster(self):
        if not self.tracks_ready():return
        selected=self.selected()
        if not self.map.routes:self.host.message('Keine sichtbare GPS-Strecke ausgewählt.','No visible GPS track selected.');return
        path,_=QFileDialog.getSaveFileName(self,self.t('Tourenbild speichern','Save tour image'),'tour.png','PNG (*.png)')
        if path:
            try:
                totals=aggregate(selected);km=sum(r['km'] or 0 for r in totals);hours=sum(r['hours'] or 0 for r in totals)
                self.map.poster(path,self.t('Meine Aktivitäten','My activities'),f'{len(selected)} · {km:.1f} km · {hours:.1f} h')
            except Exception as exc:self.error(exc)
    def share_kml(self):
        if not self.tracks_ready():return
        if not self.map.routes:self.host.message('Keine sichtbare GPS-Strecke ausgewählt.','No visible GPS track selected.');return
        path,_=QFileDialog.getSaveFileName(self,self.t('KML für Google My Maps speichern','Save KML for Google My Maps'),'activities.kml','KML (*.kml)')
        if path:
            try:
                write_kml(path,self.selected(),self.data['tracks'],self.privacy.value())
                self.host.message('KML gespeichert. In Google My Maps eine Karte erstellen, die Datei importieren und dort den Freigabelink erzeugen. Die Heatmap wird nicht als Heatmap übertragen.','KML saved. Create a map in Google My Maps, import the file and share the map link there. Heatmap rendering is not transferred.')
            except Exception as exc:self.error(exc)
    def google_map(self):
        if not self.tracks_ready():return
        # Shows the exact selected tracks in a Google Maps JavaScript view, not a recomputed route.
        from google_view import show_google_map
        show_google_map(self,self.map.routes,self.host.lang,self.heat.isChecked())

    def import_dialog(self):
        files,_=QFileDialog.getOpenFileNames(self,self.t('Aktivitätsdateien oder Garmin-ZIP importieren','Import activity files or Garmin ZIP'),'','Activities (*.fit *.gpx *.tcx *.zip)')
        if not files:return
        path=self.source.text().strip()
        if not path:
            path,_=QFileDialog.getSaveFileName(self,self.t('Neue Offline-Datenbank','New offline archive'),'garmin-import.sqlite','SQLite (*.sqlite)')
            if not path:return
            self.host.archive_path.setText(path)
        try:
            if Path(path).exists():
                with Archive(path,readonly=True) as db:account=db.account
            else:
                from engine import account_key
                account=self.host.identity or account_key('local-import')
        except Exception as exc:self.error(exc);return
        self.host.run_worker(lambda w:import_files(path,files,account,w.cancel,lambda *a:w.event.emit(a)),self.import_done,network=False)
    def import_done(self,result):
        text=self.t(f"Importiert: {result['imported']} · Duplikate: {result['duplicates']} · Fehler/nicht unterstützte Dateien: {result['failed']}",f"Imported: {result['imported']} · Duplicates: {result['duplicates']} · Failed/unsupported files: {result['failed']}")
        if result.get('cancelled'):text+=' · '+self.t('Abgebrochen; bisherige Daten bleiben erhalten.','Stopped; saved data retained.')
        self.host.log.appendPlainText(text);self.host.message(text,text)
        # finished signal must release the previous worker before starting another one.
        QTimer.singleShot(150,self.reload)

    def import_cache_dialog(self):
        from datahub import import_cache
        folder=QFileDialog.getExistingDirectory(self,self.t('cache-v1-Ordner eines vorhandenen Exports auswählen','Choose cache-v1 folder from an existing export'),self.host.output.text())
        if not folder:return
        try:
            prefix=Path(folder).parent.name
            if not prefix.startswith('account-'):raise ValueError()
            account=prefix.removeprefix('account-')
            import re
            if not re.fullmatch('[a-f0-9]{20}',account):raise ValueError()
            path=self.source.text().strip()
            if not path:
                path,_=QFileDialog.getSaveFileName(self,self.t('Offline-Datenbank','Offline archive'),'garmin-cache.sqlite','SQLite (*.sqlite)')
                if not path:return
                self.host.archive_path.setText(path)
            self.host.run_worker(lambda w:import_cache(path,folder,account,w.cancel),self.cache_done,network=False)
        except Exception as exc:self.error(exc)
    def cache_done(self,result):
        self.host.message(f"Cache übernommen: {result['responses']} · Fehler: {result['errors']}",f"Cache imported: {result['responses']} · Errors: {result['errors']}")
        QTimer.singleShot(150,self.reload)

    def backup_dialog(self):
        try:source=self.path()
        except Exception as exc:self.error(exc);return
        path,_=QFileDialog.getSaveFileName(self,self.t('Datenbank sichern','Back up archive'),'garmin-backup.sqlite','SQLite (*.sqlite)')
        if path:self.host.run_worker(lambda w:backup_archive(source,path),lambda _:self.host.message('Sicherung erstellt und geprüft.','Backup created and checked.'),network=False)
    def check_database(self):
        try:path=self.path()
        except Exception as exc:self.error(exc);return
        self.host.run_worker(lambda w:validate_archive(path),lambda r:self.host.message(f"Datenbank lesbar · {r['responses']} Antworten · Integrität: OK",f"Database readable · {r['responses']} responses · Integrity: OK"),network=False)

    def repair_dialog(self):
        from repairs import repair_plan,RepairSync
        if not self.host.api:self.host.message('Zum Nachladen zuerst mit dem zugehörigen Garmin-Konto anmelden.','Sign in to the matching Garmin account to retrieve missing data.');return
        choice,ok=QInputDialog.getItem(self,self.t('Daten ergänzen','Fill missing data'),self.t('Was soll ergänzt werden?','What should be retrieved?'),[self.t('GPS der ausgewählten Aktivitäten','GPS for selected activities'),self.t('Fehlgeschlagene Abrufe wiederholen','Retry failed requests'),self.t('Fehlende Tagesdaten im Exportzeitraum','Missing daily data in export date range')],0,False)
        if not ok:return
        try:
            p=replace(self.host.make_plan(for_sync=True),archive_path=self.path(),refresh_days=0,delay=max(2,self.host.delay.value()),retries=0)
            mode=0 if choice==self.t('GPS der ausgewählten Aktivitäten','GPS for selected activities') else 1 if choice==self.t('Fehlgeschlagene Abrufe wiederholen','Retry failed requests') else 2
            tasks=repair_plan(self.path(),self.host.identity,mode,self.selected(),p)
            if not tasks:self.host.message('Keine passenden fehlenden Abrufe gefunden.','No matching missing requests found.');return
            self.host.run_worker(lambda w:RepairSync(self.host.api,p,w.cancel,lambda *a:w.event.emit(a)).run_tasks(tasks),self.repair_done)
        except Exception as exc:self.error(exc)
    def repair_done(self,result):
        self.host.archive_sync_done(result);QTimer.singleShot(150,self.reload)

    def column_menu(self):
        menu=QMenu(self)
        for col in range(1,self.table.columnCount()):
            action=menu.addAction(self.table.horizontalHeaderItem(col).text());action.setCheckable(True);action.setChecked(not self.table.isColumnHidden(col));action.toggled.connect(lambda value,col=col:self.table.setColumnHidden(col,not value))
        menu.exec(self.cursor().pos())
    def filter_config(self):
        return {'search':self.search.text(),'sport':self.sport.currentData(),'from':self.date_from.date().toString('yyyy-MM-dd'),'to':self.date_to.date().toString('yyyy-MM-dd'),'min_km':self.min_km.value(),
                'hidden_columns':[c for c in range(1,self.table.columnCount()) if self.table.isColumnHidden(c)],'column_order':[self.table.horizontalHeader().logicalIndex(i) for i in range(self.table.columnCount())]}
    def read_presets(self):
        try:self.presets=json.loads(self.presets_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.presets={}
        self.preset.clear();self.preset.addItems(sorted(self.presets))
    def save_preset(self):
        name,ok=QInputDialog.getText(self,self.t('Filter speichern','Save filter'),self.t('Name','Name'))
        if ok and name.strip():
            try:
                self.presets[name.strip()]=self.filter_config();self.presets_path.write_text(json.dumps(self.presets,ensure_ascii=False,indent=2),encoding='utf-8');self.read_presets();self.preset.setCurrentText(name.strip())
            except Exception as exc:self.error(exc)
    def load_preset(self):
        config=self.presets.get(self.preset.currentText())
        if not config:return
        self.search.setText(config['search']);self.sport.setCurrentIndex(max(0,self.sport.findData(config['sport'])));self.date_from.setDate(QDate.fromString(config['from'],'yyyy-MM-dd'));self.date_to.setDate(QDate.fromString(config['to'],'yyyy-MM-dd'));self.min_km.setValue(config['min_km'])
        for c in range(1,self.table.columnCount()):self.table.setColumnHidden(c,c in config.get('hidden_columns',[]))
        header=self.table.horizontalHeader()
        for visual,logical in enumerate(config.get('column_order',[])):
            if 0<=logical<header.count():header.moveSection(header.visualIndex(logical),visual)
    def retranslate(self):
        keys={a['_key'] for a in self.selected()} if self.table.columnCount() else set()
        self.search.setPlaceholderText(self.t('Name, Ort, Beschreibung','Name, location, description'))
        for index,pair in enumerate([('Aktivitäten','Activities'),('Tagesdaten','Daily data'),('Auswertungen','Summaries')]):self.views.setTabText(index,self.t(*pair))
        self.period.setItemText(0,self.t('Monat','Month'));self.period.setItemText(1,self.t('Jahr','Year'))
        self.map.lang=self.host.lang
        if self.data['activities'] or self.data.get('activity_status') or self.data['health']:
            self.loaded(self.data);model=self.table.selectionModel();self.table.blockSignals(True)
            for row in range(self.table.rowCount()):
                if self.table.item(row,0).data(Qt.ItemDataRole.UserRole) in keys:model.select(self.table.model().index(row,0),QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)
            self.table.blockSignals(False);self.selection_changed()
