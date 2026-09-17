// Dependency-free UI contract: exercise the actual render and click handlers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const elements = new Map(), handlers = new Map(), requests = [];
const preferences = new Map(), bodyClasses = new Set();
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, {innerHTML:'', value:'', style:{},
    focus(){}, classList:{toggle(){}}, addEventListener:(event,handler)=>handlers.set(selector+':'+event,handler),
    querySelector:sub=>element(selector+' '+sub), setAttribute(){}, removeAttribute(){}});
  return elements.get(selector);
};
const panel = {dataset:{requestId:'response:r1'}, innerHTML:'', contains:()=>false, querySelectorAll:()=>[]};
const document = {
  body:{classList:{toggle:(name,on)=>on?bodyClasses.add(name):bodyClasses.delete(name)}},
  getElementById:()=>null,
  querySelector: element,
  querySelectorAll(selector) {
    if (selector === '.request-conversation')
      return element('#content').innerHTML.includes('class="request-conversation"') ? [panel] : [];
    return [];
  },
  addEventListener:(event, handler)=>handlers.set(event, handler)
};
const sandbox = {document, window:{addEventListener:(event,handler)=>handlers.set('window:'+event,handler), scrollTo(){}},
  matchMedia:()=>({matches:false}),
  localStorage:{getItem:key=>preferences.get(key)??null,setItem:(key,value)=>preferences.set(key,value)},
  location:{hash:''}, URLSearchParams, AbortController, setInterval(){}, setTimeout, clearTimeout,
  fetch:()=>new Promise(()=>{}), console};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/pricing.js'), 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), sandbox);

const row=(thread,turn,input,at,agent=true)=>({id:thread+':'+at,thread_id:thread,turn_id:turn,
  is_agent:agent,source:agent?'subagent':'user',thread_title:agent?'worker <one>':'Main',
  input_tokens:input,occurred_at:at,number:at,model:'test',total_tokens:input+10,
  cached_input_tokens:0,output_tokens:10,kind:'exact'});
sandbox.fixture={summary:{requests:5,total_tokens:2060,input_tokens:2010,output_tokens:50},
  executions:[],requests:[row('agent-a','a1',400,1),row('main','m1',100,2,false),
    row('agent-b','b1',900,3),row('agent-a','a2',500,4),row('main','m2',110,5,false)]};

