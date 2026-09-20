const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const elements = new Map(), handlers = new Map(), saved = new Map(), sent = [];
const el = s => {
  if(!elements.has(s))elements.set(s,{value:'',innerHTML:'',style:{},classList:{toggle(){}},
    addEventListener:(e,h)=>handlers.set(s+':'+e,h),setAttribute(){},removeAttribute(){},
    querySelector:sub=>el(s+' '+sub),querySelectorAll:()=>[],reportValidity:()=>true});
  return elements.get(s);
};
const sandbox = {document:{body:{classList:{toggle(){}}},querySelector:el,querySelectorAll:()=>[],
    addEventListener:(e,h)=>handlers.set(e,h)},window:{addEventListener(){},scrollTo(){}},
  location:{hash:''},matchMedia:()=>({matches:false}),
  localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)},
  URLSearchParams,AbortController,setTimeout,clearTimeout,setInterval(){},console,
  fetch:()=>new Promise(()=>{})};
vm.createContext(sandbox);
for(const name of ['pricing.js','app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../static',name),'utf8'),sandbox);

sandbox.rows=[{thread_id:'side',turn_id:'turn',occurred_at:1789905200,model:'test',project:'/project',
  prompt:'OCR <script>unsafe</script>',preview:'OCR <script>unsafe</script>',total_tokens:null,
  context_samples:[{occurred_at:1789905200,tokens:68533}],tools:[{name:'exec',calls:2}],
  measured_requests:0,missing_samples:1,truncated:0,session_id:null}];
const panel=vm.runInContext('supplementalPanel(rows)',sandbox);
assert.match(panel,/<details class="panel supplemental-panel">/);
assert.match(panel,/OCR &lt;script&gt;unsafe&lt;\/script&gt;/);
assert.doesNotMatch(panel,/<script>|0 tokens|0.0 credits|对话时间线/);
assert.match(panel,/用量未知/);
assert.match(panel,/上下文观测/);
assert.match(panel,/工具返回正文未保留/);
assert.match(panel,/data-supplemental-link/);
assert.match(vm.runInContext('telemetryCard({config:{enabled:true},telemetry_events:0})',sandbox),/等待首次遥测/);
assert.match(vm.runInContext('telemetryCard({config:{enabled:false}})',sandbox),/data-telemetry="on"/);
const app=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
assert.ok(app.includes(".supplemental-panel[open]"),'Reading a recovered prompt pauses auto refresh');
console.log('Supplemental UI: compact, escaped, unknown usage and collection controls passed');
