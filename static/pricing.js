'use strict';
const unitLabels={tokens:'Token',credits:'Credits',usd:'美元'};
const CREDIT_PRESETS=Object.fromEntries([1,5,20].map(x=>[String(x),{name:`7 天 ×${x} 套餐等价`,credits:2520*x}]));
const DEFAULT_CREDIT_REFERENCE=CREDIT_PRESETS['20'];
function creditPreset(reference) {
  return Object.keys(CREDIT_PRESETS).find(key=>CREDIT_PRESETS[key].name===reference.name&&CREDIT_PRESETS[key].credits===reference.credits)||'custom';
}
function creditReference() {return state.data?.credit_reference||DEFAULT_CREDIT_REFERENCE;}
const costParts={input:'普通输入',cached:'缓存读取',cache_write:'缓存写入',output:'非推理输出',reasoning:'推理'};
const pricingEditor={draft:null,dirty:false,busy:false,message:'',error:false};
const clonePricing=value=>JSON.parse(JSON.stringify(value));
function amount(value,unit='credits',asRate=false) {
  if(value===null||value===undefined)return '未定价';
  if(unit==='credits'&&!asRate)return Number(value).toLocaleString('en-US',{minimumFractionDigits:1,maximumFractionDigits:1});
  const number=value>0&&value<.0001?'<0.0001':Number(value).toLocaleString('en-US',{maximumFractionDigits:4});
  return (unit==='usd'?'$':'')+number;
}
function creditsShare(value) {
  if(value===null||value===undefined)return '未定价';
  const share=value/creditReference().credits;
  return share>0&&share<.001?'<0.1%':pct(share);
}
function measured(row) {return state.unit==='tokens'?row.total_tokens:row.costs?.[state.unit]?.total;}
function measuredLabel(row) {return state.unit==='tokens'?compact(row.total_tokens):amount(measured(row),state.unit);}
function costHint(cost,unit) {
  if(!cost||cost.total===null)return cost?.reason==='invalid_subsets'?'Token 子集异常，未纳入费用估算':'缺少费率，未纳入费用估算';
  return `${unit==='credits'?amount(cost.total,unit):Number(cost.total).toLocaleString('en-US',{maximumFractionDigits:10})} ${unit} · `+
    Object.entries(costParts).map(([key,name])=>`${name} ${amount(cost.breakdown[key],unit)}`).join(' · ')+
    (unit==='credits'?` · 占 ${full(creditReference().credits)} 参考额：${creditsShare(cost.total)}`:'')+
    (cost.unpriced_requests?` · 另有 ${full(cost.unpriced_requests)} 条未定价`:'')+
    (cost.mode?` · 按 ${cost.mode==='fast'?'Fast':'Standard'} 估算${cost.long_context?' · 长上下文加价':''}`:'');
}
function costCell(row,unit) {
  const cost=row.costs?.[unit];
  return `<td class="cost-cell" title="${esc(costHint(cost,unit))}">${esc(amount(cost?.total,unit))}${unit==='credits'&&cost?.total!=null?`<small class="credit-share">参考占比 ${esc(creditsShare(cost.total))}</small>`:''}${cost?.unpriced_requests&&cost.total!==null?'<small>部分</small>':''}</td>`;
}
function unitToolbar() {
  if(state.kind==='turn')return '<div class="usage-toolbar"><span>请求按时间排列 · 同时显示 Token 与费用</span><a href="#pricing">套餐与费率设置 ↗</a></div>';
  return `<div class="usage-toolbar"><div class="unit-tabs" role="group" aria-label="图表与排序单位">${Object.entries(unitLabels).map(([key,name])=>`<button data-unit="${key}" aria-pressed="${state.unit===key}" class="${state.unit===key?'selected':''}">${name}</button>`).join('')}</div><span>切换图表与消耗排序</span><a href="#pricing">套餐与费率设置 ↗</a></div>`;
}
function costSummary(s) {
  const costs=s.costs;if(!costs)return '';
  const missing=Math.max(costs.credits.unpriced_requests,costs.usd.unpriced_requests);
  return `<div class="cost-note">按当前费率与所选模式重算历史 · Credits 与美元是两套独立估算，不相加，也不代表实际账单。占比基准为 ${full(creditReference().credits)} credits（${esc(creditReference().name)}，用户设定），按当前筛选结果计算。${missing?`<strong>存在未定价或计数异常记录：Credits ${full(costs.credits.unpriced_requests)} 条 / 美元 ${full(costs.usd.unpriced_requests)} 条，仅汇总可计算部分。</strong>`:''}<a href="#pricing">查看口径与费率</a></div><details class="cost-breakdown"><summary>查看费用构成</summary><div class="table-wrap"><table><thead><tr><th>互不重复的 Token 分类</th><th>Credits 估算</th><th>美元估算</th></tr></thead><tbody>${Object.entries(costParts).map(([key,name])=>`<tr><td>${name}</td>${['credits','usd'].map(unit=>`<td>${esc(amount(costs[unit].total===null?null:costs[unit].breakdown[key],unit))}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`;
}
function creditPresetView(reference) {
  const selected=creditPreset(reference);
  return `<label class="credit-preset-field">套餐预设<select data-credit-preset>${Object.entries(CREDIT_PRESETS).map(([key,preset])=>`<option value="${key}" ${selected===key?'selected':''}>×${key} · ${full(preset.credits)} credits / 7 天</option>`).join('')}<option value="custom" ${selected==='custom'?'selected':''}>自定义</option></select></label>`;
}
function pricingView(data) {
  if(!pricingEditor.draft)pricingEditor.draft=clonePricing(data);
  const draft=pricingEditor.draft;
  if(!draft.credit_reference)draft.credit_reference=clonePricing(DEFAULT_CREDIT_REFERENCE);
  const fields=[['input_per_million','普通输入基价 / 百万 token'],['cached_multiplier','缓存读取倍率'],
    ['cache_write_multiplier','缓存写入倍率'],['output_multiplier','非推理输出倍率'],['reasoning_multiplier','推理倍率']];
  const extra=[['fast_multiplier','Fast 整体倍率'],['long_context_threshold','长上下文阈值 / 输入 token'],
    ['long_input_multiplier','超阈值：全部输入倍率'],['long_output_multiplier','超阈值：全部输出倍率']];
  const input=(i,unit,key,label)=>{
    const value=draft.models[i][unit][key];
    return `<input type="number" min="0" max="1000000" step="${key==='long_context_threshold'?'1':'any'}" ${key==='input_per_million'?'placeholder="未定价"':'required'} value="${value===null?'':esc(value)}" data-price-model="${i}" data-price-unit="${unit}" data-price-field="${key}" aria-label="${esc(draft.models[i].model+' '+unitLabels[unit]+' '+label)}">`;
  };
  const table=(i,items)=>`<div class="table-wrap"><table class="rate-table"><thead><tr><th>配置项</th><th>Credits</th><th>美元（USD / API）</th></tr></thead><tbody>${items.map(([key,label])=>`<tr><th>${label}</th>${['credits','usd'].map(unit=>`<td>${input(i,unit,key,label)}${key.endsWith('_multiplier')&&!key.startsWith('long_')&&key!=='fast_multiplier'?`<small class="rate-preview" data-preview="${i}-${unit}-${key}">${esc(ratePreview(draft.models[i][unit],key,unit))}</small>`:''}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  return `<div class="pricing-intro"><p>分别设置各模型的普通输入基价、缓存与输出倍率。<b>倍率都相对于普通输入基价</b>；推理从输出中拆出，不会重复相加。留空基价表示未定价，填写 0 表示免费。</p><p>美元按同样用量调用 API 的价格估算；Credits 按 Codex 费率估算。历史日志未提供可靠的 Fast 标记，默认按 Standard；切换模式会应用到该模型的全部历史记录。套餐额度与费率保存后立即应用，无需重新扫描。</p></div><form id="pricing-form"><div class="pricing-actions"><button type="button" class="button save-pricing" data-pricing-action="save">保存配置</button><button type="button" class="button" data-pricing-action="reload">重新加载 / 放弃修改</button><span id="pricing-message" role="status" class="${pricingEditor.error?'pricing-error':''}">${esc(pricingEditor.message||'默认费率核对于 '+data.verified_at)}</span></div><section class="panel credit-plan"><h2>套餐参考额度</h2>${creditPresetView(draft.credit_reference)}<div class="credit-plan-fields"><label>套餐名称<input type="text" data-credit-reference="name" value="${esc(draft.credit_reference.name)}" maxlength="80" required placeholder="例如：7 天 ×20 套餐等价"></label><label>参考 Credits 额度<input type="number" data-credit-reference="credits" value="${esc(draft.credit_reference.credits)}" min="0" max="1000000000000" step="any" required></label></div><p>预设按 ×20 = 50,400 credits / 7 天等比例换算。选择后自动填入名称和额度，也可自行修改。点击“保存配置”后应用。占比 = 当前筛选的 Credits ÷ 此额度；不按筛选天数折算。该配置只影响占比，不改变 Token、Credits 或美元估算。</p></section>${draft.models.map((m,i)=>`<details class="panel rate-model" ${i===0?'open':''}><summary><span>${esc(m.model)}</span><small>${esc(amount(m.credits.input_per_million,'credits',true))} credits / ${esc(amount(m.usd.input_per_million,'usd'))} · 百万普通输入</small>${data.observed_models.includes(m.model)?'<span class="tag">本机有记录</span>':''}</summary><div class="rate-mode"><label>估算模式 <select data-price-mode="${i}" aria-label="${esc(m.model)} 估算模式"><option value="standard" ${m.mode==='standard'?'selected':''}>Standard</option><option value="fast" ${m.mode==='fast'?'selected':''}>Fast（使用下面的整体倍率）</option></select></label><button type="button" class="text-button" data-pricing-action="restore" data-pricing-index="${i}">恢复此模型默认值</button></div>${table(i,fields)}<details class="rate-advanced"><summary>Fast 与长上下文倍率</summary>${table(i,extra)}<p>阈值为 0 时不加价；单条请求输入严格超过阈值时，对整条请求应用倍率，Fast 再叠乘。旧版累计差分可能覆盖多次请求，长上下文估算存在误差。</p></details></details>`).join('')}<div class="pricing-add"><input id="new-pricing-model" aria-label="新增模型 ID" placeholder="新增模型 ID，例如自定义模型或精确版本号" maxlength="160"><button type="button" class="button" data-pricing-action="add">添加模型</button></div></form><section class="panel pricing-notes"><h2>默认值与估算边界</h2><p>预填 Astra、Sol、Terra、Luna 的官方公开价格。Sol 当前为促销价，官方注明至少持续至 2026-11-21。模型 ID 精确匹配；自动审核及其他没有公开价格的模型不会套用主模型价格。</p><p>Codex 公开 credits 表未单列缓存写入和长上下文加价：这里默认缓存写入按普通输入 1× 估算，长上下文加价关闭。API 默认缓存写入 1.25×、读取 0.1×；超过 272,000 输入 token 时，输入及缓存 2×、输出及推理 1.5×。这些假设均可修改。</p><p>只计算日志中记录的模型 token；不包含额外工具服务费、税费、套餐订阅费或其他设备的消耗。当前配置统一用于整个历史，不重建当时的价格；不会自动根据价格网页更改你的配置。配置保存在本机数据库，升级时保留。</p><div class="pricing-sources">${data.sources.map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noreferrer">${esc(s.label)} ↗</a>`).join('')}</div></section>`;
}
function ratePreview(card,key,unit) {
  return card.input_per_million===null?'未定价':amount(card.input_per_million*card[key],unit,true)+' / 百万 token';
}
function pricingMessage(text,error=false) {
  pricingEditor.message=text;pricingEditor.error=error;
  const node=$('#pricing-message');if(node){node.textContent=text;node.className=error?'pricing-error':'';}
}
function updatePricingInput(target) {
  if(!pricingEditor.draft||pricingEditor.busy)return;
  const index=target.dataset.priceModel, mode=target.dataset.priceMode, reference=target.dataset.creditReference;
  if(target.dataset.creditPreset!==undefined){
    const preset=CREDIT_PRESETS[target.value];
    if(preset)pricingEditor.draft.credit_reference=clonePricing(preset);
    else if(target.value==='custom'){
      if(creditPreset(pricingEditor.draft.credit_reference)!=='custom')pricingEditor.draft.credit_reference.name='自定义套餐';
    }else return;
    for(const key of ['name','credits'])$(`[data-credit-reference="${key}"]`).value=pricingEditor.draft.credit_reference[key];
  }else if(reference==='name'||reference==='credits'){
    pricingEditor.draft.credit_reference[reference]=reference==='name'?target.value:target.value===''?NaN:Number(target.value);
  }else if(index!==undefined){
    const card=pricingEditor.draft.models[Number(index)][target.dataset.priceUnit];
    const key=target.dataset.priceField;
    card[key]=target.value===''?(key==='input_per_million'?null:NaN):Number(target.value);
    for(const field of ['cached_multiplier','cache_write_multiplier','output_multiplier','reasoning_multiplier']){
      const preview=$(`[data-preview="${index}-${target.dataset.priceUnit}-${field}"]`);
      if(preview)preview.textContent=ratePreview(card,field,target.dataset.priceUnit);
    }
  }else if(mode!==undefined)pricingEditor.draft.models[Number(mode)].mode=target.value;
  else return;
  $('[data-credit-preset]').value=creditPreset(pricingEditor.draft.credit_reference);
  pricingEditor.dirty=true;pricingMessage('有未保存的修改');
}
async function savePricing() {
  if(pricingEditor.busy||!pricingEditor.draft||!$('#pricing-form').reportValidity())return;
  pricingEditor.busy=true;
  const controls=$('#pricing-form').querySelectorAll('input,select,button');controls.forEach(el=>el.disabled=true);
  pricingMessage('正在保存…');
  try{
    const response=await fetch('/api/pricing',{method:'POST',headers:{'Content-Type':'application/json','X-Token-Lens':'1'},body:JSON.stringify(pricingEditor.draft)});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'配置保存失败');
    pricingEditor.draft=null;pricingEditor.dirty=false;pricingMessage('已保存，套餐占比与费率已应用');
    if(state.kind==='pricing'){state.data=data;render();}else await load();
  }catch(e){pricingMessage(e.message,true);}
  finally{pricingEditor.busy=false;controls.forEach(el=>el.disabled=false);}
}
function pricingAction(button) {
  if(pricingEditor.busy)return;
  const action=button.dataset.pricingAction;
  if(action==='save'){savePricing();return;}
  if(action==='reload'){pricingEditor.draft=null;pricingEditor.dirty=false;pricingEditor.message='';load();return;}
  const draft=pricingEditor.draft;if(!draft)return;
  if(action==='restore'){
    const index=Number(button.dataset.pricingIndex), name=draft.models[index].model;
    const original=state.data.defaults.find(m=>m.model===name);
    draft.models[index]=original?clonePricing(original):newPricingModel(name);
  }
  if(action==='add'){
    const name=$('#new-pricing-model').value.trim();
    if(!/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$/.test(name)){pricingMessage('请填写有效模型 ID',true);return;}
    if(draft.models.some(m=>m.model===name)){pricingMessage('该模型已在列表中',true);return;}
    draft.models.push(newPricingModel(name));
  }
  pricingEditor.dirty=true;pricingMessage('有未保存的修改');
  const opened=[...document.querySelectorAll('.rate-model')].map(el=>el.open);
  render();
  document.querySelectorAll('.rate-model').forEach((el,i)=>el.open=opened[i]??true);
}
function newPricingModel(name) {
  const card={input_per_million:null,cached_multiplier:.1,cache_write_multiplier:1,output_multiplier:1,
    reasoning_multiplier:1,fast_multiplier:1,long_context_threshold:0,long_input_multiplier:1,long_output_multiplier:1};
  return {model:name,mode:'standard',credits:{...card},usd:{...card}};
}
