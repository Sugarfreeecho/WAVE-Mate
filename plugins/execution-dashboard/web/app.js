const openState = new Map();
let lastData = { sessions: [] };
let inflight = null;
let selectedRequestKey = '__total__';
let phaseChartMode = 'round';
let reconcileMode = 'session';
let reconcileAllOpen = false;
const SESSION_FILTER_KEY = 'execution-dashboard-session-filter';
let sessionIndex = [];
let activeSessionId = '';
let sessionDefaultResolved = false;
let fullFallback = false;
const chartScrollState = new Map();
const chartLegendSelection = new Map();
const CHART_POINT_LIMIT = 300;
const REFRESH_DELAY_MS = 2000;
let refreshTimer = null;
const PHASE_DEFS=[
  ['pre_api','API 发送前准备'],['api_send','API 发送'],['first_token','首 token'],
  ['llm_output','LLM 输出'],['tool_execution','工具执行（批次墙钟）'],['round_postprocess','轮内后处理'],
  ['final_pipeline','Run 收尾'],
];

const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const ms = value => { const n=Number(value); if(!Number.isFinite(n)) return '—'; return n>=1000?(n/1000).toFixed(n>=10000?1:2)+' s':Math.max(0,Math.round(n))+' ms'; };
const num = value => Number.isFinite(Number(value)) ? Number(value) : 0;
function runWallMs(run){
  const wall=num(run.wall_ms);
  if(wall>0)return wall;
  const a=Date.parse(run.started_at||''),b=Date.parse(run.finished_at||'');
  if(Number.isFinite(a))return Math.max(0,(Number.isFinite(b)?b:Date.now())-a);
  return 0;
}
function runWallTotalMs(rows){
  const seen=new Set();let total=0;
  rows.forEach(row=>{
    const key=String(row.session.session_id||'')+'|'+String(row.run.run_id||'');
    if(seen.has(key))return;
    seen.add(key);total+=runWallMs(row.run);
  });
  return total;
}
function toolWallMs(req){
  const rec=(req.phases||{}).tool_execution;
  if(rec&&num(rec.total_ms)>0)return num(rec.total_ms);
  return (req.tools||[]).reduce((n,t)=>Math.max(n,num(t.duration_ms)),0);
}
function runMergedRows(rows){
  const runs=new Map();
  rows.forEach(row=>{
    const key=String(row.session.session_id||'')+'|'+String(row.run.run_id||'');
    if(!runs.has(key))runs.set(key,{session:row.session,run:row.run,reqs:[]});
    runs.get(key).reqs.push(row.req);
  });
  return Array.from(runs.values()).map(({session,run,reqs})=>{
    const last=reqs[reqs.length-1]||{};
    return{session,run,reqs,req:{react_iter:reqs.length?1:0,started_at:(reqs[0]||{}).started_at||run.started_at,model:last.model||'',status:run.status||''}};
  });
}
function phaseTotalMs(row,key){
  if(row.reqs)return row.reqs.reduce((n,req)=>n+num(phaseData(req,key).total_ms),0);
  return num(phaseData(row.req,key).total_ms);
}
function observedRun(row) {
  const runs=((row.session.observability||{}).runs)||[];
  return runs.find(run=>String(run.run_id||'')===String(row.run.run_id||''))||null;
}

function uniqueObservedRuns(rows) {
  const seen=new Set(), result=[];
  rows.forEach(row=>{
    const run=observedRun(row);
    if(!run)return;
    const key=String(row.session.session_id||'')+'|'+String(run.run_id||'');
    if(seen.has(key))return;
    seen.add(key);result.push(run);
  });
  return result;
}

function phaseData(req,key){
  const phases=req.phases||{};
  if(key==='pre_api'){
    const base=phases.pre_api||{};
    const tail=Math.max(0,num(req.pre_api_tail_ms));
    const events=Object.assign({},base.events||{});
    if(tail>0)events.pre_api_tail=tail;
    return{total_ms:num(base.total_ms)+tail,events};
  }
  if(phases[key])return phases[key];
  const stream=((phases.llm_stream||{}).events)||[],at=name=>num((stream.find(e=>e.step===name)||{}).ms_since_api_start);
  if(key==='api_send'){const v=Math.max(0,at('stream_created')-at('request_start'));return{total_ms:v,events:{request_start_to_stream_created:v}};}
  if(key==='first_token'){const v=Math.max(0,at('first_delta')-at('stream_created'));return{total_ms:v,events:{stream_created_to_first_delta:v}};}
  if(key==='llm_output'){const v=Math.max(0,(at('stream_exhausted')||at('turn_ready'))-at('first_delta'));return{total_ms:v,events:{first_delta_to_stream_end:v}};}
  if(key==='tool_execution'){
    const rec=phases.tool_execution;
    if(rec&&num(rec.total_ms)>0)return rec;
    const values=(req.tools||[]).map(t=>num(t.duration_ms)),v=values.length?Math.max(...values):0;
    return{total_ms:v,events:{estimated_parallel_wall_time:v}};
  }
  if(key==='round_postprocess'){const a=num((phases.tool_result_post||{}).total_ms),b=num((phases.tool_to_next_api||{}).total_ms);return{total_ms:a+b,events:{tool_result_post:a,tool_to_next_api:b}};}
  return{total_ms:0,events:{}};
}

