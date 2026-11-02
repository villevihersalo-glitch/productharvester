#!/usr/bin/env python3
"""Offline-first, reusable product intake registry. Standard library only.

Import downloaded CSV/JSON files; retain every source observation with its provenance.
Do not publish, scrape websites, or claim current stock from imported data.
"""
import argparse, csv, hashlib, json, re, sqlite3, unicodedata
from pathlib import Path
from datetime import datetime, timezone

SCHEMA='''
CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY, file_name TEXT, sha256 TEXT UNIQUE, source TEXT, imported_at TEXT, row_count INTEGER);
CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, import_id INTEGER REFERENCES imports(id), row_number INTEGER, source TEXT, source_id TEXT, name TEXT, brand TEXT, category TEXT, abv TEXT, ean TEXT, product_url TEXT, units TEXT, size_ml TEXT, status TEXT, canonical_key TEXT, matched_product_id TEXT, raw_json TEXT, UNIQUE(import_id,row_number));
CREATE INDEX IF NOT EXISTS observations_ean ON observations(ean);
CREATE INDEX IF NOT EXISTS observations_key ON observations(canonical_key);
CREATE TABLE IF NOT EXISTS catalog_products(id TEXT PRIMARY KEY, name TEXT, brand TEXT, category TEXT, abv TEXT, ean TEXT, canonical_key TEXT, source_url TEXT);
CREATE INDEX IF NOT EXISTS catalog_ean ON catalog_products(ean);
CREATE TABLE IF NOT EXISTS review_decisions(id INTEGER PRIMARY KEY, observation_id INTEGER, decision TEXT, note TEXT, decided_at TEXT);
'''
FIELDS={'name':['name','product_name','raw_name','title','Nimi'],'brand':['brand','vendor','Valmistaja'],'ean':['ean','gtin','barcode','variant_barcode'],'category':['category','product_type','type'],'abv':['abv','alcohol_percentage'],'url':['product_url','source_url','url','handle'],'source_id':['source_product_id','retailer_product_id','raw_id','id'],'units':['units','pack_units'],'size_ml':['unit_ml','size_ml']}

def pick(row,key):
    for alias in FIELDS[key]:
        if row.get(alias) is not None and str(row[alias]).strip(): return str(row[alias]).strip()
    return ''

def norm(s):
    s=unicodedata.normalize('NFKD',s or '').casefold()
    s=''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+',s))

def key_for(name,brand,abv,category):
    s=norm(name)
    # Normalize packaging, but retain flavour/style names and ABV differences.
    s=re.sub(r'\b(?:0\s*33|0\s*5|0\s*44|0\s*75|330|440|500|750)\s*(?:l|ml|cl)?\b',' ',s)
    s=re.sub(r'\b(?:tlk|tölkki|pullo|plo|can|bottle)\b',' ',s)
    s=re.sub(r'\s+',' ',s).strip()
    return '|'.join([norm(brand),s,norm(abv).replace(' ','') ,norm(category)])

def valid_ean(e):
    e=re.sub(r'\s+','',e or '')
    if not e.isdigit() or len(e) not in (8,12,13,14): return False
    digits=list(map(int,e)); check=digits.pop()
    return (10-sum(v*(3 if i%2==0 else 1) for i,v in enumerate(reversed(digits)))%10)%10==check

def connect(path):
    db=sqlite3.connect(path); db.executescript(SCHEMA); return db

def load_rows(path):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))
    if path.suffix.lower()=='.json':
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if isinstance(data,dict) and 'products' in data: data=data['products']
        if isinstance(data,dict) and 'items' in data: data=data['items']
        if not isinstance(data,list): raise ValueError('Expected JSON array or products/items array')
        # Shopify product exports can contain nested variants. Each variant is a source observation.
        output=[]
        for item in data:
            if not isinstance(item,dict): continue
            variants=item.get('variants')
            if isinstance(variants,list) and variants:
                for v in variants:
                    r={**item,**v}; r['name']=item.get('title','')+((' '+v['title']) if v.get('title') and v['title']!='Default Title' else '')
                    r['brand']=item.get('vendor',''); r['source_product_id']=str(item.get('id',''))+':'+str(v.get('id',''))
                    r['ean']=v.get('barcode','');output.append(r)
            else: output.append(item)
        return output
    raise ValueError('Only CSV and JSON file imports are supported')

