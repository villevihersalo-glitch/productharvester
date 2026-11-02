import csv
import tempfile
import threading
import unittest
from pathlib import Path
from tools.harvester_desktop.alko_v75 import (category_for,normalize_alko_row,rows_from_price_file,
    parse_mirror_page,collect_mirror,mirror_page_url)
from tools.harvester_desktop.engine import HarvesterDesktop
from tools.harvester_desktop.workflow_v74 import validate_job


def page(n=1,pages=2,total=3,rows=None):
    rows=rows or [('000321','Hieno Lager 5,0 %','Test','0,5 l','2,80','5,60','oluet','Suomi','','5,0','44')]
    trs=''.join('<tr>'+''.join(f'<td>{v}</td>' for v in r)+'</tr>' for r in rows)
    return ('<html><p>Tuotteita yhteensä: '+str(total)+'</p>'
            '<table><thead><tr>'+''.join('<th>'+x+'</th>' for x in ['Numero','Nimi','Valmistaja','Pullokoko','Hinta','Litrahinta','Tyyppi','Valmistusmaa','Vuosikerta','Alkoholi-%','Energia kcal/100 ml'])+
            '</tr></thead><tbody>'+trs+f'</tbody></table><p>Sivu {n} / {pages} (25 tuotetta/sivu)</p></html>')