function flatten(data, sessionId='') {
  const rows=[];
  (data.sessions||[]).forEach(session => {
    if(sessionId && session.session_id!==sessionId) return;
    (session.runs||[]).forEach(run => (run.requests||[]).forEach(req => rows.push({session,run,req})));
  });
  rows.sort((a,b)=>String(a.req.started_at||a.run.started_at||'').localeCompare(String(b.req.started_at||b.run.started_at||'')));
  return rows;
}

function sampleChartRows(rows) {
  if (rows.length <= CHART_POINT_LIMIT) return rows;
  const sampled=[];
  for(let i=0;i<CHART_POINT_LIMIT;i++){
    const index=Math.round(i*(rows.length-1)/(CHART_POINT_LIMIT-1));
    sampled.push(rows[index]);
  }
  return sampled;
}

function lineChart(target, rows, series, defaultUnit='ms') {
  const el=document.getElementById(target);if(!el)return;
  if(!rows.length||!series.length){el.innerHTML='<div class="empty">暂无数据</div>';return;}
  const selected=chartLegendSelection.get(target)||'',colors=['#89b4fa','#f9e2af','#a6e3a1','#cba6f7','#f38ba8','#94e2d5','#fab387','#74c7ec'];
  series=series.map((s,i)=>Object.assign({axis:'left',unit:defaultUnit,color:colors[i%colors.length]},s));
  const visible=selected?series.filter(s=>s.name===selected):series;
  const leftSeries=visible.filter(s=>s.axis!=='right'),rightSeries=visible.filter(s=>s.axis==='right');
  const maximum=list=>Math.max(1,...rows.flatMap(row=>list.map(s=>num(s.value(row)))));
  const maxLeft=maximum(leftSeries),maxRight=maximum(rightSeries),hasRight=series.some(s=>s.axis==='right');
  const visiblePoints=20,H=270,P={t:24,b:50};
  const axisWidth=62,axisCount=hasRight?2:1,viewportWidth=Math.max(360,el.clientWidth-axisWidth*axisCount-13);
  const W=Math.round(viewportWidth*Math.max(1,rows.length/visiblePoints));
  const x=i=>12+(rows.length===1?.5:i/(rows.length-1))*(W-24),y=(v,max)=>H-P.b-(num(v)/max)*(H-P.t-P.b);
  const format=(v,unit)=>unit==='ms'?ms(v):unit==='B'?formatBytes(v):Math.round(v).toLocaleString();
  let plot=`<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" style="width:${W}px;height:${H}px">`;
  for(let i=0;i<=4;i++){const yy=y(i/4,1);plot+=`<line x1="0" y1="${yy}" x2="${W}" y2="${yy}" class="grid"/>`;}
  series.forEach(s=>{const max=s.axis==='right'?maxRight:maxLeft,dim=selected&&s.name!==selected;const points=rows.map((r,i)=>`${x(i)},${y(s.value(r),max)}`).join(' ');plot+=`<polyline points="${points}" fill="none" stroke="${s.color}" stroke-width="${dim?1:2}" opacity="${dim?.15:1}"/>`;rows.forEach((r,i)=>{const raw=s.value(r),user=String(r.run.user_preview||r.session.session_name||'').replace(/\s+/g,' ').trim().slice(0,7)||'无用户消息',iterLabel=r.reqs?('Run '+r.reqs.length+'轮'):('LLM #'+r.req.react_iter),tip={session:r.session.session_name,session_id:r.session.session_id,run_id:r.run.run_id,time:r.req.started_at||r.run.started_at||'',react_iter:iterLabel,user,model:r.req.model||'',metric:s.name,value:num(raw),display:format(raw,s.unit),unit:s.unit};plot+=`<circle class="chart-point" data-tip="${esc(JSON.stringify(tip))}" cx="${x(i)}" cy="${y(raw,max)}" r="4" fill="${s.color}" opacity="${dim?.12:1}"/>`;});});
  rows.forEach((r,i)=>{if(rows.length<=20||i%Math.ceil(rows.length/16)===0){const user=String(r.run.user_preview||r.session.session_name||'').replace(/\s+/g,' ').trim().slice(0,7)||'无用户消息',iterLabel=r.reqs?('Run '+r.reqs.length+'轮'):('LLM #'+r.req.react_iter);plot+=`<text x="${x(i)}" y="${H-25}" text-anchor="middle">${esc(new Date(r.req.started_at||r.run.started_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}))}</text><text x="${x(i)}" y="${H-10}" text-anchor="middle">${esc(user)} · ${esc(iterLabel)}</text>`;}});plot+='</svg>';
  const axisHtml=(side,max,unit,enabled)=>`<div class="y-axis y-axis--${side}" aria-hidden="true">${enabled?[0,1,2,3,4].map(i=>{const v=max*i/4;return`<span style="top:${y(v,max)}px">${esc(format(v,unit))}</span>`;}).join(''):''}</div>`;
  const leftUnit=(leftSeries[0]||visible[0]||series[0]).unit,rightUnit=(rightSeries[0]||series.find(s=>s.axis==='right')||{}).unit;
  const legend='<div class="legend">'+series.map(s=>`<button type="button" data-chart="${esc(target)}" data-series="${esc(s.name)}" class="${selected===s.name?'is-selected':selected?'is-dimmed':''}"><i style="background:${s.color}"></i>${esc(s.name)}</button>`).join('')+'</div>';
  const previous=chartScrollState.get(target);el.innerHTML=`<div class="chart-stage">${axisHtml('left',maxLeft,leftUnit,leftSeries.length>0)}<div class="chart-scroll">${plot}</div>${hasRight?axisHtml('right',maxRight,rightUnit,rightSeries.length>0):''}</div>${legend}`;
  const scrollEl=el.querySelector('.chart-scroll');requestAnimationFrame(()=>{const maxScroll=Math.max(0,scrollEl.scrollWidth-scrollEl.clientWidth);scrollEl.scrollLeft=previous==null||previous.atEnd?maxScroll:Math.min(maxScroll,previous.left);});
  scrollEl.onscroll=()=>chartScrollState.set(target,{left:scrollEl.scrollLeft,atEnd:scrollEl.scrollLeft>=scrollEl.scrollWidth-scrollEl.clientWidth-2});
  el.querySelectorAll('.legend button').forEach(button=>button.onclick=()=>{chartLegendSelection.set(target,selected===button.dataset.series?'':button.dataset.series);renderCharts(flatten(lastData,document.getElementById('session-filter').value));});
}

