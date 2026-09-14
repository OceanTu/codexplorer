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
sandbox.items=[{total_tokens:100,costs:{usd:unit(5)}},{total_tokens:1000,costs:{usd:unit(1)}}];
vm.runInContext("state.unit='usd'",sandbox);
assert.equal(vm.runInContext('sorted(items)[0].total_tokens',sandbox),100);
const card={input_per_million:10,cached_multiplier:.1,cache_write_multiplier:1.25,
  output_multiplier:5,reasoning_multiplier:5,fast_multiplier:2,long_context_threshold:272000,
  long_input_multiplier:2,long_output_multiplier:1.5};
sandbox.config={revision:'revision-1',verified_at:'2026-09-14',sources:[],observed_models:['gpt-6-astra'],
  defaults:[],models:[{model:'gpt-6-astra',mode:'standard',usd:{...card},credits:{...card,input_per_million:250}}]};
vm.runInContext("state.kind='pricing';state.data=config;render();",sandbox);
assert.match(el('#content').innerHTML,/缓存读取倍率/);
assert.match(el('#content').innerHTML,/推理倍率/);
assert.match(el('#content').innerHTML,/保存费率/);
assert.match(el('#content').innerHTML,/美元/);
// Edits survive subsequent renders and are sent with the original revision.
vm.runInContext("updatePricingInput({dataset:{priceModel:'0',priceUnit:'usd',priceField:'cached_multiplier'},value:'0.2'});render();",sandbox);
assert.equal(vm.runInContext('pricingEditor.draft.models[0].usd.cached_multiplier',sandbox),.2);
sandbox.fetch=async(url,options)=>{sent.push({url,options});return{ok:true,json:async()=>({...sandbox.config,revision:'revision-2'})};};
(async()=>{
  await vm.runInContext('savePricing()',sandbox);
  assert.equal(sent[0].url,'/api/pricing');
  assert.equal(sent[0].options.headers['X-Token-Lens'],'1');
  assert.equal(JSON.parse(sent[0].options.body).models[0].usd.cached_multiplier,.2);
  assert.equal(JSON.parse(sent[0].options.body).revision,'revision-1');
  assert.equal(vm.runInContext('pricingEditor.dirty',sandbox),false);
  assert.match(el('#content').innerHTML,/已保存/);
  console.log('Pricing UI: partial totals, unit sorting, draft preservation and save passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
