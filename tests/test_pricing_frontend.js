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
const unit = (total,unpriced=0) => ({total,unpriced_requests:unpriced,unpriced_tokens:unpriced*100,
  priced_requests:total===null?0:1,breakdown:{input:total||0,cached:0,cache_write:0,output:0,reasoning:0}});
sandbox.example={total_tokens:1000,costs:{credits:unit(20,1),usd:unit(2,1)}};
assert.match(vm.runInContext('costCell(example,"credits")',sandbox),/20/);
assert.match(vm.runInContext('costCell(example,"credits")',sandbox),/部分/);
sandbox.unknown={costs:{credits:unit(null,1),usd:unit(null,1)}};
assert.match(vm.runInContext('costCell(unknown,"usd")',sandbox),/未定价/);
assert.doesNotMatch(vm.runInContext('costCell(unknown,"usd")',sandbox),/\$0/);
assert.equal(vm.runInContext('amount(43616.3073,"credits")',sandbox),'43,616.3');
assert.equal(vm.runInContext('amount(20,"credits")',sandbox),'20.0');
assert.equal(vm.runInContext('amount(.00001,"credits")',sandbox),'0.0');
assert.equal(vm.runInContext('amount(2.1234,"usd")',sandbox),'$2.1234');
assert.equal(vm.runInContext('amount(.0125,"credits",true)',sandbox),'0.0125', 'Do not round editable rate previews');
assert.equal(vm.runInContext('creditsShare(25200)',sandbox),'50.0%');
assert.equal(vm.runInContext('creditsShare(100800)',sandbox),'200.0%');
assert.equal(vm.runInContext('creditsShare(0)',sandbox),'0.0%');
assert.equal(vm.runInContext('creditsShare(20)',sandbox),'<0.1%');
assert.equal(vm.runInContext('creditsShare(null)',sandbox),'未定价');
assert.match(vm.runInContext('costCell(example,"credits")',sandbox),/参考占比 &lt;0.1%/);
assert.doesNotMatch(vm.runInContext('costCell(unknown,"credits")',sandbox),/0.0%/);
sandbox.reference={costs:{credits:unit(25200,1),usd:unit(2)}};
assert.match(vm.runInContext('metrics(reference)',sandbox),/占 50,400：50.0%/);
assert.match(vm.runInContext('metrics(reference)',sandbox),/已知部分/);
assert.match(vm.runInContext('costSummary(reference)',sandbox),/7 天 ×20 套餐等价/);
vm.runInContext("state.data={credit_reference:{name:'自定义 <套餐>',credits:12600}}",sandbox);
assert.equal(vm.runInContext('creditsShare(25200)',sandbox),'200.0%');
assert.match(vm.runInContext('metrics(reference)',sandbox),/占 12,600：200.0%/);
assert.match(vm.runInContext('costCell(reference,"credits")',sandbox),/参考占比 200.0%/);
assert.match(vm.runInContext('costHint(reference.costs.credits,"credits")',sandbox),/占 12,600 参考额：200.0%/);
assert.match(vm.runInContext('costSummary(reference)',sandbox),/自定义 &lt;套餐&gt;/);
sandbox.items=[{total_tokens:100,costs:{usd:unit(5)}},{total_tokens:1000,costs:{usd:unit(1)}}];
vm.runInContext("state.unit='usd'",sandbox);
assert.equal(vm.runInContext('sorted(items)[0].total_tokens',sandbox),100);
const card={input_per_million:10,cached_multiplier:.1,cache_write_multiplier:1.25,
  output_multiplier:5,reasoning_multiplier:5,fast_multiplier:2,long_context_threshold:272000,
  long_input_multiplier:2,long_output_multiplier:1.5};
sandbox.config={revision:'revision-1',verified_at:'2026-09-14',sources:[],observed_models:['gpt-6-astra'],
  credit_reference:{name:'7 天 ×20 套餐等价',credits:50400},
  defaults:[],models:[{model:'gpt-6-astra',mode:'standard',usd:{...card},credits:{...card,input_per_million:250}}]};
vm.runInContext("state.kind='pricing';state.data=config;render();",sandbox);
assert.match(el('#content').innerHTML,/缓存读取倍率/);
assert.match(el('#content').innerHTML,/推理倍率/);
assert.match(el('#content').innerHTML,/保存配置/);
assert.match(el('#content').innerHTML,/套餐名称/);
assert.match(el('#content').innerHTML,/参考 Credits 额度/);
assert.match(el('#content').innerHTML,/美元/);
// Edits survive subsequent renders and are sent with the original revision.
vm.runInContext("updatePricingInput({dataset:{priceModel:'0',priceUnit:'usd',priceField:'cached_multiplier'},value:'0.2'});render();",sandbox);
assert.equal(vm.runInContext('pricingEditor.draft.models[0].usd.cached_multiplier',sandbox),.2);
handlers.get('input')({target:{dataset:{creditReference:'name'},value:'新套餐'}});
handlers.get('input')({target:{dataset:{creditReference:'credits'},value:'12600'}});
vm.runInContext('render()',sandbox);
assert.equal(vm.runInContext('pricingEditor.draft.credit_reference.credits',sandbox),12600);
assert.match(el('#content').innerHTML,/value="新套餐"/);
assert.equal(vm.runInContext('creditsShare(25200)',sandbox),'50.0%', 'Unsaved drafts do not change the active reference');
sandbox.fetch=async(url,options)=>{sent.push({url,options});return{ok:true,json:async()=>({...sandbox.config,...JSON.parse(options.body),revision:'revision-2'})};};
(async()=>{
  await vm.runInContext('savePricing()',sandbox);
  assert.equal(sent[0].url,'/api/pricing');
  assert.equal(sent[0].options.headers['X-Token-Lens'],'1');
  assert.equal(JSON.parse(sent[0].options.body).models[0].usd.cached_multiplier,.2);
  assert.equal(JSON.parse(sent[0].options.body).revision,'revision-1');
  assert.deepEqual(JSON.parse(sent[0].options.body).credit_reference,{name:'新套餐',credits:12600});
  assert.equal(vm.runInContext('creditsShare(25200)',sandbox),'200.0%');
  assert.equal(vm.runInContext('pricingEditor.dirty',sandbox),false);
  assert.match(el('#content').innerHTML,/已保存/);
  console.log('Pricing UI: partial totals, unit sorting, draft preservation and save passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