function formatBytes(value){const n=num(value);if(n>=1048576)return(n/1048576).toFixed(2)+' MB';if(n>=1024)return(n/1024).toFixed(1)+' KB';return Math.round(n)+' B';}

function renderCharts(rows){
  let cumulativeInput=0,cumulativeOutput=0;
  const cumulativeRows=sampleChartRows(rows.map(row=>{
    cumulativeInput+=num((row.req.usage||{}).prompt_tokens||(row.req.context||{}).estimated_tokens);
    cumulativeOutput+=num((row.req.usage||{}).completion_tokens);
    return Object.assign({},row,{cumulativeInput,cumulativeOutput});
  }));
  const chartRows=sampleChartRows(rows);
  const phaseRows=sampleChartRows(phaseChartMode==='run'?runMergedRows(rows):rows);
  lineChart('api-chart',cumulativeRows,[
    {name:'累计输入 token',value:r=>r.cumulativeInput},
    {name:'累计输出 token',value:r=>r.cumulativeOutput},
    {name:'上下文长度',value:r=>num((r.req.context||{}).estimated_tokens)},
  ],'');
  lineChart('phase-chart',phaseRows,PHASE_DEFS.map(([key,label])=>({name:label,value:r=>phaseTotalMs(r,key)})));
  const toolMode=(document.getElementById('tool-chart-mode')||{}).value||'count',toolNames=[];
  rows.forEach(row=>(row.req.tools||[]).forEach(tool=>{const name=String(tool.tool||'tool');if(!toolNames.includes(name))toolNames.push(name);}));
  const toolTotals={},toolRows=sampleChartRows(rows.map(row=>{(row.req.tools||[]).forEach(tool=>{const name=String(tool.tool||'tool');toolTotals[name]=(toolTotals[name]||0)+(toolMode==='duration'?num(tool.duration_ms):1);});return Object.assign({},row,{toolTotals:Object.assign({},toolTotals)});}));
  lineChart('tool-chart',toolRows,toolNames.map(name=>({name,value:r=>num(r.toolTotals[name])})),toolMode==='duration'?'ms':'');
  lineChart('network-chart',chartRows,[
    {name:'请求至首 token',axis:'left',unit:'ms',value:r=>num((r.req.network||{}).request_to_first_token_ms||r.req.first_token_ms)},
    {name:'Transport 总耗时',axis:'left',unit:'ms',value:r=>num((r.req.network||{}).transport_elapsed_ms)},
    {name:'请求流量',axis:'right',unit:'B',value:r=>num((r.req.network||{}).request_bytes)},
    {name:'响应流量（估算）',axis:'right',unit:'B',value:r=>num((r.req.network||{}).response_payload_bytes_estimated||(r.req.network||{}).response_content_length)},
  ]);
}

