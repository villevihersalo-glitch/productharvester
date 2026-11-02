"""V74 local-first source inventory, durable sequential queue and review-only exports.
This module has no network calls. Worker reuses V73's guarded fetcher.
"""
import csv
import io
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from .engine import utcnow, safe_remote_url

SCHEMA = '''
CREATE TABLE IF NOT EXISTS catalog_sources (
 id TEXT PRIMARY KEY, merchant TEXT NOT NULL, category TEXT NOT NULL,
 url TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'requires_adapter',
 state TEXT NOT NULL DEFAULT 'unprocessed', note TEXT NOT NULL DEFAULT '',
 evidence TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS queue_jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, label TEXT NOT NULL,
 settings_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
 enabled INTEGER NOT NULL DEFAULT 1, due_at TEXT NOT NULL, interval_hours INTEGER NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, last_result TEXT NOT NULL DEFAULT '',
 last_started TEXT NOT NULL DEFAULT '', last_finished TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS queue_due_idx ON queue_jobs (enabled,state,due_at);
'''

MODES=('file','url_export','shopify','urls','sitemap','alko_file','alko_mirror')

def parse_utc(value):
    if not value: return datetime.now(timezone.utc)
    value=str(value).strip()
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None: raise ValueError('Ajastetun ajan tulee sisältää aikavyöhyke')
    return dt.astimezone(timezone.utc)


def write_csv(path, headers, rows):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f); w.writerow(headers); w.writerows(rows)
    return path


def seed_sources(db, manifest):
    """Keep operator edits; seed only missing verified public category entrances."""
    sources=json.loads(Path(manifest).read_text(encoding='utf-8'))
    for item in sources:
        db.execute('''INSERT OR IGNORE INTO catalog_sources(id,merchant,category,url,mode,state,note,evidence,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?)''',
                   (item['id'],item['merchant'],item['category'],item['url'],item['mode'],item['state'],
                    item.get('note',''),item.get('evidence',''),utcnow()))
    return len(sources)


def source_table(db):
    return [dict(zip(('id','merchant','category','url','mode','state','note','evidence','updated_at'),r))
            for r in db.execute('SELECT id,merchant,category,url,mode,state,note,evidence,updated_at FROM catalog_sources ORDER BY merchant,category')]


def queue_table(db):
    return [dict(zip(('id','source_id','label','state','enabled','due_at','interval_hours','attempts','last_result','last_started','last_finished','settings'),
                     (*r[:-1], json.loads(r[-1])))) for r in db.execute(
        '''SELECT id,source_id,label,state,enabled,due_at,interval_hours,attempts,last_result,last_started,last_finished,settings_json
           FROM queue_jobs ORDER BY due_at,id''')]


def validate_job(data):
    mode=str(data.get('mode','')).strip()
    if mode not in MODES: raise ValueError('Ei ajettavissa: lähde tarvitsee adapterin tai keruutapa puuttuu')
    source=str(data.get('source','')).strip()[:100]
    if not source: raise ValueError('Lähdenimi puuttuu')
    settings={k:data[k] for k in ('source','mode','category','url','urls','pattern','filename','delay','max_pages','resume') if k in data}
    settings['source']=source;settings['mode']=mode
    if mode=='urls':
        urls=[x.strip() for x in str(data.get('urls','')).splitlines() if x.strip()]
        if not urls: raise ValueError('Tuotelinkkilista puuttuu')
        for u in urls: safe_remote_url(u,resolve=False)
        # The engine enforces a single host; reject mismatched URLs early.
        if len({urlparse(u).hostname for u in urls})>1:raise ValueError('Tuotelinkit on jaettava verkkotunnuksen mukaan')
    elif mode=='alko_mirror':
        from .alko_v75 import mirror_page_url
        if source!='Alko':raise ValueError('Alko-hinnastopeili vain Alko-lähteelle')
        mirror_page_url(1,str(data.get('url','')).strip())
    elif mode not in ('file','alko_file'):safe_remote_url(str(data.get('url','')).strip(),resolve=False)
    else:
        if mode=='alko_file' and source!='Alko':raise ValueError('Alko-hinnasto vain Alko-lähteelle')
        filename=str(data.get('filename',''))
        if Path(filename).name!=filename or not filename.lower().endswith(('.csv','.json') if mode=='file' else ('.csv','.xlsx')):
            raise ValueError('Paikallinen tuontitiedosto puuttuu')
    delay=float(data.get('delay',1.5));pages=int(data.get('max_pages') or 100)
    if not 1 <= delay <= 30:raise ValueError('Viiveen tulee olla 1–30 sekuntia')
    if not 1 <= pages <= 3000:raise ValueError('Sivujen yläraja 1–3000')
    settings['delay']=delay;settings['max_pages']=pages
    interval=int(data.get('interval_hours') or 0)
    if not 0 <= interval <= 8760:raise ValueError('Virheellinen uusintaväli')
    due=parse_utc(data.get('due_at'))
    return settings,interval,due.isoformat(timespec='seconds')


