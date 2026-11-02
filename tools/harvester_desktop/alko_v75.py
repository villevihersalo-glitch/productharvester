"""Alko price-list intake (official-style CSV/XLSX or third-party paginated HTML).

The site niisku.lab.fi is NOT an official Alko feed. Access is gated by the
existing robots-aware fetcher, and rows remain unverified, review-only evidence.
No source Alko product number is a GTIN; never put it into `ean`.
"""
import csv
import io
import json
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, urlencode, urlunparse, parse_qsl

MIRROR_URL = 'https://niisku.lab.fi/~pelja/alko/index.php'
HEADERS = ['Numero','Nimi','Valmistaja','Pullokoko','Hinta','Litrahinta','Tyyppi',
           'Valmistusmaa','Vuosikerta','Alkoholi-%','Energia kcal/100 ml']


def fold(value):
    text = unicodedata.normalize('NFKD',str(value or '').casefold().strip())
    return ''.join(c for c in text if not unicodedata.combining(c))


def category_for(row):
    group=fold(row.get('Tyyppi','') or row.get('category',''))
    subtype=fold(row.get('Alatyyppi','') or row.get('subtype',''))
    name=fold(row.get('Nimi','') or row.get('name',''))
    both=f'{group} {subtype}'
    if 'hard seltzer' in both or 'seltzerit' in both:return 'seltzer'
    if 'siider' in both:return 'cider'
    if 'olue' in both or 'oluet' in both or group.strip() in ('beer','beers'):return 'beer'
    if 'lonkero' in both or 'long drink' in both:return 'longdrink'
    # Generic premixed drinks are NOT automatically lonkero/seltzer.
    if 'juomasekoit' in group:
        if 'lonkero' in name or 'long drink' in name:return 'longdrink'
        if 'hard seltzer' in name:return 'seltzer'
        return 'category_review'
    return ''


def volume_ml(value):
    match=re.search(r'(?<!\d)(\d+(?:[,.]\d+)?)\s*(ml|cl|l)\b',fold(value))
    if not match:return ''
    val=float(match.group(1).replace(',','.'))
    return str(round(val*{'l':1000,'cl':10,'ml':1}[match.group(2)]))


def units_for(name):
    text=fold(name)
    m=re.search(r'(?<!\d)(\d{1,2})\s*[x×]\s*\d+(?:[,.]\d+)?\s*(?:ml|cl|l)\b',text)
    if m:return m.group(1)
    m=re.search(r'(?<!\d)(\d{1,2})\s*[- ]?(?:pack|pakkaus|kpl|tölkin|pullon)\b',text)
    if m:return m.group(1)
    return '1'


def normalize_alko_row(r, provenance='Alko export', source_page=''):
    category=category_for(r)
    if not category:return None
    name=str(r.get('Nimi') or r.get('name') or '').strip()
    product_id=str(r.get('Numero') or r.get('source_product_id') or '').strip()
    if not name or not product_id:return None
    raw_ean=str(r.get('EAN') or r.get('ean') or '').strip()
    from tools.catalog_harvester.harvester import valid_ean
    ean=raw_ean if valid_ean(raw_ean) else ''
    return {'name':name,'brand':str(r.get('Valmistaja') or r.get('brand') or '').strip(),
            'source_product_id':product_id,'ean':ean,'ean_source':raw_ean,'category':category,
            'abv':str(r.get('Alkoholi-%') or r.get('abv') or '').replace(',','.').strip(),
            'size_ml':volume_ml(r.get('Pullokoko') or ''),'units':units_for(name),
            'product_url':str(r.get('Tuotesivu') or r.get('product_url') or '').strip(),
            'evidence':provenance,'source_page':source_page,'source_type':str(r.get('Tyyppi') or ''),
            'size_source':str(r.get('Pullokoko') or ''),'price_source':str(r.get('Hinta') or ''),
            'country_source':str(r.get('Valmistusmaa') or ''),
            'description_source':str(r.get('Luonnehdinta') or ''),
            'verification_status':'third_party_unverified' if 'third-party' in provenance else 'source_review_required',
            'availability_claim':'listed_in_price_list_not_live_store_stock'}


def rows_from_price_file(path):
    """Handle published Alko CSV/XLSX layouts including metadata above the header."""
    path=Path(path)
    if path.suffix.lower()=='.xlsx':
        return read_xlsx(path)
    if path.suffix.lower()!='.csv':raise ValueError('Alko-hinnasto: vain CSV tai XLSX')
    data=path.read_text(encoding='utf-8-sig')
    header_line=next((line for line in data.splitlines()[:20] if 'Numero' in line and 'Nimi' in line and 'Tyyppi' in line),'')
    delim=max((';',',','\t'),key=lambda x:header_line.count(x))
    records=list(csv.reader(io.StringIO(data),delimiter=delim))
    header_idx=next((i for i,row in enumerate(records[:20]) if 'Numero' in row and 'Nimi' in row and 'Tyyppi' in row),None)
    if header_idx is None:raise ValueError('Alko-hinnaston otsakkeita Numero / Nimi / Tyyppi ei löytynyt')
    headers=records[header_idx]
    return [dict(zip(headers,row)) for row in records[header_idx+1:] if any(c.strip() for c in row)]