function eventsHtml(events){
  if(Array.isArray(events)) return events.map(e=>`<div class="event-row"><span>${esc(e.step||'event')}</span><b>${esc(ms(e.ms_since_api_start))}</b><small>${esc(Object.keys(e).filter(k=>!['step','ms_since_api_start','model'].includes(k)).map(k=>k+'='+e[k]).join(' · '))}</small></div>`).join('');
  return Object.keys(events||{}).map(k=>`<div class="event-row"><span>${esc(k)}</span><b>${esc(ms(events[k]))}</b></div>`).join('')||'<div class="empty-inline">暂无子事件</div>';
}

function phaseHtml(session,run,req,name,phase){
  const key=[session.session_id,run.run_id,req.react_iter,name].join('|');
  const opened=openState.has(key)?openState.get(key):(name==='llm_stream');
  const label=(PHASE_DEFS.find(item=>item[0]===name)||[null,name])[1];
  return `<details class="phase" data-open-key="${esc(key)}" ${opened?'open':''}><summary><span>${esc(label)}</span><b>${esc(ms(phase.total_ms))}</b></summary><div>${eventsHtml(phase.events)}</div></details>`;
}

function toolsHtml(session,run,req){
  if(!(req.tools||[]).length)return '';
  const key=[session.session_id,run.run_id,req.react_iter,'tools'].join('|');
  const opened=openState.get(key)===true;
  return `<details class="phase" data-open-key="${esc(key)}" ${opened?'open':''}><summary><span>tools</span><b>${req.tools.length}</b></summary><div>${req.tools.map(t=>`<div class="event-row"><span>${esc(t.tool)}</span><b>${esc(ms(t.duration_ms))}</b><small>${t.failed?'失败':'成功'}</small></div>`).join('')}</div></details>`;
}

const requestKey=row=>[row.session.session_id,row.run.run_id,row.req.react_iter].join('|');

function requestDetailHtml(row){
  if(!row)return '<div class="empty">暂无执行统计</div>';
  const {session,run,req}=row;
  return `<section class="session-block"><header><div><h2>${esc(session.session_name)}</h2><code>${esc(session.session_id)}</code></div><span>${esc(new Date(req.started_at||run.started_at||'').toLocaleString())}</span></header><article class="run-block"><header><div><strong>${esc(run.mode||'chat')}</strong><code>${esc(run.run_id)}</code></div><em class="${esc(run.status||'')}">${esc(run.status||'')}</em></header><div class="request-card"><header><div><strong>LLM #${req.react_iter}</strong><span>${esc(req.model||'')}</span></div><em>${esc(req.status||'')}</em></header>${PHASE_DEFS.map(([n])=>phaseHtml(session,run,req,n,phaseData(req,n))).join('')}${toolsHtml(session,run,req)}</div></article></section>`;
}

