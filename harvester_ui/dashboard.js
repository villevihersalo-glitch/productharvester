'use strict';
const $=id=>document.getElementById(id);
const num=n=>Number(n||0).toLocaleString('fi-FI');
const ST={idle:'VALMIS ALOITTAMAAN',running:'KÄYNNISSÄ',finished:'VALMIS',cancelled:'KESKEYTETTY',error:'VIRHE'};
const MODES={file:'Tiedostotuonti',url_export:'CSV/JSON-lataus',sitemap:'Sitemap / tuotesivut',urls:'Tuotesivulista',shopify:'Shopify JSON',alko_file:'Alko CSV/XLSX',alko_mirror:'Alkon hinnastopeili (epävirallinen)'};
let lastLog=-1;
function el(tag,cls,text){const node=document.createElement(tag); if(cls)node.className=cls;if(text!==undefined)node.textContent=String(text);return node;}
async function req(url,opt={}){const res=await fetch(url,{...opt,headers:{...(opt.headers||{}),'X-Requested-With':'KaljappiHarvester'}});const json=await res.json();if(!res.ok)throw new Error(json.error||`HTTP ${res.status}`);return json;}
function selectMode(){
 const mode=$('mode').value;
 $('fileField').hidden=!['file','alko_file'].includes(mode);$('urlField').hidden=!['url_export','sitemap','shopify','alko_mirror'].includes(mode);
 $('urlsField').hidden=mode!=='urls';$('patternField').hidden=mode!=='sitemap';
 $('maxPages').closest('label').hidden=['file','alko_file','url_export'].includes(mode);
 $('delay').closest('label').hidden=['file','alko_file'].includes(mode);
 $('urlLabel').firstChild.textContent=mode==='alko_mirror'?'Alkon kolmannen osapuolen hinnastopeilin URL ':mode==='shopify'?'Shopify-kaupan osoite (HTTPS) ':mode==='sitemap'?'Sitemap XML -osoite (HTTPS) ':'Julkinen CSV / JSON -osoite (HTTPS) ';
 $('url').placeholder=mode==='alko_mirror'?'https://niisku.lab.fi/~pelja/alko/index.php':mode==='shopify'?'https://oma-kauppa.myshopify.com':mode==='sitemap'?'https://esimerkki.fi/sitemap.xml':'https://esimerkki.fi/products.csv';
 if(mode==='alko_mirror'){$('source').value='Alko';$('url').value='https://niisku.lab.fi/~pelja/alko/index.php';$('maxPages').value=600;$('delay').value=2;}
 $('file').accept=mode==='alko_file'?'.csv,.xlsx':'.csv,.json';
 $('alkoNotice').hidden=mode!=='alko_mirror';$('resumeField').hidden=mode!=='alko_mirror';
}
function feedback(message,error=false){$('feedback').textContent=message;$('feedback').className='feedback'+(error?' error':'');}
function drawTable(id,rows,empty){const body=$(id);body.replaceChildren();if(!rows.length){let tr=el('tr');let td=el('td','',empty);td.colSpan=id==='runs'?6:4;tr.append(td);body.append(tr);return;}
 for(const row of rows){let tr=el('tr');for(let i=0;i<row.length;i++){const td=el('td',i===2&&id==='products'?'code':'');const value=row[i];if(id==='runs'&&i===2){td.append(el('span','tag '+String(value).toLowerCase(),ST[value]||value));}else if(id==='products'&&i===3){td.append(el('span','tag',value));}else td.textContent=value;tr.append(td);}body.append(tr);}}
