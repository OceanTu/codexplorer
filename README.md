# Token Lens

一个完全在本机运行的 Codex token、credits 与美元估算工具。Python 3.9+ 标准库、SQLite、原生 HTML/CSS/JavaScript，无第三方依赖，无 API key，也不调用模型做统计。

## 安装包与分发

普通使用推荐下载 `dist/token-lens-v0.2.2-macos.zip`，解压后双击 `install.command`。安装器会将全部运行文件复制到 `~/Library/Application Support/Token Lens/`，启动并检查本地服务。看到安装完成后，可以删除 ZIP 和解压目录；日常双击 `~/Applications/Token Lens.command` 即可打开。

首版支持 macOS，依赖已有 Python 3.9+，不自带运行环境。无需 pip、Node 或 sudo；默认启用 hook、关闭登录自启。程序与统计数据分开保存，支持重复安装、升级备份、失败回退、卸载保留数据。详见 [发行包使用说明](packaging/README.md)。

```sh
# 生成可重复构建的发行包和 SHA-256 校验文件
python3 -B scripts/build_release.py

# 安装后管理（~/.local/bin 已在 PATH 中时）
token-lens open
token-lens doctor
token-lens autostart on
token-lens uninstall
```

构建只包含明确列出的程序、前端、安装脚本和使用说明，不会包含 `.data/`、个人分析报告、运行日志、测试产物或 Codex 历史。安装脚本也会校验包内文件；源码根目录的 `install.sh` 用于构建发行包，不直接从开发目录安装。

## 源码运行

```sh
cd /path/to/token-lens
python3 -B app.py serve
```

