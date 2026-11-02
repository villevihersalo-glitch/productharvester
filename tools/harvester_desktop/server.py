"""Local web control panel (127.0.0.1 only), no external UI dependencies."""
import io
import json
import mimetypes
import re
import shutil
import tempfile
import threading
import uuid
import webbrowser
from email.parser import BytesParser
from email import policy
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .engine import HarvesterDesktop
from .workflow_v74 import QueueManager, export_target, historical_inventory

MAX_UPLOAD = 35*1024*1024

class PanelHandler(BaseHTTPRequestHandler):
    server_version='KaljappiHarvester/1.0'
    def log_message(self, format, *args):
        pass

    @property
    def svc(self): return self.server.service

    def _reply(self,data,code=200,ctype='application/json; charset=utf-8',disposition=None):
        if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode('utf-8')
        elif isinstance(data,str):data=data.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        if disposition:self.send_header('Content-Disposition',disposition)
        self.end_headers();self.wfile.write(data)

    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/api/status':
            result=self.svc.status();result.update(self.server.queue_manager.status());return self._reply(result)
        if path=='/api/inventory.csv':
            with tempfile.TemporaryDirectory() as folder:
                dest=Path(folder)/'historical_urls.csv'
                historical_inventory(self.svc,dest)
                return self._reply(dest.read_bytes(),ctype='text/csv; charset=utf-8',disposition='attachment; filename="historical_urls.csv"')
        if path=='/api/sources.csv':
            import csv
            with tempfile.TemporaryDirectory() as folder:
                dest=Path(folder)/'source_catalog.csv'
                with dest.open('w',encoding='utf-8-sig',newline='') as f:
                    w=csv.DictWriter(f,fieldnames=['id','merchant','category','url','mode','state','note','evidence','updated_at'])
                    w.writeheader();w.writerows(self.server.queue_manager.status()['sources'])
                return self._reply(dest.read_bytes(),ctype='text/csv; charset=utf-8',disposition='attachment; filename="source_catalog.csv"')
        if path.startswith('/api/export/target'):
            from urllib.parse import parse_qs
            query=parse_qs(urlsplit(self.path).query)
            target=query.get('target',['generic'])[0]
            selection=query.get('selection',['review'])[0]
            result=export_target(self.svc,target,selection)
            return self._reply(Path(result['file']).read_bytes(),ctype='text/csv; charset=utf-8',
                disposition=f'attachment; filename="{target}_{selection}_review.csv"')
        if path=='/api/queue/template.csv':
            return self._reply('source,category,mode,url,pattern,max_pages,delay,interval_hours,due_at,label\n',
                ctype='text/csv; charset=utf-8',disposition='attachment; filename="queue_template.csv"')
        if path=='/api/export.csv':
            with tempfile.TemporaryDirectory() as folder:
                dest=Path(folder)/'review.csv'
                self.svc.export_csv(dest)
                return self._reply(dest.read_bytes(),ctype='text/csv; charset=utf-8',disposition='attachment; filename="product-review.csv"')
        static={'/':'dashboard.html','/dashboard.css':'dashboard.css','/dashboard.js':'dashboard.js'}
        filename=static.get(path)
        if filename:
            dest=self.svc.root/'harvester_ui'/filename
            if dest.is_file():return self._reply(dest.read_bytes(),ctype={'dashboard.html':'text/html; charset=utf-8','dashboard.js':'application/javascript; charset=utf-8','dashboard.css':'text/css; charset=utf-8'}[filename])
        return self._reply({'error':'Not found'},404)

    def _require_local_ajax(self):
        if self.headers.get('X-Requested-With')!='KaljappiHarvester':raise ValueError('Invalid local request token')
        origin=self.headers.get('Origin','')
        expected=f'http://127.0.0.1:{self.server.server_port}'
        if origin and origin!=expected:raise ValueError('Cross-origin requests are not allowed')

    def do_POST(self):
        path=urlsplit(self.path).path
        try:
            self._require_local_ajax()
            length=int(self.headers.get('Content-Length','0'))
            if length<0 or length>MAX_UPLOAD:raise ValueError('Upload limit is 35 MB')
            body=self.rfile.read(length)
            if path=='/api/upload':
                typ=self.headers.get('Content-Type','')
                if 'multipart/form-data' not in typ:raise ValueError('Expected multipart/form-data')
                envelope=(f'Content-Type: {typ}\r\nMIME-Version: 1.0\r\n\r\n').encode()+body
                msg=BytesParser(policy=policy.default).parsebytes(envelope)
                parts=[p for p in msg.iter_parts() if p.get_param('name',header='Content-Disposition')=='file']
                if len(parts)!=1:raise ValueError('Expected one file')
                item=parts[0];original=item.get_filename() or ''
                ext=Path(original).suffix.lower()
                if ext not in ('.csv','.json','.xlsx'):raise ValueError('Only CSV, JSON and XLSX files are accepted')
                raw=item.get_payload(decode=True)
                if len(raw)>MAX_UPLOAD:raise ValueError('File too large')
                filename=f'upload_{uuid.uuid4().hex}{ext}'
                (self.svc.workspace/'imports'/filename).write_bytes(raw)
                return self._reply({'filename':filename,'original':Path(original).name,'bytes':len(raw)})
            if path=='/api/queue/import':
                data=json.loads(body or b'{}')
                filename=str(data.get('filename',''))
                if Path(filename).name != filename or not filename.endswith('.csv'):
                    raise ValueError('Valitse CSV-tiedosto')
                dest=(self.svc.workspace/'imports'/filename).resolve()
                if dest.parent!=(self.svc.workspace/'imports').resolve() or not dest.is_file():
                    raise ValueError('Tiedostoa ei löytynyt')
                return self._reply(self.server.queue_manager.enqueue_csv(dest.read_bytes()))
            if path.startswith('/api/queue/'):
                data=json.loads(body or b'{}')
                qm=self.server.queue_manager
                if path=='/api/queue/add':return self._reply(qm.enqueue(data.get('jobs',[])))
                if path=='/api/queue/run':return self._reply(qm.run_now(data.get('ids',[])))
                if path=='/api/queue/remove':return self._reply(qm.remove(data.get('ids',[])))
                if path=='/api/queue/toggle':return self._reply(qm.toggle(data['id'],data['enabled']))
                return self._reply({'error':'Not found'},404)
            if path not in ('/api/start','/api/stop'):return self._reply({'error':'Not found'},404)
            data=json.loads(body or b'{}')
            if path=='/api/start':return self._reply(self.svc.start(data))
            return self._reply(self.svc.cancel())
        except Exception as exc:return self._reply({'error':str(exc)},400)


def serve(root,port=8765,open_browser=True):
    svc=HarvesterDesktop(root)
    try: server=ThreadingHTTPServer(('127.0.0.1',port),PanelHandler)
    except OSError:
        if port!=8765:raise
        server=ThreadingHTTPServer(('127.0.0.1',0),PanelHandler)
    server.service=svc
    server.queue_manager=QueueManager(svc)
    server.queue_manager.start()
    url=f'http://127.0.0.1:{server.server_port}/'
    print('Product Harvester käynnissä / running: '+url,flush=True)
    print('Pysäytä ohjelma: Ctrl+C. Tiedot säilyvät SQLite-tietokannassa.',flush=True)
    if open_browser:
        threading.Timer(0.6,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:
        svc.cancel()
    finally:
        server.queue_manager.stop()
        server.server_close()