def read_xlsx(path):
    """Dependency-free XLSX value reader for Excel-exported Alko price lists.

    This intentionally reads values, not formulas/macros. Bounded extraction and
    archive size checking reduce risks from malicious/unexpected workbooks.
    """
    NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    PKG = '{http://schemas.openxmlformats.org/package/2006/relationships}'
    with zipfile.ZipFile(path) as z:
        names=set(z.namelist())
        allowed=sum(i.file_size for i in z.infolist())
        if allowed>120*1024*1024:
            raise ValueError('XLSX:n purettu koko ylittää turvarajan (120 Mt)')
        if 'xl/workbook.xml' not in names:raise ValueError('Virheellinen XLSX: workbook puuttuu')
        ss=[]
        if 'xl/sharedStrings.xml' in names:
            root=ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in root.findall(NS+'si'):
                ss.append(''.join(t.text or '' for t in si.iter(NS+'t')))
        workbook=ET.fromstring(z.read('xl/workbook.xml'))
        sheets=workbook.find(NS+'sheets')
        if sheets is None or not list(sheets):raise ValueError('XLSX ei sisällä taulukoita')
        relationship_id=list(sheets)[0].get(REL+'id')
        relationships=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target=next((x.get('Target') for x in relationships if x.get('Id')==relationship_id),None)
        if not target:raise ValueError('XLSX taulukon kohde puuttuu')
        sheetname='xl/'+target.lstrip('/').removeprefix('xl/')
        if sheetname not in names:raise ValueError('XLSX taulukon data puuttuu')
        root=ET.fromstring(z.read(sheetname))
        sheetdata=root.find(NS+'sheetData')
        if sheetdata is None:raise ValueError('XLSX taulukko on tyhjä')
        values=[]
        for row in sheetdata.findall(NS+'row'):
            cells={}
            for c in row.findall(NS+'c'):
                ref=c.get('r','A1')
                letters=''.join(ch for ch in ref if ch.isalpha()).upper()
                col=0
                for ch in letters:col=col*26+(ord(ch)-64)
                val=c.find(NS+'v')
                if c.get('t')=='inlineStr':
                    inline=c.find(NS+'is')
                    text=''.join(x.text or '' for x in inline.iter(NS+'t')) if inline is not None else ''
                elif val is None:text=''
                elif c.get('t')=='s':text=ss[int(val.text)] if val.text else ''
                else:text=val.text or ''
                if col and col<=150:cells[col]=text
            if cells:values.append([cells.get(i,'') for i in range(1,max(cells)+1)])
            if len(values)>100000:raise ValueError('XLSX: liian monta riviä')
        header_index=next((i for i,row in enumerate(values[:20]) if {'Numero','Nimi','Tyyppi'}.issubset(row)),None)
        if header_index is None:raise ValueError('Alko XLSX: Numero/Nimi/Tyyppi-otsakkeita ei löydy')
        headers=values[header_index]
        output=[]
        for row in values[header_index+1:]:
            obj=dict(zip(headers,row))
            code=str(obj.get('Numero') or '')
            if code.isdigit() and len(code)<6:obj['Numero']=code.zfill(6)
            if any(str(x).strip() for x in row):output.append(obj)
        return output


class PriceTable(HTMLParser):
    def __init__(self):
        super().__init__();self.tables=[];self.current=None;self.row=None;self.cell=None;self.cell_depth=0
    def handle_starttag(self,tag,attrs):
        if tag=='table' and self.current is None:self.current=[]
        elif tag=='tr' and self.current is not None:self.row=[]
        elif tag in ('td','th') and self.row is not None:self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None and self.row is not None:
            self.row.append(' '.join(' '.join(self.cell).split()));self.cell=None
        elif tag=='tr' and self.row is not None and self.current is not None:
            self.current.append(self.row);self.row=None
        elif tag=='table' and self.current is not None:
            self.tables.append(self.current);self.current=None