function reconcileRunStats(run,reqs){
  const wall=runWallMs(run);
  let startup=reqs.reduce((n,r)=>n+num(r.startup_ms),0);
  if(!startup&&reqs.length){
    const a=Date.parse(reqs[0].started_at||''),b=Date.parse(run.started_at||'');
    if(Number.isFinite(a)&&Number.isFinite(b))startup=Math.max(0,a-b);
  }
  const reqWall=reqs.reduce((n,r)=>n+(num(r.wall_ms)>0?num(r.wall_ms):num(r.duration_ms)+num(((r.phases||{}).pre_api||{}).total_ms)+toolWallMs(r)+num(((r.phases||{}).round_postprocess||{}).total_ms)),0);
  const roundGap=reqs.reduce((n,r)=>n+num(r.round_gap_ms),0);
  const finalPipe=reqs.reduce((n,r)=>n+num(((r.phases||{}).final_pipeline||{}).total_ms),0);
  const api=reqs.reduce((n,r)=>n+num(r.duration_ms),0);
  const toolWall=reqs.reduce((n,r)=>n+toolWallMs(r),0);
  const known=startup+reqWall+roundGap+finalPipe;
  return {wall,startup,reqWall,roundGap,finalPipe,api,toolWall,residual:Math.max(0,wall-known)};
}
function reconcileRowsHtml(stats,wall){
  return [['Run 墙钟',stats.wall],['Run 启动',stats.startup],['Σ 轮次墙钟',stats.reqWall],['Σ 轮间缝隙',stats.roundGap],['Run 收尾（final_pipeline）',stats.finalPipe],['未计时残差',stats.residual],['LLM API 流累计',stats.api],['工具批次墙钟',stats.toolWall]].map(([label,value])=>`<div class="event-row event-row--aggregate"><span>${esc(label)}</span><b>${esc(ms(value))}</b><small>${wall?((value/wall)*100).toFixed(1):'0.0'}%</small></div>`).join('');
}
function runReconcileCard(run,reqs,openRuns){
  const stats=reconcileRunStats(run,reqs);
  const key='reconcile|'+String(run.run_id||'');
  const opened=openRuns||openState.get(key)===true;
  const roundRows=reqs.slice().sort((a,b)=>num(a.react_iter)-num(b.react_iter)).map(r=>{
    const rWall=num(r.wall_ms)>0?num(r.wall_ms):(num(r.duration_ms)+num(((r.phases||{}).pre_api||{}).total_ms)+toolWallMs(r)+num(((r.phases||{}).round_postprocess||{}).total_ms));
    return `<div class="event-row"><span>LLM #${esc(r.react_iter||'')}</span><b>${esc(ms(rWall))}</b><small>API ${esc(ms(r.duration_ms))} · 工具 ${esc(ms(toolWallMs(r)))} · 轮间 ${esc(ms(r.round_gap_ms))} · 收尾 ${esc(ms(((r.phases||{}).final_pipeline||{}).total_ms))}</small></div>`;
  }).join('')||'<div class="empty-inline">暂无轮次</div>';
  return `<details class="phase" data-open-key="${esc(key)}" ${opened?'open':''}><summary><span>Run 对账 · ${esc(String(run.run_id||'').slice(0,8))}</span><b>${esc(ms(stats.wall))}</b><small>${reqs.length} 轮 · ${esc(run.status||'')}</small></summary><div class="request-card">${reconcileRowsHtml(stats,stats.wall)}<details class="phase"><summary><span>轮次明细</span><b>${reqs.length} 轮</b></summary><div>${roundRows}</div></details></div></details>`;
}
function runReconcileHtml(rows, mode, openRuns){
  const runs=new Map();
  rows.forEach(row=>{
    const key=String(row.session.session_id||'')+'|'+String(row.run.run_id||'');
    if(!runs.has(key))runs.set(key,{session:row.session,run:row.run,reqs:[]});
    runs.get(key).reqs.push(row.req);
  });
  if(!runs.size)return '';
  const groups=Array.from(runs.values());
  if(mode!=='session'){
    return groups.map(g=>runReconcileCard(g.run,g.reqs,openRuns)).join('');
  }
  const sessions=new Map();
  groups.forEach(g=>{
    const sid=String(g.session.session_id||'');
    if(!sessions.has(sid))sessions.set(sid,{session:g.session,runs:[]});
    sessions.get(sid).runs.push(g);
  });
  return Array.from(sessions.values()).map(({session,runs})=>{
    const key='reconcile-session|'+String(session.session_id||'');
    const opened=openState.get(key)!==false;
    const statsList=runs.map(g=>reconcileRunStats(g.run,g.reqs));
    const wall=statsList.reduce((n,s)=>n+s.wall,0);
    const stats={
      wall,
      startup:statsList.reduce((n,s)=>n+s.startup,0),
      reqWall:statsList.reduce((n,s)=>n+s.reqWall,0),
      roundGap:statsList.reduce((n,s)=>n+s.roundGap,0),
      finalPipe:statsList.reduce((n,s)=>n+s.finalPipe,0),
      api:statsList.reduce((n,s)=>n+s.api,0),
      toolWall:statsList.reduce((n,s)=>n+s.toolWall,0),
      residual:Math.max(0,wall-statsList.reduce((n,s)=>n+(s.startup+s.reqWall+s.roundGap+s.finalPipe),0)),
    };
    const totalRounds=runs.reduce((n,g)=>n+g.reqs.length,0);
    return `<details class="phase" data-open-key="${esc(key)}" ${opened?'open':''}><summary><span>会话 ${esc(session.session_name||session.session_id||'')}</span><b>${esc(ms(wall))}</b><small>${runs.length} 个 Run · ${totalRounds} 轮</small></summary><div class="request-card">${reconcileRowsHtml(stats,wall)}${runs.map(g=>runReconcileCard(g.run,g.reqs,openRuns)).join('')}</div></details>`;
  }).join('');
}