class QueueManager:
    def __init__(self, svc):
        self.svc=svc;self.shutdown=threading.Event();self.thread=None;self.lock=threading.Lock()
        with self.svc.connect() as db:
            db.executescript(SCHEMA)
            seed_sources(db, self.svc.root/'tools/harvester_desktop/source_catalog_v74.json')
            # interrupted application should not leave queues permanently running
            db.execute("UPDATE queue_jobs SET state='queued' WHERE state='running'")

    def status(self):
        with self.svc.connect() as db:
            return {'sources':source_table(db),'queue':queue_table(db)}

    def enqueue(self,configs):
        if not isinstance(configs,list) or not configs or len(configs)>100:
            raise ValueError('Valitse 1–100 työtä')
        additions=[]
        with self.svc.connect() as db:
            for data in configs:
                settings,interval,due=validate_job(data)
                source_id=str(data.get('source_id','')).strip() or None
                label=str(data.get('label') or settings['source']).strip()[:120]
                cur=db.execute('''INSERT INTO queue_jobs(source_id,label,settings_json,due_at,interval_hours,created_at)
                        VALUES(?,?,?,?,?,?)''',(source_id,label,json.dumps(settings,ensure_ascii=False),due,interval,utcnow()))
                additions.append(cur.lastrowid)
        return {'queued':additions}

    def enqueue_csv(self,raw):
        if len(raw)>2_000_000:raise ValueError('CSV liian suuri')
        text=raw.decode('utf-8-sig')
        rows=list(csv.DictReader(io.StringIO(text)))
        if len(rows)>100:raise ValueError('Tuo enintään 100 työtä kerrallaan')
        tasks=[];skipped=[]
        for i,row in enumerate(rows,2):
            if not row.get('mode') or row['mode'] not in MODES:
                skipped.append({'row':i,'reason':'Adapteri puuttuu / ei tuettu keruutapa'});continue
            try:
                cfg={k:v for k,v in row.items() if v is not None and v!=''}
                validate_job(cfg)
                tasks.append(cfg)
            except Exception as e:skipped.append({'row':i,'reason':str(e)})
        added=self.enqueue(tasks)['queued'] if tasks else []
        return {'queued':added,'skipped':skipped}

    def toggle(self,jid,enabled):
        with self.svc.connect() as db:
            row=db.execute('SELECT id,state FROM queue_jobs WHERE id=?',(int(jid),)).fetchone()
            if not row:raise ValueError('Työtä ei löydy')
            if row[1]=='running':raise ValueError('Käynnissä olevaa työtä ei voi muuttaa')
            db.execute('UPDATE queue_jobs SET enabled=?, state=CASE WHEN ?=1 AND state IN (\'finished\',\'error\') THEN \'queued\' ELSE state END WHERE id=?',
                       (int(bool(enabled)),int(bool(enabled)),int(jid)))
        return {'ok':True}

    def run_now(self, ids):
        if not isinstance(ids,list) or not ids or len(ids)>100:raise ValueError('Valitse jonosta vähintään yksi työ')
        with self.svc.connect() as db:
            for i in ids:
                db.execute("UPDATE queue_jobs SET enabled=1,state='queued',due_at=? WHERE id=? AND state != 'running'",(utcnow(),int(i)))
        return {'queued':len(ids)}

    def remove(self,ids):
        with self.svc.connect() as db:
            for i in ids:db.execute("DELETE FROM queue_jobs WHERE id=? AND state!='running'",(int(i),))
        return {'removed':len(ids)}

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.shutdown.clear()
        self.thread=threading.Thread(target=self.loop,name='harvester-scheduler',daemon=True)
        self.thread.start()

    def stop(self):
        self.shutdown.set()
        if self.thread:self.thread.join(timeout=3)

    def loop(self):
        while not self.shutdown.wait(0.8):
            # Do not launch alongside a manually started job.
            if self.svc.worker and self.svc.worker.is_alive():continue
            with self.lock:
                with self.svc.connect() as db:
                    row=db.execute("SELECT id,settings_json,interval_hours FROM queue_jobs WHERE enabled=1 AND state='queued' AND due_at<=? ORDER BY due_at,id LIMIT 1",(utcnow(),)).fetchone()
                    if not row:continue
                    jid,settings_json,interval=row
                    db.execute("UPDATE queue_jobs SET state='running',attempts=attempts+1,last_started=? WHERE id=?",(utcnow(),jid))
                try:
                    self.svc.start(json.loads(settings_json))
                    # Worker runs until completion; subsequent queued jobs stay sequential.
                    while self.svc.worker and self.svc.worker.is_alive():
                        self.svc.worker.join(timeout=0.5)
                    state=self.svc.status()['job']['state']
                    with self.svc.connect() as db:
                        if state=='finished' and interval>0:
                            due=(datetime.now(timezone.utc)+timedelta(hours=interval)).isoformat(timespec='seconds')
                            db.execute('UPDATE queue_jobs SET state=?,due_at=?,last_finished=?,last_result=? WHERE id=?',
                                       ('queued',due,utcnow(),'finished; repeat scheduled',jid))
                        else:
                            db.execute('UPDATE queue_jobs SET state=?,enabled=0,last_finished=?,last_result=? WHERE id=?',
                                       (state,utcnow(),state,jid))
                except Exception as exc:
                    with self.svc.connect() as db:
                        db.execute('UPDATE queue_jobs SET state=?,enabled=0,last_finished=?,last_result=? WHERE id=?',
                                   ('error',utcnow(),str(exc)[:500],jid))


