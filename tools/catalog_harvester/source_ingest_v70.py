#!/usr/bin/env python3
"""Portable offline source adapters and coverage reports; no automatic publishing.

Usage:
  python source_ingest_v70.py --db data/catalog_registry_v69.sqlite ingest source.csv --config sources/alko.json
  python source_ingest_v70.py --db data/catalog_registry_v69.sqlite report --out reports/coverage_v70.json

Maps downloaded retailer exports to a standardized CSV, then feeds the V69 registry.
No website crawling or assumed live availability. Fields remain traceable to original rows.
"""
import argparse, csv, json, re, tempfile
from collections import defaultdict, Counter
from pathlib import Path
import json
from urllib.parse import urlparse
try:
    from .harvester import connect, import_file, load_rows, norm
except ImportError:
    from harvester import connect, import_file, load_rows, norm

STANDARD = ('name','brand','ean','category','abv','product_url','source_product_id','units','size_ml')

def read_config(path):
    config=json.loads(Path(path).read_text(encoding='utf-8'))
    if not config.get('source') or not isinstance(config.get('columns'),dict):
        raise ValueError('Config requires source and columns mapping')
    if not config['columns'].get('name'): raise ValueError('Missing name mapping')
    return config

def nested(row, key):
    if not key: return ''
    # Preferred mapping uses literal column names. Nested JSON paths are opt-in via dot notation.
    if key in row: return row[key]
    obj=row
    for part in key.split('.'):
        if not isinstance(obj,dict): return ''
        obj=obj.get(part)
    return obj if obj is not None else ''

def normalize_rows(rows, config):
    columns=config['columns']; constant=config.get('defaults',{})
    for row in rows:
        result={}
        for field in STANDARD:
            mapping=columns.get(field,'')
            if isinstance(mapping,list):
                value=next((nested(row,m) for m in mapping if str(nested(row,m)).strip()),'')
            else: value=nested(row,mapping)
            if value in ('',None): value=constant.get(field,'')
            result[field]=str(value).strip() if value is not None else ''
        if config.get('allowed_hosts') and result['product_url']:
            host=(urlparse(result['product_url']).hostname or '').lower()
            allowed=config['allowed_hosts']
            if not any(host==h or host.endswith('.'+h) for h in allowed):
                result['product_url']=''  # Never represent an untrusted link as verified retailer evidence.
        yield result

def ingest(db, path, config_path):
    config=read_config(config_path)
    raw=load_rows(path)
    normalized=list(normalize_rows(raw,config))
    # Temporary file outside project keeps the original downloadable file unchanged.
    with tempfile.TemporaryDirectory() as d:
        output=Path(d)/'normalized.csv'
        with output.open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=STANDARD);writer.writeheader();writer.writerows(normalized)
        result=import_file(db,output,config['source'])
    return {**result,'source':config['source'],'note':'Imported source observations; not verified as current Finnish retail stock'}

def coverage(db):
    data=db.execute('SELECT source,ean,name,brand,category,status,product_url,matched_product_id FROM observations').fetchall()
    by_source=defaultdict(lambda:{'rows':0,'unique_eans':set(),'unique_named':set(),'existing_ean':0,'review_rows':0,'with_product_url':0,'missing_ean':0,'invalid_or_multipack':0})
    ean_sources=defaultdict(set); ean_catalog=set(x[0] for x in db.execute('SELECT ean FROM catalog_products WHERE ean != ""'))
    review_eans=set(); unlinked_eans=set()
    for source,ean,name,brand,category,status,url,match in data:
        o=by_source[source];o['rows']+=1
        if ean:
            o['unique_eans'].add(ean);ean_sources[ean].add(source)
            if status not in ('EXISTING_EAN','MULTIPACK_REVIEW','INVALID_EAN_REVIEW'): review_eans.add(ean)
            if not url: unlinked_eans.add(ean)
        else:o['missing_ean']+=1
        if name:o['unique_named'].add((norm(name),norm(brand),norm(category)))
        if status=='EXISTING_EAN':o['existing_ean']+=1
        if status in ('CANDIDATE_REVIEW','POSSIBLE_SAME_DRINK','INVALID_EAN_REVIEW','MISSING_NAME'):o['review_rows']+=1
        if status in ('INVALID_EAN_REVIEW','MULTIPACK_REVIEW'):o['invalid_or_multipack']+=1
        if url:o['with_product_url']+=1
    return {
      'warning':'Historical observations are not evidence of current sale. EANs identify packaging, not necessarily unique drinks.',
      'catalog_products':db.execute('SELECT count(*) FROM catalog_products').fetchone()[0],
      'source_count':len(by_source),'observation_rows':len(data),
      'distinct_eans':len(ean_sources),
      'eans_already_in_catalog':len(set(ean_sources)&ean_catalog),
      'eans_not_in_catalog':len(set(ean_sources)-ean_catalog),
      'eans_seen_in_multiple_sources':sum(len(v)>1 for v in ean_sources.values()),
      'review_ean_candidates':len(review_eans-ean_catalog),
      'sources':{k:{**{a:(len(v) if isinstance(v,set) else v) for a,v in val.items()}} for k,val in sorted(by_source.items())}
    }

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db',default='catalog_registry.sqlite')
    sub=ap.add_subparsers(dest='command',required=True)
    i=sub.add_parser('ingest');i.add_argument('file');i.add_argument('--config',required=True)
    r=sub.add_parser('report');r.add_argument('--out')
    args=ap.parse_args(); db=connect(args.db)
    try:
        if args.command=='ingest':out=ingest(db,args.file,args.config)
        else:
            out=coverage(db)
            if args.out:
                p=Path(args.out);p.parent.mkdir(parents=True,exist_ok=True)
                p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(out,ensure_ascii=False,indent=2))
    finally:db.close()
if __name__=='__main__':main()