function cumulativeDetailHtml(rows){
  if(!rows.length)return '<div class="empty">暂无执行统计</div>';
  const phaseTotals={},phaseEvents={};
  rows.forEach(row=>PHASE_DEFS.forEach(([name])=>{
    const phase=phaseData(row.req,name);phaseTotals[name]=(phaseTotals[name]||0)+num(phase.total_ms);
    phaseEvents[name]=phaseEvents[name]||{};
    Object.entries(phase.events||{}).forEach(([event,value])=>phaseEvents[name][event]=(phaseEvents[name][event]||0)+num(value));
  }));
  const allPhaseTotal=Object.values(phaseTotals).reduce((n,v)=>n+num(v),0);
  const cumulativeEvents=(name)=>{const phaseTotal=num(phaseTotals[name]);return Object.entries(phaseEvents[name]||{}).map(([event,total])=>`<div class="event-row event-row--aggregate"><span>${esc(event)}</span><b>平均 ${esc(ms(num(total)/rows.length))}</b><small>累计 ${esc(ms(total))} · 占本阶段 ${phaseTotal?((num(total)/phaseTotal)*100).toFixed(1):'0.0'}%</small></div>`).join('')||'<div class="empty-inline">暂无子事件</div>';};
  return `<section class="session-block"><header><div><h2>累计总值</h2><code>${esc(document.getElementById('session-filter').value?'当前会话筛选':'全部会话')}</code></div><span>${rows.length} 次 LLM 请求</span></header><div class="detail-toolbar"><div><h2>Run 对账</h2><span>合并显示，减少卡片数量</span></div><label>显示方式<select id="reconcile-mode" aria-label="Run 对账显示方式"><option value="session">按会话合并</option><option value="run">按 Run 展开</option></select></label><button type="button" id="reconcile-toggle">全部展开</button></div>${runReconcileHtml(rows,reconcileMode,reconcileAllOpen)}<article class="run-block"><div class="request-card">${PHASE_DEFS.map(([name,label])=>{const total=num(phaseTotals[name]),avg=total/rows.length,ratio=allPhaseTotal?total/allPhaseTotal*100:0,key=`total|${name}`,opened=openState.get(key)===true;return`<details class="phase" data-open-key="${esc(key)}" ${opened?'open':''}><summary><span>${esc(label)}</span><b>平均 ${esc(ms(avg))} · 累计 ${esc(ms(total))} · 占比 ${ratio.toFixed(1)}%</b></summary><div>${cumulativeEvents(name)}</div></details>`;}).join('')}</div></article></section>`;
}

function renderTopCards(rows,selectedRow,isTotal){
  const summary=document.getElementById('summary');if(!summary)return;
  if(!rows.length){summary.innerHTML='';return;}
  let cards;
  if(isTotal){
    const input=rows.reduce((n,r)=>n+num((r.req.usage||{}).prompt_tokens||(r.req.context||{}).estimated_tokens),0);
    const output=rows.reduce((n,r)=>n+num((r.req.usage||{}).completion_tokens),0);
    const api=rows.reduce((n,r)=>n+num(r.req.duration_ms),0);
    const tools=rows.reduce((n,r)=>n+(r.req.tools||[]).length,0);
    const ttftRows=rows.filter(r=>Number.isFinite(Number(r.req.first_token_ms))),avgTtft=ttftRows.length?ttftRows.reduce((n,r)=>n+num(r.req.first_token_ms),0)/ttftRows.length:0;
    const traffic=rows.reduce((n,r)=>n+num((r.req.network||{}).request_bytes)+num((r.req.network||{}).response_payload_bytes_estimated||(r.req.network||{}).response_content_length),0);
    const observed=uniqueObservedRuns(rows);
    const changedFiles=observed.reduce((n,run)=>n+(run.file_changes||[]).length,0);
    cards=[['会话数',new Set(rows.map(r=>r.session.session_id)).size],['LLM 请求',rows.length],['Run 总耗时（墙钟）',ms(runWallTotalMs(rows))],['LLM API 流累计',ms(api)],['平均首 token',ms(avgTtft)],['累计输入 token',input.toLocaleString()],['累计输出 token',output.toLocaleString()],['工具调用总数',tools.toLocaleString()],['累计网络流量',formatBytes(traffic)]];
    cards.push(['文件变更',changedFiles.toLocaleString()]);
  }else{
    const req=selectedRow.req,usage=req.usage||{},ctx=req.context||{};
    const network=req.network||{},traffic=num(network.request_bytes)+num(network.response_payload_bytes_estimated||network.response_content_length);
    const observed=observedRun(selectedRow)||{};
    cards=[['LLM API 流耗时',ms(req.duration_ms)],['首 token',ms(req.first_token_ms)],['输入 token',num(usage.prompt_tokens||ctx.estimated_tokens).toLocaleString()],['输出 token',num(usage.completion_tokens).toLocaleString()],['上下文长度',`${ctx.estimated_tokens??'—'} / ${ctx.context_window??'—'}`],['工具调用',(req.tools||[]).length],['网络等待',ms(network.request_to_first_token_ms||req.first_token_ms)],['网络流量',formatBytes(traffic)]];
    cards.push(
      ['文件变更',(observed.file_changes||[]).length],
      ['心跳',observed.heartbeat_at||'—'],
      ['运行状态',observed.status||selectedRow.run.status||'—'],
    );
    if(num(req.wall_ms)>0)cards.push(['请求墙钟',ms(req.wall_ms)]);
    if(num(req.round_gap_ms)>0)cards.push(['前序轮间',ms(req.round_gap_ms)]);
  }
  summary.innerHTML=cards.map(([label,value])=>`<div><span>${esc(label)}</span><b>${esc(value)}</b></div>`).join('');
}