def import_file(db,path,source):
    b=Path(path).read_bytes(); sha=hashlib.sha256(source.encode('utf-8')+b'\0'+b).hexdigest(); rows=load_rows(path)
    existing=db.execute('SELECT id FROM imports WHERE sha256=?',(sha,)).fetchone()
    if existing: return {'skipped':True,'rows':len(rows),'reason':'same file checksum already imported'}
    now=datetime.now(timezone.utc).isoformat()
    with db:
        cur=db.execute('INSERT INTO imports(file_name,sha256,source,imported_at,row_count) VALUES(?,?,?,?,?)',(str(path),sha,source,now,len(rows)))
        iid=cur.lastrowid
        for n,r in enumerate(rows,1):
            name=pick(r,'name');brand=pick(r,'brand');ean=pick(r,'ean');category=pick(r,'category');abv=pick(r,'abv')
            product_url=pick(r,'url');units=pick(r,'units') or '1'
            k=key_for(name,brand,abv,category)
            exact=db.execute('SELECT id FROM catalog_products WHERE ean=?',(ean,)).fetchone() if ean else None
            similar=db.execute('SELECT id FROM catalog_products WHERE canonical_key=?',(k,)).fetchone() if name else None
            if not name: status='MISSING_NAME'
            elif units.isdigit() and int(units)>1: status='MULTIPACK_REVIEW'
            elif exact: status='EXISTING_EAN'
            elif similar: status='POSSIBLE_SAME_DRINK'
            elif ean and not valid_ean(ean): status='INVALID_EAN_REVIEW'
            else: status='CANDIDATE_REVIEW'
            db.execute('''INSERT INTO observations(import_id,row_number,source,source_id,name,brand,category,abv,ean,product_url,units,size_ml,status,canonical_key,matched_product_id,raw_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (iid,n,source,pick(r,'source_id'),name,brand,category,abv,ean,product_url,units,pick(r,'size_ml'),status,k,(exact or similar or [''])[0],json.dumps(r,ensure_ascii=False)))
    return {'skipped':False,'rows':len(rows),'import_id':iid}

def seed_catalog(db,path):
    rows=load_rows(path)
    with db:
        for r in rows:
            db.execute('INSERT OR REPLACE INTO catalog_products VALUES(?,?,?,?,?,?,?,?)', (r['id'],pick(r,'name'),pick(r,'brand'),pick(r,'category'),pick(r,'abv'),pick(r,'ean'),key_for(pick(r,'name'),pick(r,'brand'),pick(r,'abv'),pick(r,'category')),pick(r,'url')))
    return len(rows)

def export_review(db,path):
    rows=db.execute('''SELECT source,name,brand,category,abv,ean,product_url,units,status,matched_product_id,canonical_key FROM observations ORDER BY id''').fetchall()
    headings=['source','name','brand','category','abv','ean','product_url','units','status','matched_product_id','canonical_key']
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(headings);w.writerows(rows)
    return len(rows)

def stats(db):
    return {'catalog_products':db.execute('SELECT count(*) FROM catalog_products').fetchone()[0], 'imports':db.execute('SELECT count(*) FROM imports').fetchone()[0], 'observations':db.execute('SELECT count(*) FROM observations').fetchone()[0], 'statuses':dict(db.execute('SELECT status,count(*) FROM observations GROUP BY status')), 'sources':dict(db.execute('SELECT source,count(*) FROM observations GROUP BY source'))}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db',default='catalog_registry.sqlite');sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('seed');p.add_argument('products')
    p=sub.add_parser('import');p.add_argument('file');p.add_argument('--source',required=True)
    p=sub.add_parser('export');p.add_argument('csv')
    sub.add_parser('stats')
    a=ap.parse_args(); db=connect(a.db)
    if a.cmd=='seed': result={'seeded':seed_catalog(db,a.products)}
    elif a.cmd=='import': result=import_file(db,a.file,a.source)
    elif a.cmd=='export': result={'rows':export_review(db,a.csv)}
    else: result=stats(db)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
