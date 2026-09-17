# v0.2.10 可读 Agent 名称验证

从 session_meta 顶层或 source.subagent.thread_spawn 提取 agent_path、agent_nickname、agent_role，并读取可选的本地 state 数据库名称字段。自定义名称优先于路径任务名，再组合昵称与角色。名称一致用于图例、选择框、执行 Turn、请求来源和导航，完整线程 ID 放在悬停详情。重名加序号，无名称的自动审核按会话内稳定顺序编号，不使用继承的用户正文作为名称。

schema 4 增加名称元数据列。历史补录仅读取已索引文件的日志头，不清除扫描游标。可选 state 字段缺失或为空不会擦除已有 rollout 名称。读取 Codex 数据库始终为只读。

先补 Python 和前端回归用例并确认旧实现失败。62 项 Python 测试、3 组前端测试通过，覆盖嵌套元数据、自定义名称优先、旧 state schema、v3 升级保留用量与游标、重名区分、自动审核回退、执行筛选名称稳定及前端转义。23 份 Python 文件的 3.9 语法与编译、JavaScript、shell 和 Git diff 检查通过。

在真实索引副本补录找到 feature_tracker · Leibniz、handoff_tracker · Turing、review_fix_tracker · Socrates；仅耗时约 0.07 秒，17,134 条用量及全部扫描偏移不变。这三个旧线程本身没有用量记录，未人为添加到统计页面。普通命名子 Agent 的 UI 使用合成回归验证。

真实历史 Turn 浏览器验证：主 Agent、自动审核 25、自动审核 26 替代短 UUID 和继承的长用户正文；图例、选择框、请求来源和导航一致。选择自动审核 25 后图表与明细仍联动为 62 条，恢复全部为 690 条。

发行包 v0.2.10 校验通过并实际升级本机。固定历史 Turn 原有请求字段、汇总及费率配置均不变，仅增加名称元数据。17 份运行文件在正式源码、ZIP 和安装目录完全一致。私人验证材料在被 Git 和发行包排除的 `.data/verification-0.2.10`。
