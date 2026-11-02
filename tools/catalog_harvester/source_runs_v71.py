#!/usr/bin/env python3
"""Offline source-run ledger for repeatable catalogue imports.

Track coverage of retailer/category snapshots. Does not crawl sites or publish products.
"""
import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from source_ingest_v70 import ingest, coverage, read_config
from harvester import connect

SCHEMA = '''
CREATE TABLE IF NOT EXISTS source_targets (
  target_id TEXT PRIMARY KEY, source TEXT NOT NULL, category TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', expected_items INTEGER,
  target_url TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_runs (
  run_id INTEGER PRIMARY KEY, target_id TEXT NOT NULL REFERENCES source_targets(target_id),
  file_sha256 TEXT NOT NULL, file_name TEXT NOT NULL, recorded_at TEXT NOT NULL,
  imported_rows INTEGER NOT NULL, was_skipped INTEGER NOT NULL,
  UNIQUE(target_id,file_sha256)
);
'''

def now(): return datetime.now(timezone.utc).isoformat()

def setup(db):
    db.executescript(SCHEMA)
    return db

def validate_target(t):
    for key in ('target_id','source','category'):
        if not isinstance(t.get(key),str) or not t[key].strip(): raise ValueError(f'Missing {key}')
    if t.get('expected_items') is not None and (type(t['expected_items']) is not int or t['expected_items'] < 0):
        raise ValueError('expected_items must be nonnegative integer or null')
    url=t.get('target_url','')
    if url and (urlparse(url).scheme not in ('https','http') or not urlparse(url).hostname):
        raise ValueError('Invalid target URL')
    return t

def register(db,manifest):
    obj=json.loads(Path(manifest).read_text(encoding='utf-8'))
    targets=obj.get('targets')
    if not isinstance(targets,list): raise ValueError('Manifest must contain targets array')
    validated=[validate_target(x) for x in targets]
    if len({x['target_id'] for x in validated})!=len(validated): raise ValueError('Duplicate target_id in manifest')
    with db:
        for t in validated:
            previous=db.execute('SELECT source,category FROM source_targets WHERE target_id=?',(t['target_id'],)).fetchone()
            if previous and previous!=(t['source'],t['category']): raise ValueError('Cannot change target source/category')
            db.execute('''INSERT INTO source_targets(target_id,source,category,description,expected_items,target_url,created_at)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(target_id) DO UPDATE SET
              description=excluded.description,expected_items=excluded.expected_items,target_url=excluded.target_url''',
              (t['target_id'],t['source'],t['category'],t.get('description',''),t.get('expected_items'),t.get('target_url',''),now()))
    return {'targets_registered':len(validated)}

def import_snapshot(db,target_id,file_path,config):
    target=db.execute('SELECT source FROM source_targets WHERE target_id=?',(target_id,)).fetchone()
    if not target: raise ValueError('Unknown target; register a manifest first')
    cfg=read_config(config)
    if cfg['source']!=target[0]: raise ValueError('Source mismatch between target and adapter')
    path=Path(file_path)
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    found=db.execute('SELECT run_id FROM source_runs WHERE target_id=? AND file_sha256=?',(target_id,sha)).fetchone()
    if found: return {'skipped':True,'reason':'snapshot already tracked for this target','run_id':found[0]}
    result=ingest(db,path,config)
    with db:
        cur=db.execute('''INSERT INTO source_runs(target_id,file_sha256,file_name,recorded_at,imported_rows,was_skipped)
          VALUES(?,?,?,?,?,?)''',(target_id,sha,str(path),now(),result['rows'],int(result['skipped'])))
    return {'run_id':cur.lastrowid,'target_id':target_id,**result}

def report(db):
    targets=db.execute('''SELECT t.target_id,t.source,t.category,t.description,t.expected_items,t.target_url,
      COUNT(r.run_id),MAX(r.recorded_at),COALESCE((SELECT imported_rows FROM source_runs z WHERE z.target_id=t.target_id ORDER BY z.run_id DESC LIMIT 1),0)
      FROM source_targets t LEFT JOIN source_runs r ON r.target_id=t.target_id
      GROUP BY t.target_id ORDER BY t.source,t.category,t.target_id''').fetchall()
    rows=[]
    for id,source,category,desc,expected,url,runs,last,latest in targets:
        rows.append({'target_id':id,'source':source,'category':category,'description':desc,'expected_items':expected,
            'target_url':url,'snapshots':runs,'last_snapshot_at':last,'latest_snapshot_rows':latest,
            'status':'captured' if runs else 'pending',
            'latest_snapshot_ratio':round(latest/expected,3) if expected else None})
    return {'note':'Snapshot coverage is not market coverage. expected_items is a manually provided estimate, not verified market size.',
        'targets':len(rows),'captured_targets':sum(x['snapshots']>0 for x in rows),
        'pending_targets':sum(x['snapshots']==0 for x in rows),
        'observations':coverage(db)['observation_rows'],'items':rows}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db',default='data/catalog_registry_v69.sqlite')
    p=ap.add_subparsers(dest='cmd',required=True)
    p.add_parser('register').add_argument('manifest')
    i=p.add_parser('ingest');i.add_argument('target_id');i.add_argument('file');i.add_argument('--config',required=True)
    r=p.add_parser('report');r.add_argument('--out')
    a=ap.parse_args();db=setup(connect(a.db))
    try:
        if a.cmd=='register': result=register(db,a.manifest)
        elif a.cmd=='ingest': result=import_snapshot(db,a.target_id,a.file,a.config)
        else:
            result=report(db)
            if a.out:
                path=Path(a.out);path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps(result,indent=2,ensure_ascii=False))
    finally:db.close()
if __name__=='__main__':main()
