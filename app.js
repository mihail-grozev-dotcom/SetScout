'use strict';
/* SetScout v4 — zero-dependency local interface; no credentials or telemetry. */
const $=id=>document.getElementById(id);
const safeStore={
  get(k,def){try{return JSON.parse(localStorage.getItem(k))??def}catch{return def}},
  set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch{}}
};
const state={
  games:SEED.games.map(g=>({...g})),candidates:[],pending:safeStore.get('setscout:pending',[]),
  saved:new Set(safeStore.get('setscout:saved',[]).map(String)),filter:'all',search:'',sort:'az',
  backend:false,auto:true,autoHours:6,scan:{},bulk:{state:'idle'},busy:false,detail:null,
  reportGameId:null,reportId:null,events:[],onlyWatchEvents:false
};
const esc=x=>String(x??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#39;'}[ch]));
// Escape both quote types for safely inserted attributes.
function e(x){return esc(x).replace(/&quot(?!;)/g,'&quot;')}
function safeUrl(url){try{const u=new URL(url);return ['https:','http:'].includes(u.protocol)?u.href:''}catch{return ''}}
const steamURL=g=>`https://store.steampowered.com/app/${Number(g.id)}/`;
const achievementURL=g=>`https://steamcommunity.com/achievements/${Number(g.id)}`;
const imageURL=g=>`https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/${Number(g.id)}/header.jpg`;
function timeLabel(x){if(!x)return 'Not yet checked';const d=new Date(x);return isNaN(d)?String(x):d.toLocaleString()}
function localInputValue(x){if(!x)return '';const d=new Date(x);if(isNaN(d))return '';const p=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`}
const archiveItems=g=>Array.isArray(g.steam_archived_items)?g.steam_archived_items:[];
const reportItems=g=>Array.isArray(g.limited_items)?g.limited_items:[];
function reportStatus(r){if(r.status==='limited_active'&&r.ends_at){const d=new Date(r.ends_at);if(!isNaN(d)&&d.getTime()<=Date.now())return 'deadline_passed'}return r.status||'unknown'}
function temporal(g){
  const reports=reportItems(g),active=reports.filter(r=>reportStatus(r)==='limited_active');
  const passed=reports.filter(r=>reportStatus(r)==='deadline_passed');
  const archived=archiveItems(g).length>0||reports.some(r=>r.status==='archived');
  const deadlines=active.map(r=>new Date(r.ends_at)).filter(d=>!isNaN(d)&&d.getTime()>=Date.now()).sort((a,b)=>a-b);
  const upcoming=deadlines.length>0&&deadlines[0].getTime()-Date.now()<=7*86400000;
  return {active:active.length,passed:passed.length,archived,upcoming,next:deadlines[0]||null,unknown:!active.length&&!archived&&!passed.length};
}
function sourceLabel(g){return g.verified?'STEAM VERIFIED':g.source==='Valve launch list'?'VALVE LAUNCH LIST':g.source==='Developer announcement'?'DEVELOPER REPORT':g.source==='Community report'?'COMMUNITY REPORTED':g.source==='Unverified local entry'?'LOCAL SUGGESTION':'CANDIDATE · NOT VERIFIED'}
function toast(message){const x=$('toast');x.textContent=message;x.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>x.classList.remove('show'),5200)}
function openModal(id){$(id).classList.add('open')}
function closeModal(id){$(id).classList.remove('open')}
function itemsForFilter(){
  if(state.filter==='pending'){
    const local=state.pending.filter(p=>!state.games.some(g=>Number(g.id)===Number(p.id)));
    return state.backend?[...state.candidates,...local.filter(p=>!state.candidates.some(c=>Number(c.id)===Number(p.id)))]:local;
  }
  return state.games;
}
function filtered(){
  const games=itemsForFilter().filter(g=>!state.search||`${g.name} ${g.id} ${reportItems(g).map(x=>x.title).join(' ')}`.toLowerCase().includes(state.search));
  const matches=games.filter(g=>{
    const t=(g.types||[]).map(s=>String(s).toLowerCase()), v=temporal(g);
    switch(state.filter){
      case 'limited':return v.active>0;
      case 'deadline':return v.upcoming;
      case 'archived':return v.archived;
      case 'unknown':return v.unknown;
      case 'dlc':return t.includes('dlc');
      case 'modes':return t.includes('modes');
      case 'confirmed':return g.verified===true;
      case 'saved':return state.saved.has(String(g.id));
      default:return true;
    }
  });
  matches.sort((a,b)=>{
    if(state.sort==='recent')return String(b.added||b.discovered_at||'').localeCompare(String(a.added||a.discovered_at||''));
    if(state.sort==='sets')return (Number(b.set_count)||0)-(Number(a.set_count)||0);
    if(state.sort==='deadline')return (temporal(a).next?.getTime()||Infinity)-(temporal(b).next?.getTime()||Infinity);
    return String(a.name||'').localeCompare(String(b.name||''));
  });
  return matches;
}
function tag(t,cls){return `<span class="tag ${cls||''}">${e(t)}</span>`}
function renderGame(g){
  const saved=state.saved.has(String(g.id)),v=temporal(g),types=(g.types||[]).filter(x=>String(x).toLowerCase()!=='archived').slice(0,4).map(x=>tag(x,String(x).toLowerCase())).join('');
  const archival=archiveItems(g).length>0?'Archived · Steam flag':reportItems(g).some(r=>r.status==='archived')?'Archived · reported':'';
  let flags='';
  if(v.active)flags+=tag(`${v.active} active report${v.active>1?'s':''}`,'limited');
  if(v.upcoming)flags+=tag('Deadline soon','deadline');
  if(archival)flags+=tag(archival,'archived');
  if(v.passed)flags+=tag('Deadline passed · unverified','deadline');
  if(v.unknown)flags+=tag('Time limit unknown','unknown');
  if(g.verified)flags+=tag('Steam page checked','check');
  const status=v.active?`<span>◉</span> Limited-time: user-reported${v.next?` · ends ${e(v.next.toLocaleString())}`:''}`:archival?`<span>◈</span> Archive evidence exists`:v.passed?`<span>◷</span> Deadline passed; archival unverified`:`<span>○</span> No reliable time-limit information`;
  const date=`Last checked: ${e(timeLabel(g.checked_at))}`;
  return `<article class="game"><div class="cover"><img loading="lazy" src="${imageURL(g)}" alt="" onerror="this.style.display='none'"><button class="mark ${saved?'on':''}" data-save="${g.id}" aria-label="${saved?'Remove saved game':'Save game'}">${saved?'★':'☆'}</button><span class="source-pill">${e(sourceLabel(g))}</span></div>
   <div class="game-body"><h3>${e(g.name)}</h3><div class="appid">Steam App ID · ${Number(g.id)}${g.set_count?` · ${Number(g.set_count)} sets`:''}</div><div class="tags">${types}${flags}</div><div class="temporal-status">${status}</div><div class="date-stamp">${date}${g.last_check_status&&g.last_check_status!=='confirmed'?` · Latest check: ${e(g.last_check_status)}`:''}</div><div class="game-footer"><a href="${steamURL(g)}" target="_blank" rel="noopener">Steam store ↗</a><button data-detail="${g.id}">Track / details →</button></div></div></article>`;
}
function render(){
  const games=filtered(),all=itemsForFilter();
  $('heroCount').textContent=state.games.length;
  $('officialCount').textContent=state.games.filter(x=>x.source==='Valve launch list').length;
  $('confirmedCount').textContent=state.games.filter(x=>x.verified).length;
  $('savedCount').textContent=state.saved.size;
  $('limitedCount').textContent=state.games.reduce((n,g)=>n+temporal(g).active,0);
  $('deadlineCount').textContent=state.games.filter(g=>temporal(g).upcoming).length;
  $('archiveCount').textContent=state.games.filter(g=>temporal(g).archived).length;
  $('filterStatus').textContent=`Showing ${games.length} of ${all.length} titles`;
  const titles={limited:'Reported active limited-time',deadline:'Upcoming deadlines',archived:'Archive evidence',unknown:'Status unknown',pending:'Candidate queue',saved:'My saved games'};
  $('catalogTitle').textContent=titles[state.filter]||'Explore games';
  $('catalogSubtitle').textContent=state.filter==='pending'?'Candidates are not confirmed to have custom Achievement Sets.':'Click Track / details to add a source-linked time-sensitive report.';
  document.querySelectorAll('#chips [data-filter]').forEach(c=>c.classList.toggle('active',c.dataset.filter===state.filter));
  $('grid').innerHTML=games.length?games.map(renderGame).join(''):`<div class="empty"><h3>Nothing found</h3><p>Try another filter or add an evidence-backed report for a game.</p><button class="btn" id="clearBtn">Clear filters</button></div>`;
  if($('clearBtn'))$('clearBtn').onclick=()=>{state.filter='all';state.search='';$('search').value='';render()};
  renderScanner();renderActivity();
}
function renderScanner(){
  const on=state.backend,bulk=state.bulk||{state:'idle'};
  const running=bulk.state==='running',unverified=state.games.filter(g=>!g.verified).length;
  $('autoBadge').textContent=on?(state.auto?'AUTO: ON':'AUTO: OFF'):'SERVER OFFLINE';
  $('autoBadge').classList.toggle('off',!on||!state.auto);
  for(const id of ['discoverBtn','scanBtn','autoBtn','importBtn'])$(id).disabled=!on||state.busy||running;
  $('checkAllBtn').disabled=!on||state.busy||running||unverified===0;
  $('checkAllBtn').textContent=`✓ Check all unverified (${unverified})`;
  $('cancelAllBtn').hidden=!on||!running;
  $('cancelAllBtn').disabled=!on||bulk.cancel_requested;
  $('cancelAllBtn').textContent=bulk.cancel_requested?'Stopping after current check…':'Stop bulk scan';
  $('autoBtn').textContent=state.auto?'● Automatic checks: ON':'○ Automatic checks: OFF';
  const showBulk=on&&bulk.state!=='idle';
  $('bulkProgress').hidden=!showBulk;
  if(showBulk){
    const total=Number(bulk.total||0),completed=Number(bulk.completed||0);
    $('bulkMeter').max=Math.max(total,1);
    $('bulkMeter').value=completed;
    const statusText={running:'Checking',completed:'Finished',cancelled:'Stopped',error:'Stopped due to error'}[bulk.state]||'Ready';
    $('bulkStatus').textContent=`${statusText} ${completed} of ${total} games · ${Number(bulk.confirmed||0)} verified · ${Number(bulk.not_detected||0)} not detected · ${Number(bulk.errors||0)} errors${bulk.cancel_requested?' · stopping…':''}`;
    $('bulkLastGame').textContent=bulk.error|| (bulk.last_game?`Last: ${bulk.last_game} (${bulk.last_status||'checked'})`:'Each unverified game will be checked once in this run.');
  }
  if(!on)return;
  const src=state.scan?.last_sources||{};
  const feeds=Object.entries(src).map(([name,x])=>`${e(name)}: ${x.ok?`${Number(x.seen||0)} candidates`:'unavailable'}`).join(' · ');
  $('scanInfo').innerHTML=`<strong>Last scan:</strong> ${e(timeLabel(state.scan?.last_run))} · <strong>Queued:</strong> ${state.candidates.length} · <strong>Automatic:</strong> 8 candidates + 8 known games every ${state.autoHours}h while Python is running.${feeds?`<div>${feeds}</div>`:''}`;
}
function renderActivity(){
  $('eventFilterBtn').textContent=state.onlyWatchEvents?'★ Watching only':'All games';
  const events=[...state.events].sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at))).filter(x=>!state.onlyWatchEvents||state.saved.has(String(x.appid))).slice(0,32);
  $('eventCount').textContent=`${state.events.length} recorded`;
  $('activityList').innerHTML=events.length?events.map(item=>{
    const icon=item.kind==='archive'?'◈':item.kind==='deadline'?'◷':item.kind==='report'?'◉':'✦';
    return `<div class="event"><span class="glyph">${icon}</span><div class="event-copy"><strong>${e(item.title)}</strong><div class="subtle">${e(item.game||'Steam game')}</div><small>${e(timeLabel(item.created_at))} · ${e(item.detail||'')}</small></div><button class="btn small" data-eventgame="${Number(item.appid)}">View</button></div>`;
  }).join(''):'<div class="event empty-event">No matching activity yet. New archive observations and reports will appear here.</div>';
  if(!('Notification' in window))$('notifyBtn').textContent='Desktop alerts not supported';
  else $('notifyBtn').textContent=Notification.permission==='granted'?'✓ Desktop alerts enabled':Notification.permission==='denied'?'Alerts blocked in browser':'Enable desktop alerts';
  $('notifyBtn').disabled=!(('Notification' in window))||Notification.permission==='denied';
}
function notifyNewEvents(){
  const key='setscout:notified:v3';const prior=safeStore.get(key,null);
  const ids=state.events.map(x=>x.id).slice(-300);
  if(prior===null){safeStore.set(key,ids);return}
  const seen=new Set(prior);
  const fresh=state.events.filter(x=>!seen.has(x.id)&&state.saved.has(String(x.appid)));
  if(fresh.length){
    const latest=fresh[fresh.length-1];toast(`${fresh.length} new watchlist alert${fresh.length===1?'':'s'}: ${latest.title}`);
    if('Notification' in window&&Notification.permission==='granted'){
      try{new Notification(`SetScout · ${latest.game}`,{body:`${latest.title}${fresh.length>1?` (+${fresh.length-1} more)`:''}`})}catch{}
    }
  }
  safeStore.set(key,[...new Set([...prior,...ids])].slice(-600));
}
function toggleSaved(id){id=String(id);state.saved.has(id)?state.saved.delete(id):state.saved.add(id);safeStore.set('setscout:saved',[...state.saved]);render()}
function findGame(id){return [...state.games,...state.candidates,...state.pending].find(x=>String(x.id)===String(id))}
function detailItemMarkup(item,g){
  const st=reportStatus(item);const statusName={limited_active:'Reported active limited-time',archived:'Reported archived',unknown:'Unknown / investigating',deadline_passed:'Deadline passed · archive unverified'}[st]||'Unknown';
  const url=safeUrl(item.source_url),date=item.ends_at?`<p>Reported end: ${e(timeLabel(item.ends_at))}${st==='deadline_passed'?' · Steam archival not verified':''}</p>`:'';
  const actions=state.backend?`<div class="tiny-actions"><button data-edit="${e(item.id)}" data-game="${Number(g.id)}">Edit</button><button data-delete="${e(item.id)}" data-game="${Number(g.id)}">Delete</button></div>`:'';
  return `<div class="detail-item"><header><h4>${e(item.title)}</h4><span class="status-label ${st==='archived'?'archived':st==='unknown'?'unknown':'active'}">${e(statusName)}</span></header><p>${e(item.scope)} · User-reported (not independently verified) · Updated ${e(timeLabel(item.updated_at))}</p>${date}${item.note?`<p>${e(item.note)}</p>`:''}${url?`<p><a href="${e(url)}" target="_blank" rel="noopener noreferrer">View supplied source ↗</a></p>`:''}${actions}</div>`;
}
function showDetail(id){
  const g=findGame(id);if(!g)return;
  state.detail=g.id;$('detailTitle').textContent=g.name;
  $('detailMeta').textContent=`App ID ${g.id} · ${sourceLabel(g)} · ${g.set_count?`${g.set_count} sets · `:''}Checked ${timeLabel(g.checked_at)}. Steam may hide archived content from public views.`;
  $('detailSets').innerHTML=(g.set_names||[]).map(x=>`<span>${e(x)}</span>`).join('')||'<span>Set names not currently exposed</span>';
  $('detailArchives').innerHTML=archiveItems(g).length?archiveItems(g).map(a=>`<div class="detail-item"><h4>${e(a.name)}</h4><p>Steam page explicitly flagged ${e(a.kind)} as archived${a.set?` · ${e(a.set)}`:''}. This may be older cached evidence.</p></div>`).join(''):'<p class="subtle">No positive Steam archival flag detected. This does not mean no archived achievements exist.</p>';
  $('detailReports').innerHTML=reportItems(g).length?reportItems(g).map(r=>detailItemMarkup(r,g)).join(''):'<p class="subtle">No source-linked limited-time or archived reports yet.</p>';
  $('detailSave').textContent=state.saved.has(String(g.id))?'★ Remove saved':'☆ Save game';
  $('detailSteam').href=achievementURL(g);
  $('detailAddReport').disabled=!state.backend||!state.games.some(x=>x.id===g.id);
  $('detailAddReport').title=!state.backend?'Start the Python server to save reports.':'';
  openModal('detailOverlay');
}
function addReport(id,existing=null){
  const g=findGame(id);if(!g||!state.backend)return;
  state.reportGameId=Number(id);state.reportId=existing?.id||null;
  $('reportGame').textContent=g.name;
  $('reportTitle').textContent=existing?'Edit time-sensitive entry':'Add a time-sensitive entry';
  $('reportStatus').value=existing?.status||'limited_active';
  $('reportScope').value=existing?.scope||'set';
  $('reportName').value=existing?.title||'';
  $('reportSource').value=existing?.source_url||'';
  $('reportDeadline').value=localInputValue(existing?.ends_at);
  $('reportNote').value=existing?.note||'';
  $('reportFeedback').classList.remove('show');
  updateReportForm();openModal('reportOverlay');
}
function updateReportForm(){
  const active=$('reportStatus').value==='limited_active';
  $('reportDeadline').disabled=!active;
  if(!active)$('reportDeadline').value='';
  $('reportSource').required=$('reportStatus').value!=='unknown';
}
async function post(url,data){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const json=await r.json();if(!r.ok||json.error)throw Error(json.error||`HTTP ${r.status}`);return json;
}
async function saveReport(ev){
  ev.preventDefault();if(!state.backend)return;
  const feedback=$('reportFeedback');const deadline=$('reportDeadline').value;
  const payload={appid:state.reportGameId,report_id:state.reportId||undefined,
     title:$('reportName').value.trim(),status:$('reportStatus').value,scope:$('reportScope').value,
     source_url:$('reportSource').value.trim(),note:$('reportNote').value.trim(),
     ends_at:deadline?new Date(deadline).toISOString():''};
  $('reportSubmit').disabled=true;feedback.classList.remove('show');
  try{await post('/api/report',payload);await loadData();closeModal('reportOverlay');showDetail(state.reportGameId);toast('Time-sensitive report saved with its source.')}catch(err){feedback.textContent=err.message;feedback.classList.add('show')}finally{$('reportSubmit').disabled=false}
}
async function deleteReport(id,gameid){
  if(!confirm('Delete this user-submitted report?'))return;
  try{await post('/api/delete-report',{appid:gameid,report_id:id});await loadData();showDetail(gameid);toast('Report deleted.')}catch(err){toast(`Cannot delete: ${err.message}`)}
}
function getID(value){const raw=value.trim();if(/^\d{1,10}$/.test(raw))return Number(raw);const m=raw.match(/^https?:\/\/(?:store\.steampowered\.com|steamcommunity\.com)\/(?:app|stats|achievements)\/(\d{1,10})(?:[/?#]|$)/i);return m?Number(m[1]):null}
function addPending(id,name){if(!state.games.some(g=>g.id===id)&&!state.pending.some(g=>g.id===id)){state.pending.push({id,name:name||`Steam App ${id}`,source:'Unverified local entry',added:new Date().toISOString().slice(0,10),types:[]});safeStore.set('setscout:pending',state.pending)}}
async function check(){
  const id=getID($('steamInput').value),f=$('checkFeedback');if(!id){f.textContent='Enter a valid Steam App ID or URL.';f.classList.add('show');return}
  const name=$('gameName').value.trim().slice(0,140),btn=$('checkBtn');btn.disabled=true;btn.textContent='Checking Steam…';f.classList.remove('show');
  try{
    if(state.backend){const result=await post('/api/check',{appid:id,name});await loadData();f.textContent=result.verified?`${result.name}: ${result.set_count} custom sets detected.`:`Not confirmed: ${result.reason||result.status}. The game stays in the candidate queue.`}
    else{addPending(id,name);f.textContent='Server offline: added as a local unverified suggestion.'}
    f.classList.add('show');render();
  }catch(err){f.textContent=`Check failed: ${err.message}`;f.classList.add('show')}finally{btn.disabled=false;btn.textContent='Check / Add game'}
}
async function loadData(){
  try{
    const r=await fetch('/api/games',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error('Server offline');
    const data=await r.json();if(!Array.isArray(data.games))throw Error('Malformed catalogue');
    state.backend=true;state.games=data.games;state.candidates=data.candidates||[];state.events=data.events||[];
    state.auto=data.auto_enabled!==false;state.autoHours=data.auto_hours||6;state.scan=data.scan||{};
    const previous=state.bulk?.state;state.bulk=data.bulk||{state:'idle'};
    if(previous==='running'&&['completed','cancelled','error'].includes(state.bulk.state))toast(`Bulk scan ${state.bulk.state}: ${state.bulk.completed}/${state.bulk.total} checked.`);
    $('serverBanner').classList.remove('hidden');$('serverBanner').textContent='● Local Python server connected. Steam page checks are experimental; externally sourced limited-time reports remain user-reported. Automatic scans run only while the server stays open.';
    notifyNewEvents();
  }catch{
    state.backend=false;state.bulk={state:'idle'};$('serverBanner').classList.add('hidden');
  }
  render();
}
async function runScan(mode){
  if(!state.backend||state.busy)return;state.busy=true;renderScanner();
  $('scanResults').textContent=mode==='discover'?'Checking up to 8 newly discovered candidates…':'Checking up to 8 listed games for changes…';
  try{
    const data=await post(mode==='discover'?'/api/discover':'/api/scan',{limit:8});
    await loadData();const missing=Object.entries(data.sources||{}).filter(([,v])=>!v.ok).map(([k])=>k);
    $('scanResults').innerHTML=`<strong>${Number(data.new_games||0)} new confirmed</strong> · ${Number(data.checked||0)} checked · ${Number(data.errors||0)} errors · ${Number(data.not_detected||0)} without positive evidence. ${missing.length?`Feeds unavailable: ${e(missing.join(', '))}`:''}`;
    toast('Steam check completed. Review time-sensitive updates in Activity.');
  }catch(err){$('scanResults').textContent=`Scan failed: ${err.message}`}finally{state.busy=false;renderScanner()}
}
async function checkAllUnverified(){
  if(!state.backend||state.busy||state.bulk?.state==='running')return;
  state.busy=true;renderScanner();
  try{
    state.bulk=await post('/api/scan-unverified',{});
    await loadData();
    $('scanResults').textContent=`Bulk scan started: ${Number(state.bulk.total||0)} unverified listed games. You can leave this page open to watch progress.`;
  }catch(err){$('scanResults').textContent=`Bulk scan failed: ${err.message}`}
  finally{state.busy=false;renderScanner()}
}
async function stopBulkScan(){
  try{state.bulk=await post('/api/cancel-unverified',{});renderScanner()}
  catch(err){toast(`Cannot stop scan: ${err.message}`)}
}
async function toggleAuto(){try{const r=await post('/api/settings',{auto_enabled:!state.auto});state.auto=r.auto_enabled;renderScanner();toast(state.auto?'Automatic scanning enabled.':'Automatic scanning disabled.')}catch(err){toast(err.message)}}
async function importIDs(){const b=$('bulkSubmit'),f=$('importFeedback');b.disabled=true;try{const data=await post('/api/import',{text:$('bulkInput').value});f.textContent=`${data.queued} added to queue (${data.received} unique IDs).`;f.classList.add('show');await loadData()}catch(err){f.textContent=err.message;f.classList.add('show')}finally{b.disabled=false}}
$('grid').addEventListener('click',ev=>{const save=ev.target.closest('[data-save]');if(save){toggleSaved(save.dataset.save);return}const d=ev.target.closest('[data-detail]');if(d)showDetail(d.dataset.detail)});
$('chips').addEventListener('click',ev=>{const b=ev.target.closest('[data-filter]');if(b){state.filter=b.dataset.filter;render()}});
$('activityList').addEventListener('click',ev=>{const b=ev.target.closest('[data-eventgame]');if(b)showDetail(b.dataset.eventgame)});
$('detailReports').addEventListener('click',ev=>{const edit=ev.target.closest('[data-edit]');const del=ev.target.closest('[data-delete]');if(edit){const g=findGame(edit.dataset.game);const r=reportItems(g).find(x=>x.id===edit.dataset.edit);if(r)addReport(g.id,r)}if(del)deleteReport(del.dataset.delete,del.dataset.game)});
$('search').addEventListener('input',ev=>{state.search=ev.target.value.trim().toLowerCase();render()});
$('sort').addEventListener('change',ev=>{state.sort=ev.target.value;render()});
$('addBtn').onclick=()=>{openModal('addOverlay');$('steamInput').focus()};
$('checkBtn').onclick=check;
$('discoverBtn').onclick=()=>runScan('discover');
$('scanBtn').onclick=()=>runScan('recheck');
$('checkAllBtn').onclick=checkAllUnverified;
$('cancelAllBtn').onclick=stopBulkScan;
$('autoBtn').onclick=toggleAuto;
$('importBtn').onclick=()=>openModal('importOverlay');
$('bulkSubmit').onclick=importIDs;
$('detailSave').onclick=()=>{toggleSaved(state.detail);showDetail(state.detail)};
$('detailAddReport').onclick=()=>addReport(state.detail);
$('reportForm').onsubmit=saveReport;
$('reportStatus').onchange=updateReportForm;
$('notifyBtn').onclick=async()=>{if(!('Notification' in window))return;try{const p=await Notification.requestPermission();renderActivity();toast(p==='granted'?'Desktop notifications enabled while the tab stays open.':'Browser notifications not enabled.')}catch(err){toast(`Notification request failed: ${err.message}`)}};
$('eventFilterBtn').onclick=()=>{state.onlyWatchEvents=!state.onlyWatchEvents;renderActivity()};
$('exportBtn').onclick=()=>{
  const games=state.games.filter(g=>state.saved.has(String(g.id)));
  const json=JSON.stringify({exported_at:new Date().toISOString(),saved_games:games,events:state.events.filter(x=>state.saved.has(String(x.appid)))},null,2);
  const url=URL.createObjectURL(new Blob([json],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='setscout-v3-watchlist.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1500);
};
document.querySelectorAll('[data-close]').forEach(el=>el.onclick=()=>closeModal(el.dataset.close));
document.querySelectorAll('.overlay').forEach(el=>el.onclick=ev=>{if(ev.target===el)closeModal(el.id)});
document.addEventListener('keydown',ev=>{if(ev.key==='Escape')document.querySelectorAll('.overlay.open').forEach(x=>closeModal(x.id));if(ev.key==='Enter'&&$('addOverlay').classList.contains('open')&&document.activeElement===$('steamInput'))check()});
render();
if(location.protocol==='http:'||location.protocol==='https:'){
  loadData();setInterval(()=>{if(!state.busy)loadData()},45000);
  setInterval(()=>{if(!state.busy&&state.backend&&state.bulk?.state==='running')loadData()},1500);
}