function render(data){
  const visibleSessions=(data.sessions||[]).filter(s=>!['s-followup','s-final-first'].includes(String(s.session_id||'').toLowerCase())&&!['s-followup','s-final-first'].includes(String(s.session_name||'').toLowerCase()));
  data=Object.assign({},data,{sessions:visibleSessions});lastData=data; const filter=document.getElementById('session-filter');
  filter.innerHTML='<option value="">全部会话</option>'+sessionIndex.map(s=>`<option value="${esc(s.id)}">${esc(s.name)} · ${esc(String(s.id).slice(0,8))}</option>`).join('');
  const selected=activeSessionId&&sessionIndex.some(s=>s.id===activeSessionId)?activeSessionId:'';
  filter.value=selected;
  const rows=flatten(data,selected); renderCharts(rows);
  const requestFilter=document.getElementById('request-filter'),latest=rows.length?rows[rows.length-1]:null;
  if(selectedRequestKey!=='__total__'&&!rows.some(row=>requestKey(row)===selectedRequestKey))selectedRequestKey='__total__';
  requestFilter.innerHTML='<option value="__total__">累计总值</option>'+rows.slice().reverse().map(row=>{const user=String(row.run.user_preview||row.session.session_name||'').replace(/\s+/g,' ').trim().slice(0,7)||'无用户消息';return`<option value="${esc(requestKey(row))}">${esc(user)} · LLM #${row.req.react_iter} · ${esc(new Date(row.req.started_at||row.run.started_at||'').toLocaleString())}</option>`;}).join('');
  requestFilter.value=selectedRequestKey;
  const selectedRow=rows.find(row=>requestKey(row)===selectedRequestKey)||latest;
  renderTopCards(rows,selectedRow,selectedRequestKey==='__total__');
  document.getElementById('dashboard-body').innerHTML=selectedRequestKey==='__total__'?cumulativeDetailHtml(rows):requestDetailHtml(selectedRow);
  document.querySelectorAll('details[data-open-key]').forEach(d=>{
    const summary=d.querySelector('summary');
    if(summary)summary.addEventListener('click',event=>{
      event.preventDefault();event.stopPropagation();
      const next=!d.open;openState.set(d.dataset.openKey,next);d.open=next;
    });
  });
  const reconcileModeEl=document.getElementById('reconcile-mode');
  if(reconcileModeEl){
    reconcileModeEl.value=reconcileMode;
    reconcileModeEl.addEventListener('change',()=>{reconcileMode=reconcileModeEl.value;render(lastData);});
  }
  const reconcileToggle=document.getElementById('reconcile-toggle');
  if(reconcileToggle){
    reconcileToggle.textContent=reconcileAllOpen?'全部折叠':'全部展开';
    reconcileToggle.addEventListener('click',()=>{reconcileAllOpen=!reconcileAllOpen;render(lastData);});
  }
}

