"""A self-contained SQLite archive of successful Garmin responses and optional binaries."""
from __future__ import annotations
import hashlib
import json
import sqlite3
from datetime import datetime,timezone
from pathlib import Path

APP_ID=1196641841
class OfflineArchiveError(Exception):pass
class OfflineAccountMismatch(OfflineArchiveError):pass
class OfflineDataMissing(OfflineArchiveError):pass
class OfflineNetworkDisabled(OfflineArchiveError):pass


def entry_key(dataset,day,request):
    raw=json.dumps([dataset,day,request],sort_keys=True,ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


class Archive:
    def __init__(self,path,account=None,readonly=False):
        self.path=Path(path).expanduser().resolve();self.readonly=readonly
        exists=self.path.exists()
        if readonly and not exists:raise OfflineArchiveError('Archive does not exist')
        if not readonly:self.path.parent.mkdir(parents=True,exist_ok=True)
        try:
            self.db=sqlite3.connect(self.path.as_uri()+'?mode='+('ro' if readonly else 'rwc'),uri=True,timeout=30)
            self.db.row_factory=sqlite3.Row
            if exists:
                if self.db.execute('PRAGMA application_id').fetchone()[0]!=APP_ID:
                    raise OfflineArchiveError('Not a TTN Garmin Export archive')
                meta=dict(self.db.execute('SELECT key,value FROM app_meta'))
                if meta.get('schema')!='1':raise OfflineArchiveError('Unsupported archive version')
                self.account=meta['account']
                if account and account!=self.account:raise OfflineAccountMismatch('Account mismatch')
            else:
                if readonly or not account:raise OfflineArchiveError('Account is required for a new archive')
                self.account=account
                self.db.executescript('''
                    CREATE TABLE app_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE responses(
                      entry_key TEXT PRIMARY KEY,dataset TEXT NOT NULL,day TEXT NOT NULL,
                      request_json TEXT NOT NULL,is_binary INTEGER NOT NULL,
                      fetched_utc TEXT NOT NULL,is_empty INTEGER NOT NULL,payload BLOB NOT NULL);
                    CREATE INDEX by_dataset_day ON responses(dataset,day);
                    CREATE TABLE attempts(
                      entry_key TEXT PRIMARY KEY,dataset TEXT NOT NULL,day TEXT NOT NULL,
                      attempted_utc TEXT NOT NULL,error_type TEXT NOT NULL,http_status INTEGER);
                    CREATE TABLE sync_runs(
                      id INTEGER PRIMARY KEY,created_utc TEXT NOT NULL,report_json TEXT NOT NULL);
                ''')
                self.db.execute(f'PRAGMA application_id={APP_ID}')
                self.db.executemany('INSERT INTO app_meta VALUES(?,?)',[('schema','1'),('account',account)])
                self.db.commit()
            if not readonly:
                self.db.execute("CREATE TABLE IF NOT EXISTS inferred_skips(entry_key TEXT PRIMARY KEY,dataset TEXT NOT NULL,day TEXT NOT NULL,request_json TEXT NOT NULL,evidence_json TEXT NOT NULL,created_utc TEXT NOT NULL)")
                self.db.commit()
            self.has_inferred_skips=bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='inferred_skips' AND type='table'").fetchone())
        except Exception as exc:
            if hasattr(self,'db'):self.db.close()
            if isinstance(exc,sqlite3.Error):raise OfflineArchiveError('Cannot read or create this archive') from exc
            raise

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def close(self):self.db.close()

    def put(self,dataset,day,request,payload,binary=False,fetched_utc=None):
        key=entry_key(dataset,day,request)
        data=payload if binary else json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.db.execute('INSERT OR REPLACE INTO responses VALUES(?,?,?,?,?,?,?,?)',
            (key,dataset,day,json.dumps(request,sort_keys=True),int(binary),
             fetched_utc or datetime.now(timezone.utc).isoformat(),int(not binary and payload in (None,[],{})),sqlite3.Binary(data)))
        self.db.execute('DELETE FROM attempts WHERE entry_key=?',(key,))
        if self.has_inferred_skips:self.db.execute('DELETE FROM inferred_skips WHERE entry_key=?',(key,))
        self.db.commit()
        return key

    def mark_inferred(self,dataset,day,request,evidence):
        key=entry_key(dataset,day,request)
        if self.by_key(key):return
        self.db.execute('INSERT OR REPLACE INTO inferred_skips VALUES(?,?,?,?,?,?)',
            (key,dataset,day,json.dumps(request,sort_keys=True),json.dumps(evidence,sort_keys=True),datetime.now(timezone.utc).isoformat()))
        self.db.commit()

    def inferred(self,dataset,day,request):
        return self.has_inferred_skips and bool(self.db.execute('SELECT 1 FROM inferred_skips WHERE entry_key=?',(entry_key(dataset,day,request),)).fetchone())

    def failed(self,dataset,day,request,exc,status):
        self.db.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?,?,?)',
            (entry_key(dataset,day,request),dataset,day,datetime.now(timezone.utc).isoformat(),type(exc).__name__,status))
        self.db.commit()

    def get(self,dataset,day,request):return self.by_key(entry_key(dataset,day,request))
    def by_key(self,key):
        row=self.db.execute('SELECT * FROM responses WHERE entry_key=?',(key,)).fetchone()
        return self.decode(row) if row else None
    @staticmethod
    def decode(row):
        value=dict(row);value['data']=bytes(value.pop('payload'))
        if not value['is_binary']:value['data']=json.loads(value['data'])
        return value
    def samples(self,dataset,start,end,limit=3):
        rows=self.db.execute("SELECT * FROM responses WHERE dataset=? AND (day BETWEEN ? AND ? OR day='snapshot') ORDER BY day,entry_key LIMIT ?",(dataset,start,end,limit))
        return [self.decode(r) for r in rows]
    def snapshot(self,dataset):
        rows=self.samples(dataset,'0000-01-01','9999-12-31',1)
        return rows[0]['data'] if rows else None
    def record_run(self,report):
        self.db.execute('INSERT INTO sync_runs(created_utc,report_json) VALUES(?,?)',
            (datetime.now(timezone.utc).isoformat(),json.dumps(report,ensure_ascii=False)));self.db.commit()
    def coverage(self,start,end):
        # Collapse per-activity resources to keep the overview small even for years of data.
        family="CASE WHEN dataset LIKE 'activity-%' THEN 'activity_extras' ELSE dataset END"
        rows=self.db.execute(f"SELECT {family} AS dataset,MIN(day) AS first_day,MAX(day) AS last_day,COUNT(DISTINCT day) AS stored_days,COUNT(*) AS responses,SUM(is_empty) AS empty_responses,MAX(fetched_utc) AS fetched_utc FROM responses WHERE day BETWEEN ? AND ? OR day='snapshot' GROUP BY {family} ORDER BY dataset",(start,end))
        result=[dict(r) for r in rows]
        errors={r['dataset']:r['n'] for r in self.db.execute(f"SELECT {family} AS dataset,COUNT(*) AS n FROM attempts WHERE day BETWEEN ? AND ? OR day='snapshot' GROUP BY {family}",(start,end))}
        for item in result:item['failed_requests']=errors.pop(item['dataset'],0)
        result.extend({'dataset':k,'first_day':'','last_day':'','stored_days':0,'responses':0,'empty_responses':0,'fetched_utc':'','failed_requests':n} for k,n in errors.items())
        inferred={}
        if self.has_inferred_skips:
            inferred={r['dataset']:r['n'] for r in self.db.execute(f"SELECT {family} AS dataset,COUNT(DISTINCT day) AS n FROM inferred_skips s WHERE day BETWEEN ? AND ? AND NOT EXISTS(SELECT 1 FROM responses r WHERE r.dataset=s.dataset AND r.day=s.day) GROUP BY {family}",(start,end))}
        for item in result:item['unverified_days']=inferred.pop(item['dataset'],0)
        result.extend({'dataset':k,'stored_days':0,'unverified_days':n} for k,n in inferred.items())
        return result
