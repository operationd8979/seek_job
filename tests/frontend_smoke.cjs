// Offline browser regression: real DOM/events, synthetic data, no agents or network fetches.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn, spawnSync} = require('node:child_process');
const edge = process.env.EDGE_BIN || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'seek-job-browser-test-'));
const browser = spawn(edge, ['--headless=new', '--disable-gpu', '--no-first-run',
  '--disable-background-networking', '--remote-debugging-port=0', '--user-data-dir='+temp, 'about:blank'],
  {windowsHide:true, stdio:'ignore'});
const delay = ms => new Promise(r=>setTimeout(r,ms));
let ws;
(async()=>{
  const portFile=path.join(temp,'DevToolsActivePort');
  for(let i=0;i<100&&!fs.existsSync(portFile);i++)await delay(100);
  const [port,endpoint]=fs.readFileSync(portFile,'utf8').trim().split(/\r?\n/);
  ws=new WebSocket('ws://127.0.0.1:'+port+endpoint);
  await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  let id=0;const pending=new Map();
  ws.onmessage=e=>{const m=JSON.parse(e.data);if(pending.has(m.id)){const [ok,fail]=pending.get(m.id);pending.delete(m.id);m.error?fail(Error(m.error.message)):ok(m.result);}};
  function send(method,params={},sessionId){return new Promise((ok,fail)=>{const n=++id;pending.set(n,[ok,fail]);ws.send(JSON.stringify({id:n,method,params,sessionId}));});}
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});
  const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
  const run=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},sessionId);if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;};
  const assets=path.resolve(__dirname,'../seek_job/web_assets');
  const html=fs.readFileSync(path.join(assets,'index.html'),'utf8').replace(/<script[^>]*src[^>]*><\/script>/g,'').replace(/<link[^>]+>/g,'');
  await run('document.documentElement.innerHTML='+JSON.stringify(html));
  await run(`
    window.assert=(value,message)=>{if(!value)throw Error(message);};
    window.calls=[];window.failFetch=false;
    window.draft={schemaVersion:1,source:'other_public_sources',url:'https://example.test/job',company:'Example',jobTitle:'Frontend Developer',description:'Complete synthetic JD',sourceContent:'Captured synthetic source',descriptionStatus:'complete',descriptionKind:'public_page',capturedAt:'2026-09-19T06:00:00Z',completenessEvidence:'Verified fixture',evidence:{availabilityStatus:'Apply now'}};
    window.fixture={checkpoint:{runId:'synthetic',startedAt:new Date().toISOString(),tasks:[]},config:{search_profiles:[{id:'frontend'}]},reviewEvents:[],cv:{profiles:[{path:'profile/test',name:'Test'}],templates:['navy-header-photo'],defaultTemplate:'navy-header-photo'},jobs:Array.from({length:15},(_,i)=>({jobId:'job-'+i,jobTitle:'Frontend '+i,company:'Fixture',locations:[],approvalBlocks:[],approvalWarnings:[],review:{status:i<14?'approved':'pending'},descriptionStatus:'complete',matchStatus:'accepted'}))};
    window.fetch=async(url,options)=>{const body=options?.body?JSON.parse(options.body):null;calls.push({url,body});let result={};
      if(url==='/api/state')result={csrf:'test',operations:[],runs:[],trash:[],batches:[],presets:[],agent:{model:'fixture'},capabilities:{codex:true}};
      else if(url.startsWith('/api/run'))result=fixture;
      else if(url==='/api/fetch-jd')result=failFetch?{error:'browser_required'}:{observation:draft};
      else if(!['/api/action','/api/import'].includes(url))throw Error('Unexpected network '+url);
      return {ok:!(url==='/api/fetch-jd'&&failFetch),json:async()=>structuredClone(result)};};
  `);
  await run('var app=document.createElement("script");app.textContent='+JSON.stringify(fs.readFileSync(path.join(assets,'app.js'),'utf8'))+';document.body.appendChild(app);');
  await run(`(async()=>{
    const wait=async predicate=>{for(let i=0;i<100;i++){if(predicate())return;await new Promise(r=>setTimeout(r,10));}throw Error('UI timeout');};
    const click=action=>document.querySelector('[data-action="'+action+'"]').click();
    const check=(id,value)=>{const e=document.querySelector('[data-select="'+id+'"]');e.checked=value;e.dispatchEvent(new Event('change',{bubbles:true}));};
    await wait(()=>state.data);await openRun('synthetic');
    assert($('#create-cv').textContent.includes('(14)'), 'Default counts all approved');
    check('job-0',true);assert($('#create-cv').textContent.includes('(1)'), 'Single selection must count one');
    click('cv-dialog');assert(state.cvJobs.length===1&&state.cvJobs[0]==='job-0','Dialog subset');
    $('#confirm-profile').checked=true;click('start-cv');await wait(()=>calls.some(c=>c.body?.kind==='cv'));
    const queueCall=calls.find(c=>c.body?.kind==='cv');assert(queueCall.body.jobIds.join()==='job-0','Queue only selected job');
    await wait(()=>!$('#modal').open);check('job-0',false);check('job-14',true);
    assert($('#create-cv').disabled&&$('#create-cv').textContent.includes('(0)'), 'Pending-only selection disabled');
    check('job-1',true);assert($('#create-cv').textContent.includes('(1)'), 'Mixed selection counts only approved');
    click('clear-selection');assert($('#create-cv').textContent.includes('(14)'), 'Clear selection restores explicit all-approved scope');
    click('import');$('#capture-url').value=draft.url;click('fetch-jd');await wait(()=>state.fetchedDraft&&!state.fetching);
    assert($('#capture-company').value==='Example'&&$('#capture-title').value==='Frontend Developer'&&$('#capture-jd').value===draft.description,'Autofill');
    assert(!calls.some(c=>c.url==='/api/import'),'Preview must not save');
    click('save-observation');await wait(()=>calls.some(c=>c.url==='/api/import'));await wait(()=>!$('#modal').open);
    assert(JSON.stringify(calls.find(c=>c.url==='/api/import').body.observation)===JSON.stringify(draft),'Save preserves source evidence');
    click('import');$('#capture-url').value=draft.url;click('fetch-jd');await wait(()=>state.fetchedDraft&&!state.fetching);
    $('#capture-jd').value+=' edited';click('save-observation');await wait(()=>calls.filter(c=>c.url==='/api/import').length===2);await wait(()=>!$('#modal').open);
    const edited=calls.filter(c=>c.url==='/api/import')[1].body.observation;
    assert(edited.source==='manual'&&edited.descriptionStatus==='partial'&&!edited.evidence,'Edited draft loses unsupported verification');
    failFetch=true;click('import');$('#capture-url').value=draft.url;click('fetch-jd');await wait(()=>$('#capture-status').textContent.includes('browser_required'));
    assert(!state.fetching&&!document.querySelector('[data-action="fetch-jd"]').disabled,'Failed fetch re-enables form');
    return 'PASS: selection, queue subset, autofill, evidence preservation, edited draft and blocked source';
  })()`).then(console.log);
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(ws)ws.close();
  if(process.platform==='win32')spawnSync('taskkill',['/PID',String(browser.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
  else browser.kill();
  await delay(300);
  const resolved=path.resolve(temp);
  if(path.dirname(resolved)===path.resolve(os.tmpdir())&&path.basename(resolved).startsWith('seek-job-browser-test-'))
    fs.rmSync(resolved,{recursive:true,force:true,maxRetries:5,retryDelay:200});
});
