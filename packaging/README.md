# Token Lens · 安装与使用

在本机查看 Codex 的 Session、Turn 和每次请求的 token 消耗，支持展开可见对话。统计不调用模型、不需要 API key，数据留在自己的电脑上。

## v0.1.4 更新

- 修复大型压缩快照在同一 Turn 的每条请求中重复触发“超过 16 MB，已省略”提示的问题。确认属于压缩快照、运行时元数据或内部记录时，不再把正常过滤误报为对话正文丢失。
- 真正可能包含可见对话或工具结果的超限记录仍会提示，并明确 16 MB 指原始 JSON 大小。大小无法分类时保留提示，不隐藏不确定的内容缺失。
- 读取上限、token 统计与单条对话展开方式保持原样。升级后点击“刷新数据”清除已展开对话缓存即可。

## v0.1.3 更新

- 左侧导航可以收起为图标栏，再次展开；浏览器会记住选择。
- Session 的 Turn 列表与请求明细显示工具名称、调用次数和返回文本大小。字符数可直接查看，悬停摘要可查看 UTF-8 字节数；没有返回记录会单独标注。
- Turn 左侧提供“执行 Turn → 请求 → 工具”的精简折叠导航，按页列出，点击箭头或工具定位到单条对话。工具摘要只读轻量索引，正文继续按需加载。
- 首次升级会重扫历史，建立工具名称、调用 ID 和文本大小索引，不存储工具参数或返回正文，不改变 token 统计。

## v0.1.2 更新

- 修复 rollout 重写后请求对话持续返回 410：重新扫描会更新已有 response 的来源位置，保留原 token 计数；继承历史只更新定位，不重复计费。
- 升级首次启动自动清空扫描游标并重扫历史，保留统计数据。等待采集完成后，刷新页面即可读取修复后的对话。
- 跳过失效的相邻用量 marker 并给出提示；区分文件不可访问、索引偏移失效和 response_id 不匹配。
- 只过滤 user 正文中的 Codex 运行时注入块。Turn 继续使用请求行的“对话”按钮按需展开上下行与工具结果。
- 文件仍在但目标 response 已被移除时，正文无法恢复，用量记录继续保留。

## 环境