class AlkoV75Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
    def test_categories_and_ambiguity(self):
        self.assertEqual(category_for({'Tyyppi':'oluet'}),'beer')
        self.assertEqual(category_for({'Tyyppi':'siiderit'}),'cider')
        self.assertEqual(category_for({'Tyyppi':'lonkerot'}),'longdrink')
        self.assertEqual(category_for({'Tyyppi':'hard seltzerit'}),'seltzer')
        self.assertEqual(category_for({'Tyyppi':'juomasekoitukset','Nimi':'Something tropical'}),'category_review')
        self.assertEqual(category_for({'Tyyppi':'punaviinit'}),'')
    def test_no_fake_ean_and_multipack(self):
        row={'Numero':'000321','Nimi':'Test IPA 6 x 0,33 l','Tyyppi':'oluet','Pullokoko':'0,33 l','Alkoholi-%':'5,3','Valmistaja':'Test'}
        result=normalize_alko_row(row,'third-party Alko-style price mirror','https://example.com')
        self.assertEqual(result['ean'],'');self.assertEqual(result['source_product_id'],'000321')
        self.assertEqual(result['units'],'6');self.assertEqual(result['category'],'beer')
        self.assertEqual(result['verification_status'],'third_party_unverified')
    def test_csv_metadata_header(self):
        p=self.folder/'alko.csv'
        p.write_text('Hinnasto 2026\nRaportti\nNumero;Nimi;Valmistaja;Tyyppi;Alkoholi-%\n000321;Test Ale;Brew;oluet;5,5\n000322;Other Wine;Wine;punaviinit;12,0\n',encoding='utf-8')
        parsed=rows_from_price_file(p)
        self.assertEqual(len(parsed),2)
        self.assertEqual(normalize_alko_row(parsed[0])['abv'],'5.5')
    def test_html_parser_and_full_2_pages(self):
        p1=page(1,2,3,rows=[('000321','Test Beer','Brew','0,5 l','3','6','oluet','Suomi','','5','45'),('000322','Red Wine','Winery','0,75 l','10','13','punaviinit','Ranska','','12','70')])
        p2=page(2,2,3,rows=[('000323','Test Dry Cider','Orchard','0,33 l','4','12','siiderit','Englanti','','6','50')])
        def fetch(url,delay):
            n=int(url.rsplit('=',1)[-1]);return (p1 if n==0 else p2).encode(),{},url
        rows,summary=collect_mirror(fetch,self.folder/'cache',max_pages=5,delay=2)
        self.assertEqual(summary['raw_rows'],3)
        self.assertTrue(summary['complete'])
        self.assertEqual(len(rows),2)
        self.assertEqual({r['category'] for r in rows},{'beer','cider'})
        self.assertEqual(len(list((self.folder/'cache').glob('page_*.json'))),2)
        def no_network(url,delay):raise AssertionError('Cached pages should be used')
        rows_again,summary2=collect_mirror(no_network,self.folder/'cache',max_pages=5,resume=True)
        self.assertEqual(rows_again,rows)
    def test_incomplete_crawl_not_reported_complete(self):
        def fetch(url,delay):return page(1,3,3).encode(),{},url
        rows,summary=collect_mirror(fetch,self.folder/'cache',max_pages=1)
        self.assertFalse(summary['complete']);self.assertEqual(summary['pages_done'],1)
    def test_zero_based_pagination_works_on_real_site(self):
        self.assertEqual(mirror_page_url(1), 'https://niisku.lab.fi/~pelja/alko/index.php?page=0')
        self.assertEqual(mirror_page_url(2), 'https://niisku.lab.fi/~pelja/alko/index.php?page=1')

    def test_markup_wrapped_counts_and_thousands(self):
        html=page(1,477,11912).replace('Sivu 1 / 477',
            'Sivu <strong>1</strong> / <span>477</span>').replace(
            'Tuotteita yhteensä: 11912','Tuotteita yhteensä: <b>11,912</b>')
        rows,n,pages,total=parse_mirror_page(html)
        self.assertEqual((n,pages,total),(1,477,11912))
        self.assertEqual(len(rows),1)

    def test_blank_pagination_gives_clear_error_not_int_conversion(self):
        html=page().replace('Sivu 1 / 2','Sivu / 2')
        with self.assertRaisesRegex(ValueError,'sivunumero puuttuu'):
            parse_mirror_page(html)
        html=page().replace('Tuotteita yhteensä: 3','Tuotteita yhteensä: <b></b>')
        with self.assertRaisesRegex(ValueError,'kokonaistuotemäärä puuttuu'):
            parse_mirror_page(html)

    def test_headerless_content_length_works(self):
        # A server may stream chunked HTML without any Content-Length header.
        from unittest.mock import patch
        import io
        from tools.harvester_desktop.engine import HarvesterDesktop
        root=self.folder/'http_root';root.mkdir()
        engine=HarvesterDesktop(root)
        class Reply:
            headers={'Content-Length': ''}
            url='https://niisku.lab.fi/~pelja/alko/index.php?page=0'
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,cap):return b'yes'
        class Opener:
            def open(self,request,timeout):return Reply()
        with patch.object(engine,'_robots_allows',return_value=True),patch('tools.harvester_desktop.engine.safe_remote_url',return_value=Reply.url),patch('urllib.request.build_opener',return_value=Opener()):
            data,headers,url=engine._fetch(Reply.url,delay=0)
        self.assertEqual(data,b'yes')

    def test_safe_url_host(self):
        with self.assertRaises(ValueError):mirror_page_url(1,'https://example.com/index.php')
    def test_integration_file_no_publish(self):
        root=self.folder/'project';root.mkdir()
        svc=HarvesterDesktop(root)
        p=svc.workspace/'imports'/'alko.csv'
        p.write_text('Numero;Nimi;Valmistaja;Tyyppi;Alkoholi-%\n000321;Test Ale;Brew;oluet;5,5\n000322;Test Wine;Wine;punaviinit;12\n')
        settings={'source':'Alko','mode':'alko_file','filename':'alko.csv','max_pages':600,'delay':2}
        self.assertEqual(validate_job(settings)[0]['mode'],'alko_file')
        svc.start(settings);svc.worker.join(12)
        stat=svc.status();self.assertEqual(stat['job']['state'],'finished')
        self.assertEqual(stat['totals']['raw'],1)
        self.assertEqual(stat['totals']['catalog'],0)
        self.assertEqual(stat['recent_products'][0]['ean'],'')
        with svc.connect() as db:
            details=db.execute('SELECT raw_json FROM observations LIMIT 1').fetchone()[0]
            self.assertIn('source_product_id',details)
    def test_mirror_mode_end_to_end_into_raw_registry(self):
        root=self.folder/'mirror_project';root.mkdir()
        svc=HarvesterDesktop(root)
        p1=page(1,2,2)
        p2=page(2,2,2,rows=[('000555','Mock Cider','Test Orchard','0,33 l','3','9','siiderit','Suomi','','4,5','45')])
        requested=[]
        def fake_fetch(url,delay):
            requested.append(url)
            number=int(url.rsplit('=',1)[-1])
            return (p1 if number==0 else p2).encode(),{},url
        svc._fetch=fake_fetch
        svc.start({'source':'Alko','mode':'alko_mirror','url':'https://niisku.lab.fi/~pelja/alko/index.php','max_pages':5,'delay':2,'resume':False})
        svc.worker.join(12)
        stat=svc.status()
        self.assertEqual(stat['job']['state'],'finished',stat['job']['logs'])
        self.assertEqual(stat['job']['pages'],2)
        self.assertEqual(stat['totals']['raw'],2)
        self.assertTrue(requested[0].endswith('page=0'))
        self.assertTrue(requested[1].endswith('page=1'))
        with svc.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM observations").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM desktop_runs WHERE state='finished'").fetchone()[0],1)

    def test_queue_mirror_settings(self):
        settings={'source':'Alko','mode':'alko_mirror','url':'https://niisku.lab.fi/~pelja/alko/index.php','max_pages':600,'delay':2,'resume':True}
        config,interval,due=validate_job(settings)
        self.assertTrue(config['resume'])
        with self.assertRaises(ValueError):validate_job({**settings,'source':'Other'})

if __name__=='__main__':unittest.main()

class XLSXBasicTests(unittest.TestCase):
    def test_stdlib_xlsx_reader_keeps_alko_ids(self):
        import zipfile
        from tools.harvester_desktop.alko_v75 import rows_from_price_file
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'hinnasto.xlsx'
            ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
            row=lambda n,cols:'<row r="'+str(n)+'">'+''.join(f'<c r="{letter}{n}" t="inlineStr"><is><t>{v}</t></is></c>' for letter,v in cols)+'</row>'
            sheet='<worksheet xmlns="'+ns+'"><sheetData>'+row(1,[('A','Numero'),('B','Nimi'),('C','Tyyppi')])+row(2,[('A','000123'),('B','Savu IPA'),('C','oluet')])+'</sheetData></worksheet>'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('xl/workbook.xml','<workbook xmlns="'+ns+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Hinnasto" sheetId="1" r:id="rId1"/></sheets></workbook>')
                z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" /></Relationships>')
                z.writestr('xl/worksheets/sheet1.xml',sheet)
            rows=rows_from_price_file(path)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['Numero'],'000123')