vm.runInContext("state.kind='turn'; state.data=fixture; render();",sandbox);
let html=element('#content').innerHTML;
const series=html=>[...html.matchAll(/<polyline data-context-series="([^"]+)"[^>]*points="([^"]+)"[^>]*stroke="([^"]+)"/g)];
assert.match(html, /id="context-agent"/);
assert.equal(vm.runInContext('state.contextAgent',sandbox),'','Show all agents by default');
assert.equal(series(html).length,3,'Each agent has an independent line in the same chart');
assert.equal(new Set(series(html).map(m=>m[3])).size,3,'Agents have distinct colors');
assert.match(html, /5 条记录的输入 token 趋势/);
assert.equal((html.match(/<option value="agent-a"/g)||[]).length,1,'Resumed executions share one thread series');
assert.match(html, /worker &lt;one&gt;/);
const original=series(html), a=original.find(m=>m[1]==='agent-a');
assert.equal(a[2].split(' ').length,2);
assert.equal(a[2].split(' ')[0].split(',')[0],'20');
assert.equal(a[2].split(' ')[1].split(',')[0],'740','All agents share a real time axis');
const snapshot=JSON.stringify(sandbox.fixture);
vm.runInContext("state.open.add('agent-a:1'); state.page=2;",sandbox);
handlers.get('change')({target:{id:'context-agent',value:'agent-a'}});
html=element('#content').innerHTML;
assert.match(html, /value="agent-a" selected/);
assert.equal(series(html).length,1);
assert.deepEqual(series(html)[0].slice(1),a.slice(1),'Single-agent view keeps its color and both axis scales');
assert.match(html, /2 条记录的输入 token 趋势，峰值 500/);
assert.equal(JSON.stringify(sandbox.fixture),snapshot);
assert.equal(vm.runInContext('state.page',sandbox),0,'Agent selection resets pagination');
assert.deepEqual([...html.matchAll(/data-request="([^"]+)"/g)].map(m=>m[1]),['agent-a:1','agent-a:4']);
assert.match(element('#turn-navigation').innerHTML,/2 条请求/);
assert.doesNotMatch(element('#turn-navigation').innerHTML,/main:|agent-b:/);
assert.equal(vm.runInContext("state.open.has('agent-a:1')",sandbox),true);
assert.equal(requests.length,0);
vm.runInContext('render()',sandbox);
assert.equal(vm.runInContext('state.contextAgent',sandbox),'agent-a');
const clickLegend=id=>handlers.get('click')({target:{closest:selector=>selector==='[data-context-agent]'?{dataset:{contextAgent:id}}:null}});
clickLegend('agent-b');
assert.equal(vm.runInContext('state.contextAgent',sandbox),'agent-b');
assert.equal((element('#content').innerHTML.match(/data-request=/g)||[]).length,1);
assert.equal(vm.runInContext("state.open.has('agent-a:1')",sandbox),false,'Close conversation outside selected agent');
assert.match(element('#content').innerHTML,/<circle/,'Single sample stays visible');
clickLegend('agent-b');
assert.equal(vm.runInContext('state.contextAgent',sandbox),'','Click selected legend again to restore all');
assert.equal(series(element('#content').innerHTML).length,3);
assert.equal((element('#content').innerHTML.match(/data-request=/g)||[]).length,5,'Restore all requests');
handlers.get('change')({target:{id:'context-agent',value:'agent-a'}});
vm.runInContext("state.data={...fixture,requests:fixture.requests.filter(r=>r.thread_id!=='agent-a')}; render();",sandbox);
assert.equal(vm.runInContext('state.contextAgent',sandbox),'','Missing selection falls back to all');
assert.equal(series(element('#content').innerHTML).find(m=>m[1]==='agent-b')[3],original.find(m=>m[1]==='agent-b')[3]);
vm.runInContext("state.data={...fixture,requests:fixture.requests.filter(r=>r.is_agent)}; render();",sandbox);
assert.equal(series(element('#content').innerHTML).length,2);
vm.runInContext("state.data={...fixture,requests:fixture.requests.map(r=>({...r,occurred_at:1,input_tokens:0}))}; render();",sandbox);
assert.doesNotMatch(element('#content').innerHTML, /NaN|Infinity/,'Handle identical times and zero inputs');
// Paging and tree navigation use filtered indices while preserving original request numbers.
vm.runInContext("state.data={...fixture,requests:Array.from({length:120},(_,i)=>({...fixture.requests[i%2],id:'r'+(i+1),number:i+1,thread_id:i%2?'agent-a':'main'}))}; render();",sandbox);
handlers.get('change')({target:{id:'context-agent',value:'agent-a'}});
assert.equal((element('#content').innerHTML.match(/data-request=/g)||[]).length,50);
assert.match(element('#turn-navigation').innerHTML,/60 条请求/);
handlers.get('click')({target:{closest:selector=>selector==='[data-jump-request]'?{dataset:{jumpRequest:'r102'}}:null}});
assert.equal(vm.runInContext('state.page',sandbox),1,'Use position 51 in filtered list, not original position 102');
assert.equal((element('#content').innerHTML.match(/data-request=/g)||[]).length,10);
assert.match(element('#turn-navigation').innerHTML,/#102/);
assert.equal(vm.runInContext("state.open.has('r102')",sandbox),true);
handlers.get('change')({target:{id:'context-agent',value:''}});
assert.equal(vm.runInContext('state.page',sandbox),0);
vm.runInContext("state.data={...fixture,requests:[]}; render();",sandbox);
assert.doesNotMatch(element('#content').innerHTML, /id="context-agent"|NaN|Infinity/);
vm.runInContext("state.contextAgent='agent-a';",sandbox);
handlers.get('window:hashchange')();
assert.equal(vm.runInContext('state.contextAgent',sandbox),'');
console.log('Context chart: overlaid series, common axes, colors, legend, selection, filters and lazy messages passed');