class PageText(HTMLParser):
    """Visible text for metadata parsing (HTML markup may separate digits)."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'template'):
            self.hidden_depth += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'template') and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data):
        if not self.hidden_depth:
            self.parts.append(data)


def parse_mirror_page(html):
    parsed=PriceTable();parsed.feed(html)
    rows=[]
    for table in parsed.tables:
        if not table:continue
        for i,header in enumerate(table[:4]):
            if {'Numero','Nimi','Tyyppi'}.issubset(header):
                rows=[dict(zip(header,r)) for r in table[i+1:] if len(r)>=7]
                break
        if rows:break
    if not rows:
        raise ValueError('Hinnastosivun tuoterivejä ei löytynyt: mahdollinen estoviesti tai muuttunut sivurakenne')
    visible=PageText();visible.feed(html)
    text=' '.join(' '.join(visible.parts).split())
    # Require actual digits on BOTH sides; the V75 regex allowed whitespace-only
    # groups and raised int('') when the site used nested HTML elements.
    page_match=re.search(r'\bSivu\s+(\d+)\s*/\s*(\d+)\b',text,re.I)
    if not page_match:
        raise ValueError('Alko-peilin sivunumero puuttuu tai sivurakenne muuttui. Tallenna sivu ja tarkista adapteri.')
    page_no=int(page_match.group(1));pages=int(page_match.group(2))
    if page_no < 1 or pages < page_no:
        raise ValueError(f'Alko-peilin sivunumerointi on virheellinen: {page_no}/{pages}')
    total_match=re.search(r'Tuotteita\s+yhteens[aä]\s*:?\s*([0-9][0-9,.\s]*[0-9]|[0-9])\b',text,re.I)
    total=int(re.sub(r'\D','',total_match.group(1))) if total_match else None
    if total is None or total < len(rows):
        raise ValueError('Alko-peilin kokonaistuotemäärä puuttuu tai on virheellinen.')
    return rows,page_no,pages,total


def mirror_page_url(page,base=MIRROR_URL):
    parsed=urlparse(base)
    if parsed.hostname!='niisku.lab.fi' or parsed.path!='/~pelja/alko/index.php' or parsed.scheme!='https':
        raise ValueError('Epävirallisen Alko-hinnastopeilin osoite ei vastaa hyväksyttyä sivustoa')
    # Ignore source URL's query to prevent accidentally inheriting filters.
    if not isinstance(page, int) or page < 1:
        raise ValueError('Sivunumeron tulee olla positiivinen kokonaisluku')
    # The mirror's page parameter is zero-based: page=0 is displayed as Sivu 1.
    return urlunparse((parsed.scheme,parsed.netloc,parsed.path,'',urlencode({'page':page-1}),''))


def collect_mirror(fetch,folder,max_pages=600,delay=2.0,stop=None,progress=None,resume=False):
    """Crawl page-by-page using existing robots-aware fetch(). Store checkpoints.

    fetch(url,delay) -> (bytes, headers, final_url). Stop/403/429 don't bypass.
    A partial crawl is NOT presented as a full catalogue and cannot auto-publish.
    """
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    if not resume:
        for old in folder.glob('page_*.json'):old.unlink()
    all_rows=[];raw_count=0;page=1;total_pages=None;expected_total=None
    while page<=max_pages:
        if stop is not None and stop.is_set():raise InterruptedError('Alko-keruu keskeytetty; välitulokset tallessa')
        cache=folder/f'page_{page:04d}.json'
        if resume and cache.is_file():
            data=json.loads(cache.read_text(encoding='utf-8'))
            rows=data['rows'];page_no=data.get('page_no');pages=data['pages'];total=data.get('total')
        else:
            blob,headers,final=fetch(mirror_page_url(page),delay)
            rows,page_no,pages,total=parse_mirror_page(blob.decode('utf-8-sig','replace'))
            if page_no!=page:raise ValueError(f'Peilisivu ei vastaa pyydettyä sivua: {page} vs {page_no}')
            cache.write_text(json.dumps({'rows':rows,'page_no':page_no,'pages':pages,'total':total},ensure_ascii=False),encoding='utf-8')
        if page_no!=page:raise ValueError(f'Välimuistin sivunumero ei täsmää: {page}')
        total_pages=pages or total_pages
        expected_total=total or expected_total
        if total_pages is None:raise ValueError('Sivumäärää ei voitu vahvistaa; ei voi väittää kokonaista keruuta')
        if page>total_pages:break
        raw_count+=len(rows)
        for row in rows:
            item=normalize_alko_row(row,'third-party Alko-style price mirror',mirror_page_url(page))
            if item:all_rows.append(item)
        if progress:progress(page,total_pages,len(all_rows),raw_count)
        if page>=total_pages or page>=max_pages:break
        page+=1
    complete=page>=total_pages and total_pages is not None and all((folder/f'page_{i:04d}.json').is_file() for i in range(1,total_pages+1))
    # Every original list row is checked for per-page completeness, not just scoped rows.
    if complete and expected_total and raw_count!=expected_total:
        raise ValueError(f'Koko luettelon rivimäärä ei täsmää: {raw_count} vs {expected_total}')
    by_id={}
    for r in all_rows:
        key=r['source_product_id']
        if key in by_id and by_id[key]['name']!=r['name']:
            raise ValueError('Eri tuotteita samalla Alko-tunnuksella: '+key)
        by_id[key]=r
    return list(by_id.values()),{'complete':bool(complete),'pages_done':page,'pages_total':total_pages,
                                 'raw_rows':raw_count,'target_rows':len(by_id),'expected_rows':expected_total,
                                 'status':'complete' if complete else 'partial',
                                 'source':'unofficial-third-party-price-mirror','no_automatic_publication':True}