def export_target(svc, target='generic', selection='review', output=None):
    """Review/staging export only. Never writes any application's published catalogue."""
    if target not in ('kaljappi','coffee','generic'):raise ValueError('Tuntematon kohdesovellus')
    if selection not in ('review','all'):raise ValueError('Tuntematon valinta')
    sql='''SELECT id,source,name,brand,category,abv,ean,product_url,units,size_ml,status,matched_product_id,raw_json
             FROM observations'''
    if selection=='review':sql += " WHERE status IN ('CANDIDATE_REVIEW','POSSIBLE_SAME_DRINK','INVALID_EAN_REVIEW','MISSING_NAME','MULTIPACK_REVIEW')"
    sql+=' ORDER BY id'
    with svc.connect() as db: rows=db.execute(sql).fetchall()
    keys=['observation_id','source','name','brand','category','abv','ean','product_url','units','size_ml','review_status','matched_product_id','raw_json']
    output=Path(output) if output else svc.workspace/'exports'/f'{target}_{selection}_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.csv'
    headers=['target_app','publication_status']+keys
    write_csv(output,headers,[[target,'REVIEW_REQUIRED',*row] for row in rows])
    return {'file':str(output),'count':len(rows),'target':target,'published':False}


def historical_inventory(svc, output):
    """Historical observations are not evidence of a full completed merchant crawl."""
    with svc.connect() as db:
        rows=db.execute('''SELECT product_url, group_concat(DISTINCT source),count(*),max(name),max(ean),max(status)
                      FROM observations WHERE product_url IS NOT NULL AND product_url<>''
                      GROUP BY product_url ORDER BY product_url''').fetchall()
    write_csv(output,['url','historical_sources','observation_count','sample_name','sample_ean','last_status','evidence_class'],
              [[*r,'historical_record_not_full_catalog_crawl'] for r in rows])
    return len(rows)
