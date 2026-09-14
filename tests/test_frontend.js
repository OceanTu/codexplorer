// Dependency-free UI contract: exercise the actual render and click handlers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const elements = new Map(), handlers = new Map(), requests = [];
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, {innerHTML:'', value:'', style:{},
    classList:{toggle(){}}, addEventListener:(event,handler)=>handlers.set(selector+':'+event,handler),
    querySelector:sub=>element(selector+' '+sub), setAttribute(){}, removeAttribute(){}});
  return elements.get(selector);
};
const panel = {dataset:{requestId:'response:r1'}, innerHTML:'', contains:()=>false, querySelectorAll:()=>[]};
const document = {
  querySelector: element,
  querySelectorAll(selector) {
    if (selector === '.request-conversation')
      return element('#content').innerHTML.includes('class="request-conversation"') ? [panel] : [];
    return [];
  },
  addEventListener:(event, handler)=>handlers.set(event, handler)
};
const sandbox = {document, window:{addEventListener(){}, scrollTo(){}},
  location:{hash:''}, URLSearchParams, AbortController, setInterval(){}, setTimeout, clearTimeout,
  fetch:()=>new Promise(()=>{}), console};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), sandbox);
sandbox.fixture = {summary:{requests:2,input_tokens:200,cached_input_tokens:100,output_tokens:40,total_tokens:240},
  executions:[],requests:[1,2].map(number=>({number,id:'response:r'+number,response_id:'r'+number,
    thread_id:'s',turn_id:'t',kind:'exact',model:'test',input_tokens:100,cached_input_tokens:50,
    output_tokens:20,total_tokens:120,occurred_at:1}))};
vm.runInContext("state.kind='turn'; state.session='s'; state.turn='t'; state.data=fixture; render();", sandbox);
assert.match(element('#content').innerHTML, /请求明细/);
assert.match(element('#content').innerHTML, /输入上下文/);
assert.doesNotMatch(element('#content').innerHTML, /对话时间线|conversation-timeline|turn-timeline|request-conversation/);
const detail = {context:[],input:[],output:[],tool_results:[],warnings:[],note:'interval'};
sandbox.fetch = async url => { requests.push(url); return {ok:true,json:async()=>detail}; };
handlers.get('click')({target:{closest:selector=>selector==='[data-request]'?{dataset:{request:'response:r1'}}:null}});
setImmediate(async()=>{
  assert.deepEqual(requests, ['/api/requests/response%3Ar1/messages']);
  assert.match(element('#content').innerHTML, /aria-expanded="true"/);
  assert.equal((element('#content').innerHTML.match(/class="request-conversation"/g)||[]).length, 1);
  assert.match(panel.innerHTML, /上行 · 新增输入/);
  assert.match(panel.innerHTML, /下行 · 助手输出/);
  assert.doesNotMatch(element('#content').innerHTML, /对话时间线|conversation-timeline|turn-timeline/);
  // Manual refresh must evict a previously cached 410 and reload only the open row.
  vm.runInContext("messageCache.set('response:r1', {error:'stale position'});", sandbox);
  sandbox.location.hash = '#session/s/turn/t';
  sandbox.fetch = async url => {
    requests.push(url);
    const data = url==='/api/sync'?{requested_at:1}:url==='/api/status'?{scan:{started_at:2}}:
      url==='/api/options'?{models:[],projects:[]}:url.includes('/messages')?detail:sandbox.fixture;
    return {ok:true,json:async()=>data};
  };
  await handlers.get('#refresh:click')();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.filter(url=>url.includes('/messages')).length, 2);
  assert.doesNotMatch(panel.innerHTML, /stale position/);
  console.log('Turn UI: compact view, lazy request expansion and refresh recovery passed');
});
