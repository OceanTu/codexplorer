'use strict';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const full = n => Number(n || 0).toLocaleString('en-US');
const compact = n => n >= 1e9 ? (n / 1e9).toFixed(2) + ' B' : n >= 1e6 ? (n / 1e6).toFixed(2) + ' M' : n >= 1e3 ? (n / 1e3).toFixed(1) + ' K' : full(n);
const pct = n => (100 * (n || 0)).toFixed(1) + '%';
const date = (n, detailed = false) => n ? new Date(n * 1000).toLocaleString('zh-CN', detailed ? {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false} : {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '—';
const shortPath = p => p ? p.split('/').filter(Boolean).slice(-1)[0] : '未知项目';
const ratio = (a,b) => b ? a/b : 0;
const routeLink = (sid, tid) => '#session/' + encodeURIComponent(sid) + (tid ? '/turn/' + encodeURIComponent(tid) : '');
const state = {range:'7', from:null, to:null, page:0, sort:'total', data:null, kind:'overview', session:null, turn:null, execution:'', open:new Set(), request:0};
let controller;
let refreshing=false;
const messageCache=new Map();

function query() {
  const q = new URLSearchParams();
  const now = new Date();
  let from = state.from, to = state.to;
  if (state.range !== 'custom') {
    from = null; to = null;
    if (state.range !== 'all') {
      const start = new Date(now.getFullYear(),now.getMonth(),now.getDate());
      if (state.range !== 'today') start.setDate(start.getDate()-Number(state.range)+1);
      from = start.getTime()/1000;
    }
  }
  if (from !== null) q.set('from',from);
  if (to !== null) q.set('to',to);
  ['project','model','scope'].forEach(id => { if ($('#'+id).value) q.set(id,$('#'+id).value); });
  if ($('#search').value.trim()) q.set('q',$('#search').value.trim());
  if (state.kind==='turn' && state.execution) q.set('execution',state.execution);
  return q.toString();
}

async function get(path, signal) {
  const r = await fetch(path, {signal});
  if (!r.ok) {
    const error=await r.json().catch(()=>({}));
    throw new Error(error.error||'本地服务暂时不可用（' + r.status + '）');
  }
  return r.json();
}

async function waitForSync(requestedAt, signal) {
  const deadline=Date.now()+30000;
  while(Date.now()<deadline) {
    const status=await get('/api/status',signal);
    if(status.scan?.started_at>=requestedAt)return status.scan;
    await new Promise(resolve=>setTimeout(resolve,350));
  }
  throw new Error('采集尚未完成，后台会继续处理，请稍后再试');
}

function metric(label, value, note, primary=false, symbol='◌', raw) {
  const pieces = String(value).split(' ');
  return `<div class="metric ${primary?'primary':''}"><div class="metric-label">${label}<span class="symbol" aria-hidden="true">${symbol}</span></div><div class="metric-value" title="${esc(raw === undefined ? value : full(raw))}">${esc(pieces[0])}${pieces[1]?`<small>${esc(pieces[1])}</small>`:''}</div><div class="metric-caption">${note}</div></div>`;
}
function metrics(s) {
  return `<section class="metrics" aria-label="用量摘要">${metric('Token 总量', compact(s.total_tokens), `${full(s.sessions)} 个 session · ${full(s.turns)} 个 turn`,true,'◇',s.total_tokens)}${metric('未缓存输入', compact(s.uncached_input_tokens), `占输入 ${pct(ratio(s.uncached_input_tokens,s.input_tokens))}` ,false,'↗',s.uncached_input_tokens)}${metric('缓存命中率', pct(s.cache_hit_rate), `${compact(s.cached_input_tokens)} 缓存输入 tokens`,false,'◷')}${metric('输出 Token',compact(s.output_tokens),`含 ${compact(s.reasoning_output_tokens)} 推理 tokens`,false,'↙',s.output_tokens)}</section>`;
}
const legend = '<div class="legend"><span>未缓存输入</span><span class="cached">缓存输入</span><span class="output">输出</span></div>';
function dailyChart(daily) {
  const max = Math.max(1,...daily.map(x=>x.total_tokens));
  return `<section class="panel"><div class="panel-heading"><h2>每日消耗</h2>${legend}</div><div class="daily-wrap"><div class="axis"><span>${compact(max)}</span><span>${compact(max/2)}</span><span>0</span></div><div class="daily" role="group" aria-label="每日 token 消耗，点击可筛选日期">${daily.map((d,i)=>{
    const label = d.day.slice(5).replace('-','/');
    const description = `${d.day} · ${full(d.total_tokens)} tokens · 缓存命中 ${pct(d.cache_hit_rate)}`;
    return `<button class="day" data-day="${d.day}" aria-label="${esc(description)}" title="${esc(description)}"><span class="bar" style="height:${(d.total_tokens/max)*84}%"><i style="height:${ratio(d.uncached_input_tokens,d.total_tokens)*100}%"></i><i class="cache" style="height:${ratio(d.cached_input_tokens,d.total_tokens)*100}%"></i><i class="out" style="height:${ratio(d.output_tokens,d.total_tokens)*100}%"></i></span><span class="day-label">${daily.length<=14 || i%Math.ceil(daily.length/10)===0 ? label : ''}</span></button>`;
  }).join('') || '<div class="empty">当前范围暂无记录</div>'}</div></div></section>`;
}
function modelChart(models,total) {
  return `<section class="panel"><div class="panel-heading"><h2>模型分布</h2><small>按 token 总量</small></div><div class="models">${models.map(m=>`<button class="model-item" data-model="${esc(m.model)}" title="筛选 ${esc(m.model)}"><div class="model-top"><span>${esc(m.model)}</span><b>${compact(m.total_tokens)}</b></div><div class="track"><i style="width:${ratio(m.total_tokens,total)*100}%"></i></div><div class="model-sub">${pct(ratio(m.total_tokens,total))} · ${full(m.requests)} 次请求 / 记录</div></button>`).join('') || '<div class="empty">暂无数据</div>'}</div></section>`;
}
function empty(message='试试扩大时间范围或重置筛选条件。') {return `<div class="panel empty"><h3>当前范围没有用量记录</h3><p>${esc(message)}</p><button class="button" data-action="reset">查看全部记录</button></div>`;}
function tablePanel(title, count, head, rows, foot, sortable=true) {
  const pages=Math.max(1,Math.ceil(count/50));
  return `<section class="panel table-panel"><div class="panel-heading"><div class="table-info"><h2>${title}</h2><span class="count-badge">${full(count)}</span></div>${sortable?`<select class="sort" aria-label="排序方式" id="table-sort"><option value="total" ${state.sort==='total'?'selected':''}>消耗从高到低 ↓</option><option value="time" ${state.sort==='time'?'selected':''}>时间从新到旧 ↓</option><option value="chronological" ${state.sort==='chronological'?'selected':''}>时间从旧到新 ↑</option></select>`:'<span class="tag">按时间顺序</span>'}</div><div class="table-wrap"><table><thead><tr>${head.map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div><div class="table-footer"><span>${foot}</span><span><button data-page="prev" ${state.page===0?'disabled':''}>←</button> ${state.page+1} / ${pages} <button data-page="next" ${state.page+1>=pages?'disabled':''}>→</button></span></div></section>`;
}
function sliced(items) {state.page=Math.min(state.page,Math.max(0,Math.ceil(items.length/50)-1));return items.slice(state.page*50,(state.page+1)*50);}
function sorted(items) {return [...items].sort((a,b)=>state.sort==='total'?b.total_tokens-a.total_tokens:state.sort==='time'?b.first_at-a.first_at:a.first_at-b.first_at);}
function usageCell(n,max) {return `<td class="token-cell"><span class="cell-number" title="${full(n)}">${compact(n)}</span><div class="track"><i style="width:${100*ratio(n,max)}%"></i></div></td>`;}
function coverage(s) {return `${full(s.requests)} 次请求 / 记录 · ${full(s.exact_requests)} 条逐次记录 · ${full(s.requests-s.exact_requests)} 条历史还原${s.quality_warnings?` · ${full(s.quality_warnings)} 条需注意的记录`:''}`;}

function overview(d) {
  const s=d.summary, items=sorted(d.sessions), max=Math.max(1,...items.map(x=>x.total_tokens));
  const rows=sliced(items).map((r,i)=>`<tr class="clickable" data-route="${routeLink(r.id)}"><td class="rank">${String(state.page*50+i+1).padStart(2,'0')}</td><td><div class="session-title"><a href="${routeLink(r.id)}">${esc(r.title)}</a></div><div class="row-subtitle" title="${esc(r.project)}">${esc(shortPath(r.project))} · ${r.threads} 个代理线程${r.archived?' · 已归档':''}</div></td><td><span class="tag">${esc(r.models[0])}</span>${r.models.length>1?` <span class="tag">+${r.models.length-1}</span>`:''}</td><td>${full(r.turns)}</td>${usageCell(r.total_tokens,max)}<td>${pct(r.cache_hit_rate)}</td><td class="row-subtitle">${date(r.last_at)}</td><td class="arrow">›</td></tr>`);
  return metrics(s)+`<div class="charts">${dailyChart(d.daily)}${modelChart(d.models,s.total_tokens)}</div>`+(s.requests?`<div class="insight"><span aria-hidden="true">↳</span><div>子代理与自动审核消耗 <strong>${compact(s.agent_tokens)}</strong> tokens，占总量 <strong>${pct(ratio(s.agent_tokens,s.total_tokens))}</strong>。已计入所属会话。</div></div>`:'')+(items.length?tablePanel('会话明细',items.length,['#','Session','模型','Turns','Token 总量','缓存命中','最近消耗',''],rows,coverage(s)):empty());
}
function sessionView(d) {
  const items=sorted(d.turns), max=Math.max(1,...items.map(x=>x.total_tokens));
  const top=items.length?[...items].sort((a,b)=>b.total_tokens-a.total_tokens)[0]:null;
  const rows=sliced(items).map(r=>`<tr class="clickable" data-route="${routeLink(d.id,r.id)}"><td><span class="tag">Turn ${r.number}</span><div class="row-subtitle">${date(r.first_at)}</div></td><td class="turn-preview"><a href="${routeLink(d.id,r.id)}">${esc(r.preview||'此 turn 没有可用的用户摘要')}</a><div class="row-subtitle">${esc(r.models.join(' · '))} · 记录跨度 ${duration(r.duration_seconds)} · ${r.execution_turns} 个执行 turn</div></td><td>${full(r.requests)}</td>${usageCell(r.total_tokens,max)}<td>${pct(r.cache_hit_rate)}</td><td title="${full(r.agent_tokens)}">${compact(r.agent_tokens)}</td><td>${statusBadge(r.status)}</td><td class="arrow">›</td></tr>`);
  return metrics(d.summary)+(top?`<div class="insight"><span aria-hidden="true">↗</span><div>消耗最高：<a href="${routeLink(d.id,top.id)}"><strong>Turn ${top.number}</strong></a>，${compact(top.total_tokens)} tokens，占当前会话筛选结果的 <strong>${pct(ratio(top.total_tokens,d.summary.total_tokens))}</strong>。</div></div>`:'')+(items.length?tablePanel('Turn 明细',items.length,['Turn','用户请求 / 摘要','请求 / 记录','Token 总量','缓存命中','子代理 Token','状态',''],rows,coverage(d.summary)):empty());
}
function duration(s) {return s<60?Math.round(s)+' 秒':s<3600?(s/60).toFixed(1)+' 分钟':(s/3600).toFixed(1)+' 小时';}
function statusBadge(s) {const labels={running:'未见结束事件',completed:'已完成',interrupted:'已中断',observed:'历史记录'};return `<span class="tag ${s==='interrupted'?'warn':''}">${labels[s]||esc(s)}</span>`;}
function requestChart(rows) {
  const main=rows.filter(r=>!r.is_agent), items=main.length?main:rows;
  if (!items.length) return '';
  const max=Math.max(1,...items.map(x=>x.input_tokens)), width=1000, height=130;
  const points=items.map((r,i)=>`${20+(items.length>1?i/(items.length-1):.5)*960},${140-r.input_tokens/max*height}`).join(' ');
  return `<section class="panel section-gap"><div class="panel-heading"><h2>${main.length?'主代理':'代理'}输入上下文走势</h2><small>峰值 ${compact(max)} tokens · 每条记录的输入量（含缓存）</small></div><div class="request-chart"><svg viewBox="0 0 ${width} 155" preserveAspectRatio="none" role="img" aria-label="${items.length} 条记录的输入 token 趋势，峰值 ${full(max)}"><path d="M20 10H980 M20 75H980 M20 140H980" stroke="#edf1eb" fill="none"/><polyline points="${points}" fill="none" stroke="#569174" stroke-width="2" vector-effect="non-scaling-stroke"/>${items.length===1?`<circle cx="500" cy="${140-items[0].input_tokens/max*height}" r="4" fill="#569174"/>`:''}</svg><div class="chart-captions"><span>开始 · ${compact(items[0].input_tokens)}</span><span>${items.length} 条记录</span><span>最后 · ${compact(items.at(-1).input_tokens)}</span></div></div></section>`;
}
function messageCard(m, requestId) {
  const isCall=['function_call','custom_tool_call'].includes(m.kind);
  const role=isCall?'工具调用':({user:'用户',assistant:'助手',tool:'工具返回'}[m.role]||m.role);
  return `<article class="message-card" data-message-id="${esc(m.id)}"><header><strong>${esc(role)}${m.name?' · '+esc(m.name):''}</strong><time>${date(Date.parse(m.timestamp)/1000,true)}</time></header><pre class="message-text">${esc(m.text)}</pre>${m.call_id?`<div class="message-call-id">${esc(m.call_id)}</div>`:''}${m.next_offset!==null?`<div class="message-more"><span>已显示 ${full(m.next_offset)} / ${full(m.total_chars)} 字符</span><button class="button" data-more-message="${esc(m.id)}" data-message-request="${esc(requestId)}">继续加载</button></div>`:''}${m.error?`<p class="message-error">${esc(m.error)}</p>`:''}</article>`;
}
function messagesHTML(requestId) {
  const entry=messageCache.get(requestId);
  if(!entry||entry.loading)return '<p class="messages-placeholder" role="status">正在读取这条请求的对话…</p>';
  if(entry.error)return `<p class="message-error" role="alert">${esc(entry.error)}</p><button class="button" data-retry-messages="${esc(requestId)}">重新读取</button>`;
  const d=entry.data;
  const column=(label,items,empty)=>`<section class="message-column"><h3>${label} <span class="count-badge">${items.length}</span></h3>${items.map(m=>messageCard(m,requestId)).join('')||`<p class="messages-placeholder">${empty}</p>`}</section>`;
  return `<p class="messages-note">${esc(d.note)}</p>${d.warnings.map(w=>`<p class="message-warning">${esc(w)}</p>`).join('')}${d.context.length?`<details class="message-context"><summary>本轮用户消息（用于对照，未重复计为本次新增输入）</summary>${d.context.map(m=>messageCard(m,requestId)).join('')}</details>`:''}<div class="message-grid">${column('↑ 上行 · 新增输入',d.input,'此区间没有可还原的新增用户或工具消息。')}${column('↓ 下行 · 助手输出',d.output,d.is_compaction?'压缩响应没有保存为可见对话消息。':'日志未保存可见回复，可能只有内部推理或未落盘内容。')}</div>${d.tool_results.length?`<details class="message-tool-results"><summary>工具执行结果 · ${d.tool_results.length} 条（供后续请求使用，不属于模型输出 token）</summary>${d.tool_results.map(m=>messageCard(m,requestId)).join('')}</details>`:''}`;
}
function updateMessagesPanel(id) {
  const panel=[...document.querySelectorAll('.request-conversation')].find(el=>el.dataset.requestId===id);
  if(panel) {
    const opened=new Set([...panel.querySelectorAll('details[open]')].map(el=>el.className));
    const scrolls=new Map([...panel.querySelectorAll('.message-card')].map(el=>[el.dataset.messageId,el.querySelector('.message-text').scrollTop]));
    panel.innerHTML=messagesHTML(id);
    panel.querySelectorAll('details').forEach(el=>{if(opened.has(el.className))el.open=true;});
    panel.querySelectorAll('.message-card').forEach(el=>{if(scrolls.has(el.dataset.messageId))el.querySelector('.message-text').scrollTop=scrolls.get(el.dataset.messageId);});
  }
}
async function ensureMessages(id,force=false) {
  if(!force&&messageCache.has(id))return;
  const entry={loading:true};messageCache.set(id,entry);updateMessagesPanel(id);
  try{entry.data=await get('/api/requests/'+encodeURIComponent(id)+'/messages');}
  catch(e){entry.error=e.message;}
  entry.loading=false;updateMessagesPanel(id);
}
async function moreMessage(button) {
  const requestId=button.dataset.messageRequest,id=button.dataset.moreMessage;
  const d=messageCache.get(requestId)?.data;if(!d)return;
  const m=['context','input','output','tool_results'].flatMap(k=>d[k]).find(m=>m.id===id);
  if(!m||m.next_offset===null)return;
  button.disabled=true;delete m.error;
  try {
    const chunk=await get('/api/requests/'+encodeURIComponent(requestId)+'/messages?'+new URLSearchParams({message:id,offset:m.next_offset}));
    m.text+=chunk.text;m.next_offset=chunk.next_offset;m.total_chars=chunk.total_chars;
  }catch(e){m.error=e.message;}
  updateMessagesPanel(requestId);
}
function requestDetailsRow(r) {
  const open=state.open.has(r.id),panelId='messages-'+r.number;
  return `<tr><td><button class="request-toggle" data-request="${esc(r.id)}" aria-expanded="${open}" aria-controls="${panelId}">${open?'−':'＋'} #${r.number}<small>对话</small></button></td><td>${date(r.occurred_at,true)}</td><td><span class="tag ${r.is_agent?'agent':''}" title="${esc(r.thread_title)}">${r.is_agent?(r.source==='guardian_review'?'自动审核':'子代理'):'主代理'}</span><div class="row-subtitle">${esc(r.model)}${r.effort?' · '+esc(r.effort):''}</div></td><td title="${full(r.input_tokens)}">${compact(r.input_tokens)}</td><td title="${full(r.cached_input_tokens)}">${compact(r.cached_input_tokens)}</td><td title="${full(r.output_tokens)}">${compact(r.output_tokens)}</td><td title="${full(r.reasoning_output_tokens)}">${compact(r.reasoning_output_tokens)}</td><td class="cell-number" title="${full(r.total_tokens)}">${compact(r.total_tokens)}</td><td><span class="tag ${r.quality?'warn':''}" title="${esc(r.quality)}">${r.kind==='exact'?'逐次记录':'历史还原'}${r.quality?' *':''}</span></td></tr>${open?`<tr class="conversation-row" id="${panelId}"><td colspan="9"><div class="request-conversation" data-request-id="${esc(r.id)}">${messagesHTML(r.id)}</div><details class="message-provenance"><summary>原始记录信息</summary><dl class="request-details"><dt>Response ID</dt><dd>${esc(r.response_id||'旧版记录无 response_id')}</dd><dt>Thread / Turn</dt><dd>${esc(r.thread_id)} / ${esc(r.turn_id)}</dd><dt>原始文件 · 字节偏移</dt><dd>${esc(r.path)} · ${r.byte_offset}</dd><dt>统计来源</dt><dd>${esc(r.kind)}${r.quality?' · '+esc(r.quality):''}</dd><dt>缓存写入（输入子集）</dt><dd>${full(r.cache_write_input_tokens)}</dd></dl></details></td></tr>`:''}`;
}

function turnView(d) {
  const rows=sliced(d.requests).map(requestDetailsRow);
  const executionPicker = `<div class="execution-picker"><label for="execution">执行 Turn</label><select id="execution" aria-label="筛选执行 Turn"><option value="">全部 ${d.executions.length} 个（含续跑 / 子代理）</option>${d.executions.map((r,i)=>`<option value="${esc(r.id)}" ${state.execution===r.id?'selected':''}>${i+1}. ${r.is_agent?(r.source==='guardian_review'?'自动审核':'子代理'):'主代理'} · ${r.id.slice(-8)} · ${compact(r.total_tokens)} tokens</option>`).join('')}</select><span>可单独查看每个执行 turn 的消耗</span></div>`;
  return executionPicker+metrics(d.summary)+requestChart(d.requests)+(rows.length?tablePanel('请求明细',d.requests.length,['记录','时间','来源 / 模型','输入','缓存输入','输出','推理','总量','依据'],rows,'点击“对话”查看上下行消息；展开阅读时暂停自动刷新。缓存 / 推理已包含在输入 / 输出中。',false):empty());
}
function collectionView(d) {
  const scan=d.scan||{}, hook=d.latest_hook, files=d.files||{};
  const card=(title,rows,description)=>`<section class="panel status-card"><h3>${title}</h3><p>${description}</p><div class="status-list">${rows.map(([k,v])=>`<div class="status-row"><span>${k}</span><b>${v}</b></div>`).join('')}</div></section>`;
  return `<div class="status-grid">${card('历史导入与增量扫描',[['已索引日志文件',full(files.files)+' 份'],['最近扫描',date(scan.scanned_at,true)],['程序版本',esc(d.version||'dev')],['本次新增读取',compact(scan.bytes_read||0)+' B'],['本次扫描耗时',full(scan.duration_ms)+' ms'],['等待写完的日志',full(files.pending)],['异常行',full(files.malformed)],['没有用量记录的线程',full(d.unmeasured_threads)]],'服务运行时每 3 秒扫描新增日志，前端每 5 秒刷新。服务关闭期间的记录会在下次启动时补齐。')}${card('Codex 生命周期 Hook',[['已收到事件',full(d.hooks_received)],['最近事件',hook?esc(hook.event):'尚未收到'],['最近触发时间',hook?date(hook.occurred_at,true):'等待首次触发'],['处理方式','结束事件触发 · 后台增量入库']],'Stop / SubagentStop / Interrupt / SessionEnd 触发本地采集。Hook 本身不消耗模型 token。安装后请在终端运行 codex，进入 Codex CLI 后通过 /hooks 信任新增定义；桌面版入口可能不同。')}<section class="panel status-card"><h3>数据保留与边界</h3><p>索引只保留 token 数量、关联 ID、模型、时间、项目路径和简短用户摘要。数据库目录：<code>${esc(d.data_dir||'开发目录下的 .data')}</code>。不会保存完整对话、工具输出或凭据，也不会向外部发送历史记录。</p><p>没有用量记录的线程可能为空会话或缺失统计信息，不能把它们理解成已确认的零消耗。</p><p>采集 hook 可独立工作；页面关闭不影响服务采集，电脑休眠时暂停，醒来后补齐日志。</p></section><section class="panel status-card"><h3>${scan.errors?.length?'需要处理的日志':'采集检查'}</h3>${scan.errors?.length?`<pre>${esc(scan.errors.join('\n'))}</pre>`:'<p>最近一次扫描未报告文件读取错误。</p>'}<p>原始日志格式由 Codex 控制，可能随版本变化。异常行与标记记录需要复核；当前统计只覆盖可读取的本机历史。</p><a href="https://learn.chatgpt.com/docs/hooks" target="_blank" rel="noreferrer">查看官方 Hooks 文档 ↗</a></section></div>`;
}

function parseRoute() {
  const parts=location.hash.slice(1).split('/').map(decodeURIComponent);
  state.kind=parts[0]==='collection'?'collection':parts[0]==='session'&&parts[1]?(parts[2]==='turn'&&parts[3]?'turn':'session'):'overview';
  state.session=parts[1]||null;state.turn=parts[3]||null;
}
function render() {
  const d=state.data;if(!d)return;
  const title=state.kind==='overview'?'Token 用量':state.kind==='collection'?'采集状态':state.kind==='session'?d.title:'Turn 用量';
  $('#page-title').textContent=title;
  $('#page-title').style.fontSize=state.kind==='session'?'25px':'';
  $('#eyebrow').textContent={overview:'USAGE OVERVIEW',session:'SESSION EXPLORER',turn:'REQUEST EXPLORER',collection:'COLLECTION HEALTH'}[state.kind];
  $('#page-subtitle').textContent={overview:'从会话到单次请求，找到消耗发生在哪里。',session:'按主请求归并续跑、子代理与自动审核；点击 turn 可查看每个执行 turn 和逐次消耗。',turn:state.turn,collection:'历史数据回填与后续记录，都在本机完成。'}[state.kind];
  $('#breadcrumb').innerHTML='<a href="#">工作空间</a><span>/</span>'+(state.kind==='overview'?'用量总览':state.kind==='collection'?'采集状态':`<a href="#">用量总览</a><span>/</span>${state.kind==='turn'?`<a href="${routeLink(state.session)}">Session</a><span>/</span>Turn`:`<span class="crumb-title">${esc(d.title)}</span>`}`);
  $('#filters').hidden=state.kind==='collection';$('.range-tabs').hidden=state.kind==='collection';
  $('#custom-dates').hidden=state.range!=='custom'||state.kind==='collection';
  $('#nav-overview').classList.toggle('active',state.kind!=='collection');$('#nav-collection').classList.toggle('active',state.kind==='collection');
  $('#content').innerHTML=state.kind==='overview'?overview(d):state.kind==='session'?sessionView(d):state.kind==='turn'?turnView(d):collectionView(d);
  const execution=$('#execution');if(execution)execution.addEventListener('change',()=>{state.execution=execution.value;state.page=0;state.open.clear();load();});
  const sort=$('#table-sort');if(sort)sort.addEventListener('change',()=>{state.sort=sort.value;state.page=0;render();});
  document.querySelectorAll('.request-conversation').forEach(el=>ensureMessages(el.dataset.requestId));
}

async function load(silent=false) {
  const request=++state.request;
  if(controller)controller.abort();controller=new AbortController();
  parseRoute();
  let path=state.kind==='collection'?'/api/status':state.kind==='overview'?'/api/overview':`/api/sessions/${encodeURIComponent(state.session)}${state.kind==='turn'?'/turns/'+encodeURIComponent(state.turn):''}`;
  if(!silent)$('#content').setAttribute('aria-busy','true');
  try {
    const d=await get(path+'?'+query(),controller.signal);
    if(request!==state.request)return;
    state.data=d;render();$('#error').hidden=true;
    const progress=(state.kind==='collection'?d:d.status)?.progress;
    const banner=$('#scan-progress');
    banner.hidden=!progress?.running;
    if(progress?.running)banner.textContent=progress.total?`正在导入历史：已处理 ${full(progress.completed)} / ${full(progress.total)} 份日志文件。完成后统计会自动更新。`:'正在查找历史日志，统计会自动更新…';
    $('#live-status').className='connected';$('#live-status').textContent='已连接 · '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
  } catch(e) {
    if(e.name==='AbortError')return;
    $('#error').textContent=e.message+'。请通过 Token Lens 启动入口重新打开，或运行 token-lens doctor 检查；现有数据保留在本机。';$('#error').hidden=false;
    $('#live-status').className='';$('#live-status').textContent='连接中断';
  } finally {if(request===state.request)$('#content').removeAttribute('aria-busy');}
}
async function options() {
  try {
    const d=await get('/api/options');
    for(const [id,values,label] of [['model',d.models,'全部模型'],['project',d.projects,'全部项目']]) {
      const el=$('#'+id),old=el.value;
      el.innerHTML=`<option value="">${label}</option>`+values.map(v=>`<option value="${esc(v)}">${esc(id==='project'?shortPath(v):v)}</option>`).join('');
      if(values.includes(old))el.value=old;
    }
  }catch(e){/* Main request exposes connection errors. */}
}
function chooseRange(value) {
  state.range=value;state.page=0;
  document.querySelectorAll('[data-range]').forEach(b=>b.classList.toggle('selected',b.dataset.range===value));
  $('#custom-dates').hidden=value!=='custom';
  if(value!=='custom')load();
}
function reset(all=false) {
  ['search','model','project'].forEach(id=>$('#'+id).value='');$('#scope').value='all';
  state.from=null;state.to=null;state.execution='';chooseRange(all?'all':'7');
}
document.addEventListener('click',e=>{
  const toggle=e.target.closest('[data-request]');
  if(toggle){const id=toggle.dataset.request;state.open.has(id)?state.open.delete(id):state.open.add(id);render();[...document.querySelectorAll('[data-request]')].find(b=>b.dataset.request===id)?.focus({preventScroll:true});return;}
  const retry=e.target.closest('[data-retry-messages]');if(retry){ensureMessages(retry.dataset.retryMessages,true);return;}
  const more=e.target.closest('[data-more-message]');if(more){moreMessage(more);return;}

  const el=e.target.closest('[data-route],[data-range],[data-day],[data-model],[data-page],[data-action]');if(!el)return;
  if(el.dataset.route && !e.target.closest('a,button,details'))location.hash=el.dataset.route;
  if(el.dataset.range)chooseRange(el.dataset.range);
  if(el.dataset.model){$('#model').value=el.dataset.model;state.page=0;load();}
  if(el.dataset.day){const day=el.dataset.day;$('#date-from').value=day;$('#date-to').value=day;chooseRange('custom');applyDates();}
  if(el.dataset.page){state.page+=el.dataset.page==='next'?1:-1;state.open.clear();render();}
  if(el.dataset.action==='reset')reset(true);
});
['model','project','scope'].forEach(id=>$('#'+id).addEventListener('change',()=>{state.page=0;load();}));
let debounce;$('#search').addEventListener('input',()=>{clearTimeout(debounce);debounce=setTimeout(()=>{state.page=0;load();},250);});
$('#clear').addEventListener('click',()=>reset());
function applyDates(){
  const from=$('#date-from').value,to=$('#date-to').value;
  if(!from||!to||from>to){$('#date-message').textContent='请选择有效的起止日期';return;}
  state.from=new Date(from+'T00:00:00').getTime()/1000;
  const end=new Date(to+'T00:00:00');end.setDate(end.getDate()+1);state.to=end.getTime()/1000;
  $('#date-message').textContent='';state.page=0;load();
}
$('#apply-dates').addEventListener('click',applyDates);
$('#refresh').addEventListener('click',async()=>{
  const button=$('#refresh'),label=button.querySelector('span');
  const refreshController=new AbortController();
  const timeout=setTimeout(()=>refreshController.abort(),35000);
  button.disabled=true;refreshing=true;label.textContent='采集中…';
  try {
    const r=await fetch('/api/sync',{method:'POST',headers:{'X-Token-Lens':'1'},signal:refreshController.signal});
    if(!r.ok)throw new Error(r.status===403?'刷新请求被拒绝，请从本机地址打开页面':`刷新接口返回 HTTP ${r.status}`);
    const queued=await r.json();
    const scan=await waitForSync(queued.requested_at,refreshController.signal);
    messageCache.clear();
    await options();await load(true);
    if(scan.errors?.length){$('#error').textContent=`已更新，但有 ${scan.errors.length} 份日志读取失败，详见采集状态。`;$('#error').hidden=false;}
  } catch(e) {
    $('#error').textContent=e.name==='AbortError'?'刷新等待超时，请稍后重试。':e instanceof TypeError?'无法连接本地刷新接口，请确认服务仍在运行。':e.message;
    $('#error').hidden=false;
  } finally {
    clearTimeout(timeout);button.disabled=false;refreshing=false;label.textContent='刷新数据';
  }
});
$('#method-button').addEventListener('click',()=>$('#method').showModal());
$('#method').addEventListener('click',e=>{if(e.target===$('#method')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
window.addEventListener('hashchange',()=>{state.page=0;state.execution='';state.open.clear();messageCache.clear();load();window.scrollTo(0,0);});
options().then(()=>load());
setInterval(()=>{if(!document.hidden&&!$('#method').open&&!refreshing&&!(state.kind==='turn'&&document.querySelector('.request-conversation')))load(true);},5000);
setInterval(()=>{if(!document.hidden&&!refreshing)options();},60000);