打开 [本地看板](http://127.0.0.1:8765)。也可双击 `start.command`。服务只监听 `127.0.0.1`，在终端按 Ctrl+C 停止。再次启动会自动补齐停机期间新增的历史。

功能：

- 总览：每日消耗、模型分布、缓存命中、未缓存输入、输出与推理、子代理占比。Token / Credits / 美元切换图表和消耗排序，各层明细同时展示三种数值。
- 费率设置：按精确模型 ID 配置普通输入基价（每百万 token），以及缓存读取、缓存写入、非推理输出和推理倍率。分别编辑 credits 与 USD，支持 Standard / Fast、长上下文阈值和倍率，恢复单模型默认值、添加未知模型。保存后立即重算历史，无需扫描，升级保留配置。
- 时间、项目、模型、主代理 / 子代理筛选；按会话、项目或 turn 摘要搜索。
- Session → 主 turn → 请求下钻；查看上下文输入增长、自动审核用量、状态与记录跨度。主 turn 页还可筛选每个实际执行 turn（包括自动续跑和子代理），单独查看其消耗。
- 请求行的“对话”可展开上行新增用户 / 工具消息、下行助手回复 / 工具调用，并单独查看工具执行结果和本轮最初用户消息。长文本可继续加载；展开阅读时暂停自动刷新。展开底部“原始记录信息”可查看 response ID、实际线程、原始文件与字节偏移、统计来源和异常标记。
- 采集状态页显示增量扫描、hook 事件、异常行和没有用量记录的线程。
- 左侧导航可收起、展开并记住状态。Turn 页提供“执行 Turn → 请求 → 工具”的精简树，每页最多 50 条请求，点击箭头或工具定位并展开单条对话。
- Turn 列表、请求明细和本轮工具摘要显示工具名称、调用次数、返回文本字符数；悬停查看 UTF-8 字节数。空返回计为 0，缺失返回标为“未记录”；无法关联的请求标为“定位不可用”。工具返回文本不等于模型输出 token，也不计入 token 总量。

首次历史导入在后台运行，页面显示文件处理进度。服务每 3 秒扫描新增日志；页面每 5 秒刷新。手动“刷新数据”会等待新一轮采集完成后更新。关闭浏览器不会停止服务。运行日志写入 `.data/server.log` 并自动轮转，启动终端的输出通道断开不会影响刷新接口。

## 后续 Hook 采集

```sh
# 预览即将安装的配置
python3 -B app.py hook-config

# 合并至 ~/.codex/hooks.json，保留已有 hooks 并备份原文件
python3 -B app.py install-hooks
```

安装后，在支持 hooks 的 Codex CLI 中输入 `/hooks`，检查并信任 Token Lens 的四个新增定义：`Stop`、`SubagentStop`、`Interrupt`、`SessionEnd`。可能需要重新打开会话以加载配置。**安装配置不等于已经获得 Codex 的 hook 信任**；采集状态页收到真实事件后才会显示触发时间。不要跳过信任校验。

Hook 从 stdin 只提取事件名、session ID 和 turn ID，写入本地队列后启动后台采集器，立即输出 `{}`。后台在 0.25 秒后以及再过 1 秒重读增量，兼容结束时日志落盘稍晚的情况。重复采集受文件锁与请求 ID 唯一约束保护。即使看板服务未运行，hook 也可以独立入库；未及时落盘的记录会在下一次 hook 或服务扫描补齐。当前版本的 hook 不承诺覆盖所有隐藏执行路径，因此服务运行时的扫描仍是必要补充。

不会修改 `config.toml` 或覆盖已经存在的 `notify`，本机 Computer Use 通知照常保留。移除本工具的 hook：

```sh
python3 -B app.py uninstall-hooks
```

移除按本工具当前的精确命令匹配，保留其他 hook。如果移动了工具目录，先在旧目录卸载，再在新目录安装。

## 统计口径

1. **总量 = 输入 + 输出。**缓存读取 / 写入属于输入，推理属于输出，不重复相加。未缓存输入 = 输入 − 缓存读取，包含可能存在的缓存写入。
2. **新版日志**优先读 `token_usage_record.usage`，按 `response_id` 全局去重。关联 `session_id`、`thread_id`、`turn_id`、`root_turn_id`；模型与推理强度取当时的 `turn_context`。
3. **旧版日志**读 `event_msg.token_count.info`，对同一个日志流的 `total_token_usage` 做差分。相同快照忽略；首次遇到累计值或计数器重置时仅取 `last_token_usage`。没有 last 值的未知初始基数不计为新消耗。旧版一次差分可能覆盖多次请求，界面因此使用“请求 / 记录”标签。
4. **两种累计值不可混用。**新版 `thread_token_usage` 跨恢复延续，旧版 `total_token_usage` 可能按窗口归零。解析器维护独立基线，从旧版差分中扣除已经计入的逐次记录，避免双计。新版先落盘、旧版随后补充是本机验证过的常见顺序；同累计基数下反向落盘也支持去重。
5. **子代理与自动审核**按 `session_id` 归入所属 session；存在 `root_turn_id` 时合入主 turn（包括自动续跑），无法关联时单列；主 turn 页的执行 Turn 筛选器可逐个分析实际 `turn_id`。记录跨度是首末消耗时间之差，可能包括等待间隔，不是模型活跃时长。fork 记录使用明确的继承 ordinal 边界（若有）排除复制历史；不递归重放 `history_base`。同一请求跨文件复制或归档不重复计量。
6. Session 名称从 `session_index.jsonl` 以及只读 `state_*.sqlite` 的 `name/title` 补齐；不使用 `tokens_used` 做消耗统计。时间范围按请求发生时间筛选，图表按服务电脑的本地日期聚合。session/turn 页也遵循当前筛选条件；turn 编号在筛选间保持一致。
7. **费用是估算，不换算套餐额度百分比或实际账单。**Credits 使用 Codex 费率，美元表示同样用量按 API 价格计算的成本，两者独立，不相加。缺少费率或子集计数异常时标为未定价，不当作零；混合结果只汇总已定价部分，并列出缺失记录数与 token。本机日志不能恢复服务端隐藏消耗、其他设备使用、已删除历史或缺失日志。

标记说明：`counter_reset` 表示旧累计值回退，改用本次值；`prior_usage_unattributed` 表示首次累计值包含之前的未知基数；`inconsistent_subsets` 表示子集计数异常；`total_mismatch` 表示报告总量与输入加输出不一致。单次明细保留 `reported_total` 以便核对。`unknown` 模型表示日志缺失模型信息；不会用当前模型猜历史模型。

## 费率与费用估算

Credits 估算统一显示 1 位小数，底层计算、排序和美元精度不变。摘要卡和各层 Credits 明细显示相对于**套餐参考额度**的占比。在左侧“费率设置”的“套餐参考额度”中填写套餐名称与 Credits 额度，默认是 **50,400 credits / 7 天 ×20 套餐等价**，可按自己的套餐修改。点击“保存配置”后立即应用，重启或升级保留。占比 = 当前筛选下未舍入的 Credits ÷ 配置额度，不按筛选天数折算，也不限制在 100%；小于 0.1% 的正值显示 `<0.1%`。未定价记录不算作零占比，混合结果明确标注已知部分。这是等价参考，不是官方套餐剩余额度。

左侧进入 **费率设置**。所有倍率相对于普通输入基价；基价留空表示未知，填 0 表示明确免费。保存采用整份配置原子写入，检测其他页面的并发修改；编辑期间不自动刷新覆盖表单。模型 ID 精确匹配，支持手动添加其他模型，不会猜测版本号别名的价格。

按每条请求计算，再按每日、模型、session、turn、执行 turn 聚合。令 I 为输入、C 为缓存读取、W 为缓存写入、O 为输出、R 为推理，则五个互斥分类为 `I-C-W, C, W, O-R, R`。各分类乘以对应基价和倍率，再除以 1,000,000。只在展示时格式化小数，聚合前不舍入。工具返回字符数不参与计费；已有 token 总量不变。

默认费率核对日期 **2026-09-14**：

| 模型 | Credits 普通输入 / 百万 | USD 普通输入 / 百万 | 缓存读取倍率 | 输出 / 推理倍率 |
| --- | ---: | ---: | ---: | ---: |
| gpt-6-astra | 250 | $10 | 0.1 | 5 |
| gpt-5.6-sol | 100 | $4 | 0.1 | 5 |
| gpt-5.6-terra | 50 | $2 | 0.1 | 6 |
| gpt-5.6-luna | 5 | $0.2 | 0.1 | 6 |

Sol 为促销价，官方注明至少持续到 2026-11-21。自动审核 `codex-auto-review`、`unknown` 及其他未核实模型默认不定价，允许手动填写。

- API 缓存写入默认 1.25×。单请求输入严格超过 272,000 时，对整个请求输入及缓存应用 2×，输出及推理 1.5×；Fast 另乘 2×。
- Codex credits 公开费率未单列缓存写入和长上下文加价：本工具明确假设缓存写入 1×、不加长上下文费，可自行修改；Fast 另乘 2.5×。
- 历史日志缺少可靠的服务档位记录，默认按 Standard。设置 Fast 会应用到该模型的全部历史，不声称识别了真实档位。旧版差分可能涵盖多次请求，按单条记录判断长上下文只是近似。
- 配置统一用于全历史，没有历史价格时段；不含工具服务费、税费、订阅费。默认值不会联网自动更新覆盖用户配置。
- 配置保存在数据目录 SQLite `settings.pricing`，不改 usage 账本，不需要 schema 升级或历史重扫。源码和发行包只包含公共默认值。

接口：`GET /api/pricing` 读取配置、默认值及来源；`POST /api/pricing` 保存 `models`、`credit_reference: {name, credits}` 与原始 `revision`（名称 1–80 字符，额度大于 0 且不超过 1e12），需要同源 JSON 和 `X-Token-Lens: 1`。总览、Session、Turn 接口同时返回顶层 `credit_reference`，与本次估算读取同一份配置。旧版本缺少此设置时默认 50,400，旧客户端省略该字段时保留现有配置。各用量接口增加 `costs.credits` / `costs.usd`，含 total、五类 breakdown、priced_requests、unpriced_requests 和 unpriced_tokens。所有记录未定价时 total 为 null；空结果或明确免费时为 0。单请求另含实际用于估算的 effective_rates、mode 和 long_context。

官方来源：[Codex credits](https://learn.chatgpt.com/docs/pricing)、[Fast](https://learn.chatgpt.com/docs/agent-configuration/speed)、[API 缓存规则](https://developers.openai.com/api/docs/guides/prompt-caching)、[Astra](https://developers.openai.com/api/docs/models/gpt-6-astra)、[Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)、[Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)、[Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)。

## 数据与维护

原始读取目录：`$CODEX_HOME/sessions`、`$CODEX_HOME/archived_sessions`（默认 `~/.codex`）。没有读取 `auth.json`。索引在 `.data/usage.sqlite3`，仅保存计数、关联 ID、时间、模型、项目路径与最多 240 字的用户摘要；不复制完整对话或工具输出。请求对话在展开时才从索引指向的原始日志中读取，并通过本机接口显示，正文不写入数据库。`.data/` 已从 Git 排除。

对话关联按同一日志中相邻用量记录划分：上行是该区间新增的可见用户 / 工具消息，不是包含全部缓存历史的 API 报文。工具调用属于助手输出，其执行结果单列并归入下一请求的输入；旧版日志晚记 token 的顺序也单独处理。系统 / 开发者指令、内部推理和媒体二进制不展示；图片、音视频用附件占位说明。若日志丢失或位置变化，显示原因并保留原有 token 统计。单次读取区间超过 32 MB、单行超过 16 MB 或消息数量超过上限会明确提示省略；普通长文本每次加载 12,000 字符。

导入器保存 inode、字节偏移、时间、大小、已解析前缀的 SHA-256 及解析状态；追加解析与 SQLite 写入在事务内完成。文件增长时先流式校验此前缀，变化则完整重扫，避免只比较尾部而漏掉中段重写。未变化文件直接跳过；增长文件的前缀校验会额外读取历史字节，但不重复解析或计费。未结束的 JSONL 行留待下次解析；畸形行计数后跳过。重扫通过 UPSERT 更新已有请求的位置和关联元数据，保留原 token 计数；继承 ordinal 或同流重复记录只修复已有请求定位，不产生新计费。

v0.1.2 将索引 schema 升到 2，只清空可再生的 `files` 游标，保留用量、会话和 hook 数据，首次启动自动完整重扫。手动刷新完成后会重新读取已展开请求，清除缓存的旧错误。Turn 页面仍按单条请求展开，不加载整轮聚合时间线。原文件尚在但目标 response 已被移除时，无法还原不存在的正文，用量仍保留。

user 正文过滤 Codex 自动注入的环境、插件、浏览器和 AGENTS 指令块；assistant 与 tool 文本保留。相邻 marker 失效时跳过并显示区间扩大的 warning。当前记录无法匹配时，接口区分 `source_unavailable`（文件不存在或不可访问）、`stale_offset`（偏移失效）和 `response_mismatch`（response_id 不匹配），不将 JSON 解析失败描述成文件删除。

v0.1.3 将 schema 升到 3，为树导航建立工具元数据索引：只保存工具名、调用 ID、事件位置、可见返回文本的字符和字节数，不保存工具参数与完整正文。升级后自动重扫，日志改写时重建该文件的工具索引。调用按相邻有效用量 marker 归属请求，结果按同文件 call_id 关联；复制历史仍按 response_id 汇总。没有用量 marker 的调用暂时无法归属某次请求，未落盘的结果在后续采集时补齐。字符数按文本视图计算，媒体使用占位符，不代表原始媒体大小或精确 token 数。

v0.1.4 在超大记录触发大小提示前，使用最多 4 KB 的 JSON 前缀确认外层记录类型。压缩快照、运行时元数据和内部记录本来就不作为正文展示，因此不再重复提示“对话已省略”。可见消息、工具调用及返回结果仍受原有 16 MB 单条读取上限保护；不能确认类型时继续告知内容可能缺失。分类只读取外层 type、payload.type 与 role，不会把工具正文引用的同名字段当成记录类型。

```sh
# 单次补齐、重建派生索引（不修改 Codex 原始日志）
python3 -B app.py sync
python3 -B app.py sync --rebuild

# 自定义路径与端口；全局参数放在子命令前
python3 -B app.py --codex-home /path/to/.codex --data-dir /path/to/index serve --port 8766

# 统计与运行集成测试（集成测试需要允许本机 loopback 监听）
python3 -B -m unittest discover -s tests -v
node tests/test_frontend.js
node tests/test_pricing_frontend.js
node --check static/app.js
node --check static/pricing.js
```

源码运行不会注册自启系统服务，下次开机运行 `start.command` 即可。安装包版本提供可选的 macOS 登录自启，可通过 `token-lens autostart on/off` 管理。Codex hook 配置在安装后持续保留，不依赖看板自启。

## 实现结构

```text
Codex 原始 JSONL ── 历史回填 / 增量读取 ─┐
                                       ├─ SQLite ─ HTTP API ─ 本地前端
Stop / Interrupt / SubagentStop ─ hook ─┘
```

- `codex_usage/importer.py`：日志适配、请求去重、差分与恢复、turn 关联。
- `codex_usage/store.py`：派生索引结构。
- `codex_usage/api.py`：统一筛选与四层汇总。
- `codex_usage/pricing.py`、`static/pricing.js`：可编辑费率、逐请求估算、费用展示与配置页。
- `codex_usage/messages.py`：按请求关联并按需读取可见对话，支持旧版工具结果顺序和长消息分段。
- `app.py`：CLI、后台增量采集、hook 安装 / 卸载和本地 HTTP 服务。
- `codex_usage/lifecycle.py`、`manage.py`：安装后的固定管理入口、进程身份校验、服务及登录自启管理。
- `codex_usage/installation.py`、`installer.py`：包校验、独立目录安装、SQLite 一致性迁移、升级及回退。
- `scripts/build_release.py`：白名单打包与校验文件；`packaging/README.md`：分发使用说明。
- `static/`：中文看板，不依赖 CDN。
- `tests/`：重复快照、恢复基数、混合日志、文件截断 / 半行、子代理归并、筛选、hook 保留、独立采集与 HTTP 集成。

官方参考：[Hooks](https://learn.chatgpt.com/docs/hooks)（事件、stdin、信任流程）；[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)（`notify` 配置）。日志适配基于本机 Codex 0.150–0.153 系列实际记录，rollout 格式不是稳定的公共接口；升级后可用采集状态和测试复核。