function show(data){
 drawSourceRegistry(data.sources||[]);drawQueue(data.queue||[]);
 const t=data.totals,j=data.job;
 $('totalCatalog').textContent=num(t.catalog);$('totalRaw').textContent=num(t.raw);$('totalEan').textContent=num(t.unique_eans);$('totalNew').textContent=num(t.unmatched_eans);
 $('connection').textContent='Palvelin käytettävissä';
 $('reviewCount').textContent=num(t.review_rows)+' TARKISTETTAVAA HAVAINTOA';
 const running=j.state==='running';$('startButton').disabled=running;$('stopButton').disabled=!running;
 $('jobState').textContent=ST[j.state]||j.state.toUpperCase();$('jobState').className='status-pill '+j.state;
 $('jobName').textContent=j.source||'Ei käynnissä olevaa työtä';$('jobMode').textContent=MODES[j.mode]||'—';
 $('processed').textContent=num(j.processed);$('found').textContent=num(j.found);$('errors').textContent=num(j.errors);
 const percent=j.total?Math.round(j.processed/j.total*100):0;
 $('percent').textContent=j.total?percent+' %':running?'Käsitellään…':'0 %';$('progressBar').style.width=(j.total?Math.min(100,percent):0)+'%';
 if(j.logs.length!==lastLog){const logs=$('logs');logs.replaceChildren();if(!j.logs.length){logs.append(el('div','console-empty','Keruuloki ilmestyy tänne käynnistyksen jälkeen.'));}else{for(const item of j.logs){let line=el('div','console-entry');line.append(el('time','',item.time),el('span','',item.text));logs.append(line);}logs.scrollTop=logs.scrollHeight;}lastLog=j.logs.length;}
 drawTable('runs',data.runs.map(r=>[r.source,MODES[r.mode]||r.mode,r.state,num(r.rows),num(r.new_eans),(r.started||'').replace('T',' ').slice(0,16)]),'Ei vielä uusia keruuajoja.');
 drawTable('products',data.recent_products.map(p=>[p.name||'Nimi puuttuu',p.source||'—',p.ean||'—',p.status||'—']),'Ei havaintoja.');
}
async function refresh(){try{show(await req('/api/status'));}catch(e){$('connection').textContent='Ei yhteyttä palvelimeen';}}
$('mode').addEventListener('change',selectMode);
$('source').addEventListener('change',()=>{$('customSourceField').hidden=$('source').value!=='custom'});
async function buildConfig(){
 feedback('');let source=$('source').value==='custom'?$('customSource').value.trim():$('source').value;
 if(!source)throw new Error('Anna lähteen nimi.');
 const mode=$('mode').value;
 const cfg={source,mode,category:$('category').value,url:$('url').value.trim(),urls:$('urls').value,
 pattern:$('pattern').value.trim(),max_pages:Number($('maxPages').value),delay:Number($('delay').value),resume:$('resumeMirror').checked};
 try{
  if(mode==='file'||mode==='alko_file'){
   const file=$('file').files[0];if(!file)throw new Error('Valitse CSV-, JSON- tai XLSX-tiedosto.');
   feedback('Siirretään tiedostoa paikalliselle ohjelmalle…');
   const data=new FormData();data.append('file',file);
   const result=await req('/api/upload',{method:'POST',body:data});cfg.filename=result.filename;
  }
  return cfg;
 }catch(err){throw err;}
}
$('collectForm').addEventListener('submit',async e=>{
 e.preventDefault();try{const cfg=await buildConfig();
  await post('/api/start',cfg);
  feedback('Keruu käynnistetty.');await refresh();
 }catch(err){feedback(err.message,true);}
});
$('queueButton').addEventListener('click',async()=>{
 try{const cfg=await buildConfig();cfg.label=cfg.source+' / '+(cfg.category||'kaikki');
  cfg.interval_hours=Number($('repeatHours').value)||0;
  if($('startAt').value)cfg.due_at=new Date($('startAt').value).toISOString();
  const r=await post('/api/queue/add',{jobs:[cfg]});feedback('Lisätty jonoon: #'+r.queued.join(','));await refresh();
 }catch(e){feedback(e.message,true);}
});
async function post(url,data){return req(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});}
function drawSourceRegistry(rows){const t=$('sourceRows');t.replaceChildren();if(!rows.length){const x=el('tr');const c=el('td','','Lähteitä ei vielä ole');c.colSpan=5;x.append(c);t.append(x);return;}
 for(const item of rows){const r=el('tr');r.append(el('td','',item.merchant),el('td','',item.category));
  const td=el('td');const a=el('a','link',item.url);a.href=item.url;a.target='_blank';a.rel='noopener noreferrer';td.append(a);r.append(td);
  r.append(el('td','',item.mode==='requires_adapter'?'Ei suoraan ajettavissa':item.state));r.append(el('td','',item.note));t.append(r);
 }}
function drawQueue(rows){const t=$('queueRows');const chosen=new Set([...t.querySelectorAll('.job-select:checked')].map(x=>x.value));t.replaceChildren();$('queueCount').textContent=rows.length+' TYÖTÄ';
  if(!rows.length){const r=el('tr'),c=el('td','','Jono on tyhjä.');c.colSpan=8;r.append(c);t.append(r);return;}
  for(const x of rows){const tr=el('tr'),td=el('td');const cb=el('input');cb.type='checkbox';cb.className='job-select';cb.value=x.id;cb.checked=chosen.has(String(x.id));td.append(cb);tr.append(td);
   tr.append(el('td','',x.label),el('td','',MODES[x.settings.mode]||x.settings.mode),el('td','',x.state+(x.enabled?'':' (pois)')),
    el('td','',x.due_at.replace('T',' ').slice(0,16)),el('td','',x.interval_hours?x.interval_hours+' h':'Kerran'),el('td','',x.attempts));const action=el('td');if(x.state!=='running'){const b=el('button','small-toggle',x.enabled?'Tauko':'Aktivoi');b.type='button';b.onclick=async()=>{try{await post('/api/queue/toggle',{id:x.id,enabled:!x.enabled});await refresh();}catch(e){$('queueFeedback').textContent=e.message;}};action.append(b);}tr.append(action);t.append(tr);
  }
}
function selectedIds(){return [...document.querySelectorAll('.job-select:checked')].map(x=>Number(x.value));}
$('runSelected').addEventListener('click',async()=>{try{const r=await post('/api/queue/run',{ids:selectedIds()});$('queueFeedback').textContent='Jonoon aktivoitu '+r.queued+' työtä';await refresh();}catch(e){$('queueFeedback').textContent=e.message;}});
$('removeSelected').addEventListener('click',async()=>{try{const r=await post('/api/queue/remove',{ids:selectedIds()});$('queueFeedback').textContent='Poistettu '+r.removed+' työtä';await refresh();}catch(e){$('queueFeedback').textContent=e.message;}});
$('queueFile').addEventListener('change',async()=>{try{const file=$('queueFile').files[0];if(!file)return;const fd=new FormData();fd.append('file',file);const upl=await req('/api/upload',{method:'POST',body:fd});const r=await post('/api/queue/import',{filename:upl.filename});$('queueFeedback').textContent=`Lisätty ${r.queued.length} työtä; ohitettu ${r.skipped.length} epäkelpoa riviä`;await refresh();}catch(e){$('queueFeedback').textContent=e.message;}});
$('exportTargetButton').addEventListener('click',()=>{const target=$('exportTarget').value,selection=$('exportSelection').value;window.location.href='/api/export/target?target='+encodeURIComponent(target)+'&selection='+encodeURIComponent(selection);});
$('stopButton').addEventListener('click',async()=>{try{await req('/api/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});feedback('Keskeytyspyyntö lähetetty.');}catch(e){feedback(e.message,true);}});
selectMode();refresh();setInterval(refresh,1600);