- macOS，Apple Silicon 或 Intel；Python 3.9+，需包含标准库 SQLite。
- 已在本机使用过 Codex，历史目录默认是 `~/.codex`，也支持 `CODEX_HOME`。
- 没有 pip、Node、CDN 或第三方 Python 包依赖。安装包不自带 Python；缺少环境时请先从 [Python 官网](https://www.python.org/downloads/macos/) 安装 Python，再运行安装器。

## 安装

1. 解压 ZIP，双击 `install.command`。也可在终端进入解压目录，执行 `sh install.sh`。
2. 安装器检测环境并校验文件，将完整程序复制到 `~/Library/Application Support/Token Lens/`。
3. 默认启动后台服务并打开看板。首次历史导入在后台进行，页面显示已处理的日志文件数量。
4. 打开终端并运行 `codex`，进入 **Codex CLI** 后输入 `/hooks`，检查并信任本工具新增定义。桌面版入口可能不同，安装本工具不会给所有 Codex 客户端添加 `/hooks` 命令。信任状态由 Codex 管理，安装器不会代为绕过。未收到 hook 时，运行中的服务仍会扫描日志。
5. 看到“安装完成”后，**可以删除 ZIP 和整个解压文件夹**。启动入口、后台服务和 hook 均使用安装目录，不依赖下载位置。

首装默认启用 hook，登录自启默认关闭。双击安装会询问是否启用登录自启；终端安装使用默认值。升级默认保留之前的选择。若 macOS 提示无法验证开发者，可在确认安装包来源后通过 Finder 的“打开”或系统“隐私与安全性”流程处理；本包未进行 Apple 签名或公证，安装器不会关闭系统安全设置。

## 日常打开

双击 `~/Applications/Token Lens.command`。入口会启动服务并打开保存的地址；关闭浏览器或终端不影响后台服务。

安装器也会尝试创建 `~/.local/bin/token-lens`，不会覆盖已有其他程序或修改 shell 配置。如果该目录已在 PATH 中，可以直接运行 `token-lens`；否则使用完整路径：

```sh
"$HOME/Library/Application Support/Token Lens/bin/token-lens" open
```

| 命令 | 功能 |
|---|---|
| `open` | 启动服务并打开浏览器 |
| `start` / `stop` / `restart` | 管理后台服务 |
| `status` / `doctor` | 查看版本、Python、路径、服务、扫描与 hook 状态 |
| `sync` | 单次增量导入 |
| `hooks on` / `hooks off` | 安装或移除本工具的 hook |
| `autostart on` / `autostart off` | 开启或关闭登录自启 |
| `uninstall` | 停止服务、移除程序与本工具的 hook，保留统计数据和配置 |

登录自启使用当前用户的 LaunchAgent，不需要管理员权限。`stop` 会停止当前服务并卸载当前登录会话的任务；保留的自启设置会在下次登录生效。要持续关闭自启，使用 `autostart off`。

## 安装参数

```sh
# 默认安装，不询问选项；仍会启动服务和打开浏览器
sh install.sh --yes

# 自定义 Codex 目录、端口，开启登录自启
sh install.sh --codex-home /path/to/.codex --port 8766 --autostart

# 安装但暂不启动，不配置 hooks
sh install.sh --no-start --no-hooks

# 在当前用户的其他固定目录安装
sh install.sh --install-dir "/path/with spaces/Token Lens" --no-open

# 从旧源码版本复制统计索引；仅首次安装可用
sh install.sh --migrate-from /path/to/old/token-lens
```

其他参数：`--no-autostart`、`--hooks`、`--no-shortcuts`。可用 `TOKEN_LENS_PYTHON=/absolute/path/python3 sh install.sh` 指定解释器。安装目录必须位于解压目录和旧源码目录之外。默认端口 8765 被占用时会选择其他可用端口并持久保存，不会停止其他程序。

迁移前请停止旧源码版本的看板服务；安装器用 SQLite 一致性备份复制旧 `.data/usage.sqlite3`，迁移明确属于旧路径的 hook，保留原目录。新服务会补齐迁移期间的新日志。完整对话仍按需读取原始 Codex 日志，不会随索引复制。

## 找不到 /hooks 时

`/hooks` 是官方文档描述的 **Codex CLI 交互命令**，需要先在终端运行 `codex`，再在其交互界面输入。它不是终端里的独立命令，也不能据此推断某个桌面版本一定有同名入口。

在出现问题的电脑上先执行：

```sh
"$HOME/Library/Application Support/Token Lens/bin/token-lens" doctor
codex --version
codex features list
```

- `hooks_file` 是实际写入的配置路径；`hooks_configured: true` 只代表文件中存在本工具的配置，不代表 Codex 已加载、信任或执行。
- `codex_cli_on_path: null` 表示当前终端 PATH 中没有 `codex`，不等于没有桌面版。请按 [Codex CLI 官方说明](https://learn.chatgpt.com/docs/codex/cli) 配置命令行入口。
- 若 CLI 中也找不到 `/hooks`，先核对版本和 `features list` 输出。新旧版本及功能开关可能不同，不要通过重复安装 Token Lens 来猜测修复。
- 核对 Codex 实际使用的 `CODEX_HOME` 与 `doctor` 中的 `codex_home` 是否一致，安装后重新打开 Codex 会话再检查。
- 没有可用 hook 时，保持 Token Lens 服务运行仍能通过定期扫描导入新增日志；停机期间的记录会在下次启动补齐。

参考：[官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)。

## 升级与卸载

升级时解压新版 ZIP，再运行安装器即可。程序安装到单独的版本目录，配置和数据不随升级覆盖；固定启动入口和 hook 命令保持稳定。重复安装不会重复添加 hook。

切换前校验新程序，已有数据库通过 SQLite backup API 备份到 `backups/`，再检查启动响应；升级失败恢复旧版本、配置及数据库。数据库来自更高格式版本时拒绝降级。旧版本和备份保留供恢复，会占用磁盘空间。本包不会自动下载更新，也不承诺任意版本之间均可降级。

运行 `token-lens uninstall` 后，`data/`、`logs/`、`backups/` 和安装配置仍保留在安装目录。重新安装可继续使用。需要彻底清理本工具数据时，在卸载完成后手动删除该安装目录；Codex 自身的原始日志不会被删除。

## 数据与排错

```text
~/Library/Application Support/Token Lens/
  bin/token-lens          固定管理入口
  current -> versions/…   当前程序
  versions/               独立的程序版本
  config.json             Codex 路径、Python、端口等
  data/                   SQLite 索引与待处理事件
  logs/                   服务日志
  backups/                升级前的一致性备份
```

服务只监听 `127.0.0.1`。只读 Codex 的历史、归档和可选标题索引，不读取凭据、不上传数据。数据库保留计数、关联 ID、模型、项目路径和简短摘要；完整对话在展开时从原始日志读取。原始日志丢失时无法再展开相应对话。

刷新失败先运行 `doctor`，再查看 `logs/server.log` 和 `logs/service-output.log`。若 Python 被移除或移动，重新运行安装包可更新解释器路径；Python 已不可用时，安装器仍会重新检测可用版本。

总 token = 输入 + 输出，缓存包含在输入中、推理包含在输出中，不重复相加。日志 token 不等于订阅套餐额度或账单，统计只覆盖可读取的本机历史。Codex 日志格式变化可能需要新版适配；hook 是否可用及信任状态取决于当前 Codex 版本。

ZIP 附带 SHA-256 文件校验值，包内 `manifest.json` 用于检测文件损坏；校验值本身不替代可信的分发来源。

## v0.2.0 费用估算

左侧“费率设置”可按模型分别配置 credits 和美元的输入单价、缓存 / 输出 / 推理倍率及 Fast、长上下文规则。保存立即重算历史，升级保留配置。总览、Session、Turn 和请求均同时显示 token 与两种费用估算；图表和排序可切换单位。未知价格标为未定价，不代表免费。美元是 API 等价估算，credits 是独立口径，都不是实际账单；默认值来源和假设可在设置页查看。
