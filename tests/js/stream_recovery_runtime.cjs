const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const src = fs.readFileSync(path.resolve(__dirname, '../../frontend/src/app/modules/sse-handling.js'), 'utf8');
function fn(name, source = src) {
  const match = source.match(new RegExp('(?:async )?function ' + name + '\\([^]*?^}', 'm'));
  assert(match, name);
  return match[0];
}
function fixture() {
  let visible = false, requests = 0, renders = 0, userIndex = 10, count = 11, aborted = 0;
  const stream = {isConnected:true};
  const ctx = {runId:'run-1',stream,streamConsuming:true,streamEventIndex:11,lastBusinessEventAt:Date.now()-40000};
  let run = {runId:'run-1',ctx,controller:{abort(){aborted++;}}};
  const c = vm.createContext({
    console,Date,Set,Map,Promise,AbortController, currentSessionId:'s',messageLoadEpoch:0,
    finalRecoveryBySession:new Map(),streamHistoryRecoveryBySession:new Set(),
    getVisibleChatStream:()=>stream,latestVisibleUserEventIndex:()=>userIndex,
    hasVisibleFinalAfterUser:()=>visible,findStoredFinalAfterUser:()=>null,
    getSessionRunState:()=>run, clearSessionRunState:()=>{run=null;},
    getRunAbortReason:()=>'',scheduleActiveSessionReconnect:()=>{},
    isServerStreamActive:()=>true,reconcileRunStateFromServer:async()=>{},
    getUiEventCount:async()=>count,markRunAbortReason:()=>{},
    sleepMs:async()=>{},isMyAgentFeatureEnabled:()=>true,
    fetchWithTimeout:async()=>{requests++;return {ok:true,json:async()=>({range_start:11,events:[{type:'status'},{type:'final',content:'done',run_id:'run-1'}]})};},
    applyMessageEvent:(_sid,event,index)=>({event,index,type:event.type}),
    renderFinalRecordIfMissing:(_sid,_ctx,_stream,record)=>{assert.equal(record.index,12);renders++;visible=true;return true;},
    setInterval:()=>1,clearInterval:()=>{},setTimeout:()=>1,clearTimeout:()=>{},
  });
  for (const name of ['markRunFinalSeen','ensureFinalVisibleAfterRunIfEnabled','ensureFinalVisibleAfterRun','checkSessionStreamProgress','consumeAgentSseResponse']) vm.runInContext(fn(name),c);
  return {c,ctx,stream,setRun:r=>{run=r;},setUser:i=>{userIndex=i;},setCount:n=>{count=n;},stats:()=>({requests,renders,aborted,run})};
}
async function main() {
  const a=fixture();
  assert.deepEqual(await Promise.all([a.c.ensureFinalVisibleAfterRun('s',a.ctx,{}),a.c.ensureFinalVisibleAfterRun('s',a.ctx,{})]),[true,true]);
  assert.equal(a.stats().requests,1);assert.equal(a.stats().renders,1);assert.equal(a.ctx.seenFinal,true);
  assert.equal(await a.c.ensureFinalVisibleAfterRun('s',a.ctx,{}),true);assert.equal(a.stats().requests,1);
  for (const change of [f=>{f.c.currentSessionId='other';},f=>f.setUser(20),f=>{f.c.messageLoadEpoch++;},f=>f.setRun({runId:'run-2',ctx:{}})]) {
    const f=fixture();let release;
    f.c.fetchWithTimeout=()=>new Promise(resolve=>{release=resolve;});
    const result=f.c.ensureFinalVisibleAfterRun('s',f.ctx,{});
    change(f);release({ok:true,json:async()=>({range_start:11,events:[{type:'status'},{type:'final'}]})});
    assert.equal(await result,false);assert.equal(f.stats().renders,0);
  }
  const retry=fixture();let calls=0;
  const success=retry.c.fetchWithTimeout;
  retry.c.fetchWithTimeout=async()=>{calls++;return calls===1?{ok:false,status:503}:success();};
  assert.equal(await retry.c.ensureFinalVisibleAfterRun('s',retry.ctx,{}),true);assert.equal(calls,2);
  const bodyTimeout=fixture();let cancelledTimers=0;
  bodyTimeout.c.setTimeout=callback=>{queueMicrotask(callback);return 1;};
  bodyTimeout.c.clearTimeout=()=>{cancelledTimers++;};
  bodyTimeout.c.fetchWithTimeout=async(_url,options)=>({ok:true,json:()=>new Promise((_resolve,reject)=>{
    if(options.signal.aborted)reject(new Error('body timeout'));
    else options.signal.addEventListener('abort',()=>reject(new Error('body timeout')));
  })});
  await assert.rejects(bodyTimeout.c.ensureFinalVisibleAfterRun('s',bodyTimeout.ctx,{}),/body timeout/);
  assert.equal(cancelledTimers,3);
  const newer=fixture();
  newer.c.fetchWithTimeout=async()=>({ok:true,json:async()=>({range_start:11,events:[{type:'user'},{type:'final'}]})});
  assert.equal(await newer.c.ensureFinalVisibleAfterRun('s',newer.ctx,{}),false);assert.equal(newer.stats().renders,0);
  const bounded=fixture();
  bounded.c.fetchWithTimeout=async()=>({ok:true,json:async()=>({requested_range_start:30,range_start:100,events:[{type:'final'}]})});
  assert.equal(await bounded.c.ensureFinalVisibleAfterRun('s',bounded.ctx,{}),false);assert.equal(bounded.stats().renders,0);
  const quiet=fixture();await quiet.c.checkSessionStreamProgress('s',quiet.ctx);assert.equal(quiet.stats().aborted,0);
  quiet.setCount(13);await quiet.c.checkSessionStreamProgress('s',quiet.ctx);
  assert.equal(quiet.stats().aborted,1);assert(quiet.c.streamHistoryRecoveryBySession.has('s'));
  const active=fixture();active.setCount(13);active.ctx.lastBusinessEventAt=Date.now();
  await active.c.checkSessionStreamProgress('s',active.ctx);assert.equal(active.stats().aborted,0);
  const closed=fixture();let cleared=0;
  closed.c.clearInterval=()=>{cleared++;};
  closed.c.consumeAgentSseResponseInner=async()=>{throw new Error('lost socket');};
  await assert.rejects(closed.c.consumeAgentSseResponse({ok:true,body:{},headers:{get:()=> 'text/event-stream'}},closed.ctx,'s',11),/lost socket/);
  assert.equal(cleared,1);assert.equal(closed.ctx.streamConsuming,false);assert.equal(closed.stats().run,null);
  assert(closed.c.streamHistoryRecoveryBySession.has('s'));
  const replacing=fixture();
  replacing.c.consumeAgentSseResponseInner=async()=>{replacing.setRun({runId:'run-2',ctx:{}});};
  await replacing.c.consumeAgentSseResponse({ok:true,body:{},headers:{get:()=> 'text/event-stream'}},replacing.ctx,'s',11);
  assert.equal(replacing.stats().run.runId,'run-2');
  const terminal=fixture();let sealed=0;
  const terminalRun=terminal.stats().run;terminalRun.submitted=true;
  // reconcile 兜底只允许在流已不再消费（真结束且终端丢失）时收尾；
  // 健康流（streamConsuming 且未见终态）不得被误杀，见 session-management.js streamAlive 守卫。
  // 这里用 reattached + 不再消费模拟“观察者重连发现服务端已结束”的真收尾场景。
  terminalRun.reattached = true;
  terminal.ctx.streamConsuming = false;
  Object.assign(terminal.c,{
    sessionStore:{sessionOrder:[],get:()=>null,runsBySession:new Map([['s',terminalRun]]),activeRunInfoBySession:new Map()},
    fetchSessionsStateSnapshot:async()=>({}),applySessionSnapshot:()=>{},updateSidebarRuntimeStatus:()=>{},
    abortSessionRun:()=>terminal.setRun(null),endRunForClient:(_sid,ctx,opts)=>{assert.equal(ctx,terminal.ctx);assert.equal(opts.drainFollowup,false);assert.equal(opts.collapseProcess,false);sealed++;},
    syncSessionListIndicatorClasses:()=>{},setSendButtonState:()=>{},renderSessionListIfChanged:()=>{},
  });
  const sessions=fs.readFileSync(path.resolve(__dirname,'../../frontend/src/app/modules/session-management.js'),'utf8');
  vm.runInContext(fn('reconcileRunStateFromServer',sessions),terminal.c);
  await terminal.c.reconcileRunStateFromServer({});assert.equal(sealed,1);assert.equal(terminal.stats().run,null);
  // 健康流不得被 reconcile 误杀：本地仍在消费且未见终态时，abort/endRun 均不得触发。
  const healthy=fixture();let healthySealed=0;
  const healthyRun=healthy.stats().run;healthyRun.submitted=true;
  // fixture 默认 streamConsuming=true 且 terminalSeen 未设置，正好模拟首步 15s 窗口的健康流。
  Object.assign(healthy.c,{
    sessionStore:{sessionOrder:[],get:()=>null,runsBySession:new Map([['s',healthyRun]]),activeRunInfoBySession:new Map()},
    fetchSessionsStateSnapshot:async()=>({}),applySessionSnapshot:()=>{},updateSidebarRuntimeStatus:()=>{},
    abortSessionRun:()=>{healthy.setRun(null);},endRunForClient:()=>{healthySealed++;},
    syncSessionListIndicatorClasses:()=>{},setSendButtonState:()=>{},renderSessionListIfChanged:()=>{},
  });
  vm.runInContext(fn('reconcileRunStateFromServer',sessions),healthy.c);
  await healthy.c.reconcileRunStateFromServer({});assert.equal(healthySealed,0);assert.notEqual(healthy.stats().run,null);
  const long=fixture();let poll,checks=0,stops=0;
  const scroll=fs.readFileSync(path.resolve(__dirname,'../../frontend/src/app/modules/session-scroll-history.js'),'utf8');
  Object.assign(long.c,{streamPollTimer:null,setInterval:f=>{poll=f;return 1;},clearInterval:()=>{stops++;},
    isSessionRunning:()=>true,reconcileRunStateFromServer:async()=>{checks++;},
    document:{visibilityState:'visible'},syncSessionListIndicatorClasses:()=>{},setSendButtonState:()=>{},
  });
  vm.runInContext(fn('clearStreamPoll',scroll)+fn('maybeStartStreamPollForSession',scroll),long.c);
  long.c.maybeStartStreamPollForSession('s',{});
  for(let i=0;i<40;i++){poll();await new Promise(setImmediate);}
  assert.equal(checks,40);assert.equal(stops,0,'a long run must keep reconciling past five minutes');
  console.log('stream recovery runtime: passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