async function loadSessionIndex(){
  try{
    const r=await fetch('/api/execution-metrics/sessions',{cache:'no-store'});
    if(!r.ok)throw new Error('sessions endpoint unavailable');
    const p=await r.json();
    if(!p.ok)throw new Error(p.error||'加载失败');
    fullFallback=false;
    sessionIndex=((p.data&&p.data.sessions)||[]).filter(s=>{
      const id=String(s.session_id||'').toLowerCase(),name=String(s.session_name||'').toLowerCase();
      return !['s-followup','s-final-first'].includes(id)&&!['s-followup','s-final-first'].includes(name);
    }).map(s=>({id:s.session_id,name:s.session_name||s.session_id}));
    return;
  }catch(_e){}
  // 旧后端没有 sessions 轻量接口时回退全量加载，保证功能可用。
  const r=await fetch('/api/execution-metrics',{cache:'no-store'});
  const p=await r.json();
  if(!r.ok||!p.ok)throw new Error(p.error||'加载失败');
  fullFallback=true;
  sessionIndex=((p.data&&p.data.sessions)||[]).filter(s=>{
    const id=String(s.session_id||'').toLowerCase(),name=String(s.session_name||'').toLowerCase();
    return !['s-followup','s-final-first'].includes(id)&&!['s-followup','s-final-first'].includes(name);
  }).map(s=>({id:s.session_id,name:s.session_name||s.session_id}));
}
async function loadMetrics(){
  const url=(activeSessionId&&!fullFallback)?('/api/execution-metrics?session_id='+encodeURIComponent(activeSessionId)):'/api/execution-metrics';
  const r=await fetch(url,{cache:'no-store'});
  const p=await r.json();
  if(!r.ok||!p.ok)throw new Error(p.error||'加载失败');
  return p.data||{sessions:[]};
}
async function refresh(){
  if(inflight)return;
  const controller=new AbortController();inflight=controller;
  try{
    await loadSessionIndex();
    if(!sessionDefaultResolved){
      sessionDefaultResolved=true;
      let saved='';
      try{saved=localStorage.getItem(SESSION_FILTER_KEY)||'';}catch(_e){}
      if(saved&&sessionIndex.some(s=>s.id===saved))activeSessionId=saved;
      else if(sessionIndex.length)activeSessionId=sessionIndex[0].id;
    }else if(activeSessionId&&!sessionIndex.some(s=>s.id===activeSessionId)){
      activeSessionId=sessionIndex.length?sessionIndex[0].id:'';
    }
    const data=await loadMetrics();
    render(data);
  }
  catch(e){
    if(e.name!=='AbortError')document.getElementById('dashboard-body').innerHTML=`<div class="empty">加载失败：${esc(e.message||e)}</div>`;
  }
  finally{
    if(inflight===controller)inflight=null;
    clearTimeout(refreshTimer);
    // 自动轮询仅用于单会话（轻量快照）；“全部会话”是全量聚合，改为手动刷新，避免大载荷卡死页面。
    if(activeSessionId&&!fullFallback){refreshTimer=setTimeout(refresh,REFRESH_DELAY_MS);}
  }
}
document.getElementById('session-filter').addEventListener('change',event=>{
  activeSessionId=event.target.value;
  selectedRequestKey='__total__';
  try{localStorage.setItem(SESSION_FILTER_KEY,activeSessionId);}catch(_e){}
  refresh();
});
document.getElementById('request-filter').addEventListener('change',event=>{selectedRequestKey=event.target.value;render(lastData);});
document.getElementById('phase-chart-mode').addEventListener('change',event=>{phaseChartMode=event.target.value;renderCharts(flatten(lastData,document.getElementById('session-filter').value));});
document.getElementById('tool-chart-mode').addEventListener('change',()=>renderCharts(flatten(lastData,document.getElementById('session-filter').value)));
document.getElementById('refresh-btn').addEventListener('click',refresh);
document.getElementById('back-to-chat-btn').addEventListener('click',()=>{
  if(window.opener && !window.opener.closed){
    try{window.opener.focus();window.close();return;}catch(_error){}
  }
  location.href='/';
});
const chartTooltip=document.getElementById('chart-tooltip');
document.addEventListener('pointerover',event=>{
  const point=event.target.closest&&event.target.closest('.chart-point');
  if(!point||!chartTooltip)return;
  try{
    const t=JSON.parse(point.dataset.tip||'{}');
    chartTooltip.innerHTML=`<strong>${esc(t.metric)}</strong><b>${esc(t.display)}</b><dl><dt>会话</dt><dd>${esc(t.session)}</dd><dt>时间</dt><dd>${esc(t.time?new Date(t.time).toLocaleString():'—')}</dd><dt>执行轮次</dt><dd>${esc(t.user||'无用户消息')} · ${esc(typeof t.react_iter==='number'?('LLM #'+t.react_iter):t.react_iter)}</dd><dt>模型</dt><dd>${esc(t.model||'—')}</dd><dt>Run ID</dt><dd>${esc(t.run_id||'—')}</dd><dt>Session ID</dt><dd>${esc(t.session_id||'—')}</dd></dl>`;
    chartTooltip.classList.add('is-visible');chartTooltip.setAttribute('aria-hidden','false');
  }catch(_error){}
});
document.addEventListener('pointermove',event=>{
  if(!chartTooltip||!chartTooltip.classList.contains('is-visible'))return;
  const gap=14,w=chartTooltip.offsetWidth||280,h=chartTooltip.offsetHeight||180;
  chartTooltip.style.left=Math.max(8,Math.min(innerWidth-w-8,event.clientX+gap))+'px';
  chartTooltip.style.top=Math.max(8,Math.min(innerHeight-h-8,event.clientY+gap))+'px';
});
document.addEventListener('pointerout',event=>{
  if(!chartTooltip)return;
  const point=event.target.closest&&event.target.closest('.chart-point');
  if(point){chartTooltip.classList.remove('is-visible');chartTooltip.setAttribute('aria-hidden','true');}
});
refresh();
