"""General-purpose, local-first product harvesting engine for Kaljappi and other projects.

Only public exports, explicitly supplied sitemaps/URLs and publicly exposed Shopify
JSON are supported. Honors robots.txt; does not bypass authentication or rate limits.
No scraped record is published into the app automatically.
"""
import csv
import hashlib
import ipaddress
import json
import os
import re
import socket
import sqlite3
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from contextlib import contextmanager
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from tools.catalog_harvester import harvester
from tools.catalog_harvester.source_ingest_v70 import STANDARD, normalize_rows
from datetime import datetime, timezone

MAX_RESPONSE_BYTES = 14 * 1024 * 1024
MAX_LIST_URLS = 10000
BEVERAGE_CATEGORIES = ('beer', 'cider', 'longdrink', 'seltzer')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS desktop_runs(
  id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, source TEXT,
  mode TEXT, category TEXT, origin TEXT, state TEXT, pages INTEGER DEFAULT 0,
  rows INTEGER DEFAULT 0, newly_seen_eans INTEGER DEFAULT 0, details TEXT DEFAULT ''
);
'''

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def canonical_category(value):
    v=str(value or '').casefold().strip()
    mapping = {'olut':'beer','beer':'beer','olue':'beer','ale':'beer','siideri':'cider','cider':'cider',
               'lonkero':'longdrink','long drink':'longdrink','longdrink':'longdrink',
               'hard seltzer':'seltzer','seltzeri':'seltzer','seltzer':'seltzer'}
    return mapping.get(v, v)


def safe_remote_url(url, resolve=True):
    """Disallow local addresses, credentials and non-HTTP protocols in a web-fetch job."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Anna kelvollinen julkinen http/https-osoite')
    hostname = parsed.hostname.lower()
    if hostname in ('localhost',) or hostname.endswith(('.localhost','.local','.internal')):
        raise ValueError('Paikalliseen verkkoon osoittavia osoitteita ei noudeta')
    if parsed.port and parsed.port not in (80,443):
        raise ValueError('Vain portit 80 ja 443 ovat sallittuja')
    try:
        ip = ipaddress.ip_address(hostname)
        if not ip.is_global:
            raise ValueError('Yksityiset IP-osoitteet eivät ole sallittuja')
    except ValueError as exc:
        if 'Yksityiset IP' in str(exc):
            raise
    if resolve:
        for entry in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme=='https' else 80), type=socket.SOCK_STREAM):
            if not ipaddress.ip_address(entry[4][0]).is_global:
                raise ValueError('Osoite ei ole julkisessa verkossa')
    return url


class SameSiteRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects outside the original host to limit accidental cross-site crawling."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlparse(req.full_url).hostname
        new = urllib.parse.urlparse(newurl).hostname
        safe_remote_url(newurl)
        if old != new:
            raise ValueError('Sivusto ohjasi toiselle verkkotunnukselle; tarkista osoite')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class LDParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.active=False;self.scripts=[];self.buffer=[];self.title='';self.in_title=False
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag == 'script' and 'ld+json' in a.get('type','').lower():
            self.active=True;self.buffer=[]
        if tag == 'title': self.in_title=True
    def handle_data(self,data):
        if self.active: self.buffer.append(data)
        if self.in_title: self.title+=data
    def handle_endtag(self,tag):
        if tag == 'script' and self.active:
            self.scripts.append(''.join(self.buffer));self.buffer=[];self.active=False
        if tag == 'title': self.in_title=False


def product_nodes(item):
    if isinstance(item,list):
        for obj in item: yield from product_nodes(obj)
    elif isinstance(item,dict):
        types=item.get('@type',[])
        types=[types] if isinstance(types,str) else types
        if any(str(x).lower().split('/')[-1]=='product' for x in types): yield item
        if '@graph' in item: yield from product_nodes(item['@graph'])
        if 'mainEntity' in item: yield from product_nodes(item['mainEntity'])


def extract_product_html(html, page_url, category=''):
    """Only use explicit page data. No invented EAN, flavor, ABV or stock evidence."""
    p=LDParser();p.feed(html)
    products=[]
    for raw in p.scripts:
        try: obj=json.loads(raw)
        except (ValueError,TypeError): continue
        for node in product_nodes(obj):
            name=str(node.get('name') or '').strip()
            if not name: continue
            brand=node.get('brand') or ''
            if isinstance(brand,dict): brand=brand.get('name','')
            code=next((str(node.get(k) or '').strip() for k in ('gtin13','gtin14','gtin12','gtin8','gtin','sku') if str(node.get(k) or '').strip()),'')
            code=code if code.isdigit() and harvester.valid_ean(code) else ''
            abv=node.get('alcoholContent','')
            if isinstance(abv,dict): abv=abv.get('value','')
            offers=node.get('offers') or {}
            if isinstance(offers,list): offers=offers[0] if offers else {}
            available=offers.get('availability','') if isinstance(offers,dict) else ''
            products.append({'name':name,'brand':str(brand),'ean':code,'category':category,
                             'abv':str(abv or ''),'product_url':page_url,
                             'source_product_id':str(node.get('productID') or node.get('sku') or ''),
                             'units':'1','size_ml':'',
                             'evidence':'JSON-LD Product','availability_claim':str(available),
                             'description_source':str(node.get('description') or '')[:1200]})
    return products


def parse_sitemap(xml):
    """Return (is_index, URLs); works with sitemap namespaces and gzip handled upstream."""
    root=ET.fromstring(xml)
    kind=root.tag.rsplit('}',1)[-1].lower()
    if kind not in ('sitemapindex','urlset'):
        raise ValueError('Ei kelvollinen sitemapindex/urlset XML')
    return kind == 'sitemapindex', [x.text.strip() for x in root.iter() if x.tag.rsplit('}',1)[-1]=='loc' and x.text]


class HarvesterDesktop:
    def __init__(self, root):
        self.root=Path(root).resolve()
        configured=os.environ.get('PRODUCT_HARVESTER_DATA_DIR','').strip()
        self.data_root=Path(configured).expanduser().resolve() if configured else self.root
        (self.data_root/'data').mkdir(parents=True,exist_ok=True)
        self.dbpath=self.data_root/'data/catalog_registry_v69.sqlite'
        self.workspace=self.data_root/'workspace'
        for d in ('imports','snapshots','exports','runs'):(self.workspace/d).mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.stop=threading.Event();self.worker=None
        self.job={'state':'idle','source':'','mode':'','processed':0,'total':0,'found':0,'pages':0,'errors':0,'logs':[]}
        self.robots={};self._last_request={}
        with self.connect() as db:
            db.executescript(SCHEMA)
            # V72 has 351 products; V69's catalogue snapshot still has only 348.
            if (self.root/'data/products.json').is_file():
                harvester.seed_catalog(db,self.root/'data/products.json')

    @contextmanager
    def connect(self):
        db=harvester.connect(self.dbpath)
        try:
            db.executescript(SCHEMA)
            yield db
            db.commit()
        finally:
            db.close()

    def _update(self, **changes):
        with self.lock: self.job.update(changes)

    def _log(self,msg):
        with self.lock:
            self.job['logs'].append({'time':datetime.now().strftime('%H:%M:%S'),'text':msg})
            self.job['logs']=self.job['logs'][-90:]

    def status(self):
        with self.lock: job=json.loads(json.dumps(self.job))
        with self.connect() as db:
            counts=harvester.stats(db)
            unique_eans=db.execute("SELECT COUNT(DISTINCT ean) FROM observations WHERE ean != ''").fetchone()[0]
            existing=db.execute("SELECT COUNT(DISTINCT o.ean) FROM observations o JOIN catalog_products c ON o.ean=c.ean WHERE o.ean != ''").fetchone()[0]
            reviews=db.execute("SELECT COUNT(*) FROM observations WHERE status IN ('CANDIDATE_REVIEW','POSSIBLE_SAME_DRINK','INVALID_EAN_REVIEW','MISSING_NAME','MULTIPACK_REVIEW')").fetchone()[0]
            runs=[dict(zip(('id','started','source','mode','category','state','pages','rows','new_eans'),row)) for row in db.execute('SELECT id,started_at,source,mode,category,state,pages,rows,newly_seen_eans FROM desktop_runs ORDER BY id DESC LIMIT 12')]
            latest=[dict(zip(('name','source','ean','status'),row)) for row in db.execute('SELECT name,source,ean,status FROM observations ORDER BY id DESC LIMIT 12')]
        return {'job':job,'totals':{'catalog':counts['catalog_products'],'raw':counts['observations'],
                'unique_eans':unique_eans,'known_eans':existing,'unmatched_eans':max(0,unique_eans-existing),
                'review_rows':reviews,'imports':counts['imports']},'statuses':counts['statuses'],'sources':counts['sources'],
                'runs':runs,'recent_products':latest}

    def _robots_allows(self,url):
        pu=urllib.parse.urlparse(url); origin=f'{pu.scheme}://{pu.netloc}'
        if origin not in self.robots:
            robots_url=origin+'/robots.txt'
            try:
                # Robots retrieval itself is not gated on robots.txt.
                req=urllib.request.Request(robots_url,headers={'User-Agent':'KaljappiProductHarvester/1.0 (personal research; contact site owner for bulk data)'})
                with urllib.request.build_opener(SameSiteRedirect()).open(req,timeout=12) as rsp:
                    content=rsp.read(512*1024).decode('utf-8','replace')
                rp=urllib.robotparser.RobotFileParser();rp.parse(content.splitlines());self.robots[origin]=rp
            except urllib.error.HTTPError as e:
                if e.code==404:
                    rp=urllib.robotparser.RobotFileParser();rp.parse([]);self.robots[origin]=rp
                else: raise ValueError(f'robots.txt ei saatavilla ({e.code}); verkkokeruu keskeytetty') from e
            except Exception as e:
                raise ValueError('robots.txt -tarkistus epäonnistui; verkkokeruu keskeytetty: '+str(e)) from e
        return self.robots[origin].can_fetch('KaljappiProductHarvester',url)

    def _fetch(self,url,delay=1.5):
        safe_remote_url(url)
        if not self._robots_allows(url): raise ValueError('robots.txt kieltää tämän URL-osoitteen keruun')
        origin=urllib.parse.urlparse(url).netloc
        previous=self._last_request.get(origin,0)
        if previous: time.sleep(max(0,float(delay)-(time.monotonic()-previous)))
        if self.stop.is_set(): raise InterruptedError('Keskeytetty käyttäjän pyynnöstä')
        self._last_request[origin]=time.monotonic()
        req=urllib.request.Request(url,headers={'User-Agent':'KaljappiProductHarvester/1.0 (respectful research)','Accept':'text/html,application/json,text/csv,application/xml,text/xml,*/*;q=0.5'})
        try:
            with urllib.request.build_opener(SameSiteRedirect()).open(req,timeout=25) as response:
                raw_length=(response.headers.get('Content-Length') or '').strip()
                # Missing or blank Content-Length is legitimate for chunked HTTP.
                # The bounded read below still enforces MAX_RESPONSE_BYTES.
                content_length=int(raw_length) if raw_length.isdecimal() else 0
                if content_length>MAX_RESPONSE_BYTES: raise ValueError('Lähdetiedosto ylittää 14 MB kokorajan')
                data=response.read(MAX_RESPONSE_BYTES+1)
                if len(data)>MAX_RESPONSE_BYTES: raise ValueError('Lähdetiedosto ylittää 14 MB kokorajan')
                return data,dict(response.headers),response.url
        except urllib.error.HTTPError as e:
            if e.code in (401,403,429): raise ValueError(f'Palvelin rajoittaa pääsyä (HTTP {e.code}). Käytä luvallista vientitiedostoa tai rajapintaa.') from e
            raise ValueError(f'HTTP {e.code}: {url}') from e

    def _unique_preexisting(self):
        with self.connect() as db:
            a={r[0] for r in db.execute("SELECT DISTINCT ean FROM observations WHERE ean != ''")}
            a.update(r[0] for r in db.execute("SELECT DISTINCT ean FROM catalog_products WHERE ean != ''"))
        return a

    def _store_rows(self,rows,source,category,run_id,existing):
        if not rows: return {'rows':0,'new_eans':0,'skipped':False}
        # Preserve every raw source observation; category is explicitly supplied only if missing.
        normalized=[]
        for row in rows:
            r=dict(row)
            r['category']=r.get('category') or category
            if 'product_url' not in r and 'url' in r:r['product_url']=r['url']
            normalized.append(r)
        name=f'{run_id}_{uuid.uuid4().hex[:8]}.json'
        output=self.workspace/'snapshots'/name
        output.write_text(json.dumps(normalized,ensure_ascii=False,indent=2),encoding='utf-8')
        with self.connect() as db:
            result=harvester.import_file(db,output,source)
        distinct={str(r.get('ean','')).strip() for r in normalized if harvester.valid_ean(str(r.get('ean','')).strip())}
        # Every run gets a human-readable automatic file, regardless of later publication.
        csv_path=self.workspace/'runs'/f'run_{run_id:06d}.csv'
        import csv
        fields=['name','brand','ean','category','abv','product_url','source_product_id','units','size_ml','evidence','availability_claim','description_source']
        with csv_path.open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(normalized)
        return {'rows':len(normalized),'new_eans':len(distinct-existing),'skipped':result['skipped'],
                'file':str(output), 'csv':str(csv_path),
                'import_id':result.get('import_id')}

    def _read_file_rows(self,path,source,category):
        rows=harvester.load_rows(path)
        config_path=self.root/'tools/catalog_harvester/sources'/({'Alko':'alko','S-kaupat':'s_kaupat','K-Ruoka':'k_ruoka','Shopify':'shopify'}.get(source,'')+'.json')
        if config_path.is_file():
            config=json.loads(config_path.read_text(encoding='utf-8'))
            normalized=list(normalize_rows(rows,config))
        else:
            normalized=[{'name':harvester.pick(r,'name'),'brand':harvester.pick(r,'brand'),'ean':harvester.pick(r,'ean'),
                         'category':harvester.pick(r,'category'),'abv':harvester.pick(r,'abv'),'product_url':harvester.pick(r,'url'),
                         'source_product_id':harvester.pick(r,'source_id'),'units':harvester.pick(r,'units') or '1','size_ml':harvester.pick(r,'size_ml')} for r in rows]
        for row in normalized: row['category']=row.get('category') or category
        return normalized

    def _source_mode(self,settings,run_id,existing):
        mode=settings['mode'];source=settings['source'];category=settings.get('category','');
        delay=float(settings.get('delay',1.5)); limit=max(1,min(int(settings.get('max_pages') or 100),3000))
        if mode=='alko_file':
            if source!='Alko':raise ValueError('Alko-hinnaston tiedostotuonti vain Alko-lähteelle')
            from .alko_v75 import rows_from_price_file, normalize_alko_row
            path=(self.workspace/'imports'/settings.get('filename','')).resolve()
            if path.parent!=(self.workspace/'imports').resolve() or not path.is_file():
                raise ValueError('Alko-hinnastotiedostoa ei löydy')
            originals=rows_from_price_file(path)
            self._update(total=len(originals))
            rows=[]
            for idx,r in enumerate(originals,1):
                item=normalize_alko_row(r,'operator-supplied Alko-style catalogue')
                if item:rows.append(item)
                if idx%500==0:self._update(processed=idx,found=len(rows))
            self._update(total=len(originals),processed=len(originals),found=len(rows))
            if not rows:raise ValueError('Tiedostosta ei löytynyt yhtään Kaljapin tuotekategoriaan kuuluvaa riviä')
            self._log(f'Alko-tiedosto: {len(originals)} riviä, joista {len(rows)} Kaljapin tarkistukseen')
            return rows
        if mode=='alko_mirror':
            from .alko_v75 import collect_mirror, MIRROR_URL
            if source!='Alko':raise ValueError('Alko-peili voidaan suorittaa vain Alko-lähteelle')
            self._log('Alko-peilin adapteri V75.1: sivutus alkaa page=0:sta')
            cache=self.workspace/'alko_mirror_checkpoint'
            def progress(page,pages,filtered,all_rows):
                self._update(total=pages,processed=page,pages=page,found=filtered)
                if page==1 or page%20==0 or page==pages:
                    self._log(f'Alko-peili: {page}/{pages} sivua, {all_rows} raakaa ja {filtered} Kaljapin ehdokasta')
            rows,meta=collect_mirror(self._fetch,cache,max_pages=limit,delay=max(1.5,delay),
                    stop=self.stop,progress=progress,resume=bool(settings.get('resume',False)))
            if not meta['complete']:
                raise ValueError(f'Osittainen aineisto ({meta["pages_done"]}/{meta["pages_total"]} sivua). '
                                 'Jatka keruuta Resume-asetuksella; julkaisuun ei tuoda osittaista luetteloa.')
            self._log(f'Koko peililuettelo kerätty: {meta["raw_rows"]} riviä, Kaljapin rajauksessa {len(rows)}')
            return rows
        if mode=='file':
            path=(self.workspace/'imports'/settings.get('filename','')).resolve()
            if path.parent!=(self.workspace/'imports').resolve() or not path.is_file():raise ValueError('Ladattu tiedosto ei löytynyt')
            rows=self._read_file_rows(path,source,category)
            self._update(total=len(rows),found=len(rows),processed=len(rows))
            self._log(f'Paikallinen tiedosto: {len(rows)} tuotetietuetta')
            return rows
        url=settings.get('url','').strip()
        if mode=='urls':
            url=next((u.strip() for u in str(settings.get('urls','')).splitlines() if u.strip()),'')
        safe_remote_url(url)
        if mode=='url_export':
            blob,headers,final=self._fetch(url,delay)
            suffix='.json' if 'json' in headers.get('Content-Type','').lower() or urllib.parse.urlparse(final).path.lower().endswith('.json') else '.csv'
            file=self.workspace/'imports'/f'web_{run_id}{suffix}'
            file.write_bytes(blob)
            rows=self._read_file_rows(file,source,category)
            self._update(pages=1,processed=len(rows),total=len(rows),found=len(rows))
            return rows
        if mode=='shopify':
            parsed=urllib.parse.urlparse(url)
            base=f'{parsed.scheme}://{parsed.netloc}'
            products=[];last_hash=None
            for page in range(1,limit+1):
                if self.stop.is_set():raise InterruptedError('Käyttäjä keskeytti')
                endpoint=f'{base}/products.json?limit=250&page={page}'
                data,_,_=self._fetch(endpoint,delay)
                signature=hashlib.sha256(data).hexdigest()
                if signature==last_hash:break
                last_hash=signature
                obj=json.loads(data)
                batch=obj.get('products') if isinstance(obj,dict) else None
                if not isinstance(batch,list):raise ValueError('Shopify /products.json ei palauttanut products-listaa')
                if not batch:break
                for product in batch:
                    if not isinstance(product,dict):continue
                    for variant in product.get('variants') or [{}]:
                        title=product.get('title','')
                        if variant.get('title') and variant['title']!='Default Title':title+=' – '+variant['title']
                        products.append({'name':title,'brand':product.get('vendor',''),'ean':variant.get('barcode',''),
                                         'category':product.get('product_type') or category,'product_url':f"{base}/products/{product.get('handle','')}",
                                         'source_product_id':str(product.get('id',''))+':'+str(variant.get('id','')),
                                         'units':'1','size_ml':'','shopify_variant':variant})
                self._update(pages=page,processed=page,found=len(products))
                self._log(f'Shopify-sivu {page}: yhteensä {len(products)} varianttia')
                if len(batch)<250: break
            return products
        if mode=='urls':
            urls=list(dict.fromkeys(x.strip() for x in (settings.get('urls') or '').splitlines() if x.strip()))
            if not urls:raise ValueError('Lisää vähintään yksi tuotelinkki')
            urls=urls[:limit]
        elif mode=='sitemap':
            urls=self._discover_sitemap(url,limit,delay)
            needle=settings.get('pattern','')
            if needle:
                try: reg=re.compile(needle,re.IGNORECASE)
                except re.error as e:raise ValueError('Virheellinen URL-suodatin: '+str(e))
                urls=[x for x in urls if reg.search(x)]
            urls=urls[:limit]
            self._log(f'Sivukartasta valittiin {len(urls)} tuotesivua')
        else:raise ValueError('Tuntematon keruutapa')
        self._update(total=len(urls))
        results=[];errors=0
        original_host=urllib.parse.urlparse(url).hostname
        for index,page_url in enumerate(urls,1):
            if self.stop.is_set():raise InterruptedError('Käyttäjä keskeytti')
            try:
                safe_remote_url(page_url)
                if original_host != urllib.parse.urlparse(page_url).hostname: raise ValueError('Ulkopuolinen verkkotunnus')
                blob,headers,final=self._fetch(page_url,delay)
                html=blob.decode('utf-8','replace')
                products=extract_product_html(html,final,category)
                results.extend(products)
                if not products: self._log('Tuoterakennetta ei löydy: '+page_url[:110])
            except Exception as e:
                errors+=1;self._log(f'Virhe [{index}/{len(urls)}]: {str(e)[:130]}')
            self._update(processed=index,pages=index,found=len(results),errors=errors)
            if index%10==0:self._log(f'Käsitelty {index}/{len(urls)} sivua, {len(results)} tuotetietuetta')
        if errors == len(urls) and urls: raise ValueError('Kaikki verkkopyynnöt epäonnistuivat. Katso keruuloki; tietoja ei tallennettu.')
        return results

    def _discover_sitemap(self,url,limit,delay):
        work=[url];seen=set();products=[];xml_pages=0
        while work and len(products)<MAX_LIST_URLS and xml_pages<min(100,limit):
            if self.stop.is_set():raise InterruptedError('Käyttäjä keskeytti')
            item=work.pop(0)
            if item in seen:continue
            seen.add(item)
            blob,headers,final=self._fetch(item,delay)
            if blob[:2]==b'\x1f\x8b':
                import gzip
                blob=gzip.decompress(blob)
            if len(blob)>MAX_RESPONSE_BYTES:raise ValueError('Liian suuri sitemap')
            is_index,locs=parse_sitemap(blob)
            xml_pages+=1
            root_host=urllib.parse.urlparse(url).hostname
            locs=[x for x in locs if urllib.parse.urlparse(x).hostname==root_host]
            if is_index:work.extend(locs)
            else:products.extend(locs)
            self._log(f'Sivukartta {xml_pages}: löydetty {len(products)} URLia')
        return list(dict.fromkeys(products))

    def start(self,settings):
        mode=settings.get('mode')
        if mode not in ('file','url_export','shopify','urls','sitemap','alko_file','alko_mirror'):raise ValueError('Tuntematon keruutapa')
        source=str(settings.get('source','')).strip()[:100]
        if not source:raise ValueError('Anna lähteen nimi')
        if mode in ('alko_mirror','alko_file') and source!='Alko':
            raise ValueError('Alko-hinnasto vain Alko-lähteelle')
        if mode=='urls':
            first=next((u.strip() for u in str(settings.get('urls','')).splitlines() if u.strip()),'')
            safe_remote_url(first,resolve=False)
        elif mode=='alko_mirror':
            from .alko_v75 import mirror_page_url
            mirror_page_url(1,str(settings.get('url','')).strip())
        elif mode not in ('file','alko_file'):
            safe_remote_url(str(settings.get('url','')).strip(),resolve=False)
        with self.lock:
            if self.worker and self.worker.is_alive():raise ValueError('Toinen keruu on vielä käynnissä')
            self.stop.clear()
            self.job={'state':'running','source':source,'mode':mode,'processed':0,'total':0,'found':0,'pages':0,'errors':0,'logs':[]}
            self.worker=threading.Thread(target=self._run,args=(dict(settings),),daemon=True)
            self.worker.start()
        return {'started':True}

    def cancel(self):
        self.stop.set();self._log('Keskeytyspyyntö vastaanotettu; nykyinen verkkopyyntö suoritetaan loppuun')
        return {'stopping':True}

    def _run(self,settings):
        source=settings['source'];mode=settings['mode'];category=canonical_category(settings.get('category',''))
        if mode=='alko_mirror':source='Alko-hinnastopeili (epävirallinen)'
        run_id=None;started=utcnow();existing=self._unique_preexisting()
        try:
            with self.connect() as db:
                cur=db.execute('INSERT INTO desktop_runs(started_at,source,mode,category,origin,state) VALUES(?,?,?,?,?,?)',
                               (started,source,mode,category,settings.get('url') or settings.get('filename',''),'running'))
                db.commit();run_id=cur.lastrowid
            self._log('Keruu käynnistetty: '+source+' ('+mode+')')
            rows=self._source_mode(settings,run_id,existing)
            if self.stop.is_set():raise InterruptedError('Käyttäjä keskeytti: keskeneräistä erää ei tuotu')
            result=self._store_rows(rows,source,category,run_id,existing)
            self._log(f"Tallennettu {result['rows']} lähdehavaintoa; uusia EAN-tunnisteita {result['new_eans']} (ei uusia julkaistuja juomia)")
            if result['skipped']:self._log('Identtinen erä oli jo tuotu; ei uusia rivejä')
            state='finished';self._update(state=state,found=result['rows'])
            info=json.dumps(result,ensure_ascii=False)
        except InterruptedError as e:
            state='cancelled';info=str(e);self._update(state=state);self._log(info)
        except Exception as e:
            state='error';info=str(e);self._update(state=state,errors=self.job['errors']+1);self._log('Keruu epäonnistui: '+info)
            # Preserve traceback locally for troubleshooting with Codex / Claude Code.
            try:
                tracefile=self.workspace/'runs'/f'error_run_{run_id or "startup"}.txt'
                tracefile.write_text(traceback.format_exc(),encoding='utf-8')
                self._log('Virheen tekninen raportti: '+str(tracefile))
            except OSError:
                pass
        if run_id is not None:
            with self.connect() as db:
                db.execute('UPDATE desktop_runs SET finished_at=?,state=?,pages=?,rows=?,newly_seen_eans=?,details=? WHERE id=?',
                           (utcnow(),state,self.job['pages'],self.job['found'],
                            result.get('new_eans',0) if state=='finished' else 0,info,run_id))
                db.commit()
        self._log('Erän lopputila: '+state)

    def export_csv(self,output):
        with self.connect() as db:return harvester.export_review(db,output)
