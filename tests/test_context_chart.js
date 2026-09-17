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
assert.match(html, /id="context-agent"/, 'Chart must offer an agent selector');
assert.equal(vm.runInContext('state.contextAgent',sandbox),'main');
assert.match(html, /2 条记录的输入 token 趋势，峰值 110/);
assert.equal((html.match(/<option value="agent-a"/g)||[]).length,1,'Group resumptions by thread, not execution turn');
assert.match(html, /worker &lt;one&gt;/);
const snapshot=JSON.stringify(sandbox.fixture);
vm.runInContext("state.open.add('agent-a:1'); state.page=2;",sandbox);
handlers.get('change')({target:{id:'context-agent',value:'agent-a'}});
html=element('#context-chart').innerHTML;
assert.match(html, /value="agent-a" selected/);
assert.match(html, /2 条记录的输入 token 趋势，峰值 500/);
assert.match(html, /开始 · 400/);
assert.match(html, /最后 · 500/);
assert.equal(JSON.stringify(sandbox.fixture),snapshot,'Selection must not alter totals or request rows');
assert.equal(vm.runInContext('state.page',sandbox),2);
assert.equal(vm.runInContext("state.open.has('agent-a:1')",sandbox),true);
assert.equal(requests.length,0,'Chart selection must not fetch messages or usage');
vm.runInContext('render()',sandbox);
assert.equal(vm.runInContext('state.contextAgent',sandbox),'agent-a','Keep choice across refresh/rerender');
// A selected thread removed by filtering falls back to main; agents-only never combines threads.
vm.runInContext("state.data={...fixture,requests:fixture.requests.filter(r=>r.thread_id!=='agent-a')}; render();",sandbox);
assert.equal(vm.runInContext('state.contextAgent',sandbox),'main');
vm.runInContext("state.data={...fixture,requests:fixture.requests.filter(r=>r.is_agent)}; render();",sandbox);
assert.equal(vm.runInContext('state.contextAgent',sandbox),'agent-a');
assert.match(element('#content').innerHTML,/2 条记录的输入 token 趋势，峰值 500/);
handlers.get('change')({target:{id:'context-agent',value:'agent-b'}});
assert.match(element('#context-chart').innerHTML,/1 条记录的输入 token 趋势，峰值 900/);
assert.match(element('#context-chart').innerHTML,/<circle/);
vm.runInContext("state.data={...fixture,requests:[]}; render();",sandbox);
assert.doesNotMatch(element('#content').innerHTML, /id="context-agent"|NaN|Infinity/);
vm.runInContext("state.contextAgent='agent-a';",sandbox);
handlers.get('window:hashchange')();
assert.equal(vm.runInContext('state.contextAgent',sandbox),'','Reset selection when navigating to a different Turn');
console.log('Context chart: per-agent isolation, resumptions, selection, refresh, filters and lazy messages passed');
