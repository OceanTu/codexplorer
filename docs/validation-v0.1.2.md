# v0.1.2 对话索引修复验收

验证日期：2026-09-14。环境：macOS Apple Silicon、Python 3.9、SQLite、Node.js。

## 测试先行

Git 提交 `4d4b353` 保存 v0.1.1 源码基线；`096da69` 先加入回归测试。旧实现运行恢复测试出现 8 个失败和 1 个错误，HTTP 集成测试复现刷新后继续返回 410。随后修改正式实现，补充刷新缓存与并发截断读取的回归用例。

最终结果：39 项 Python 测试全部通过；前端交互测试通过；18 个 Python 源文件的 Python 3.9 语法与编译检查通过；JavaScript、shell 语法与 `git diff --check` 通过。

```sh
python3 -B -m unittest discover -s tests -v
node tests/test_frontend.js
node --check static/app.js
node --check tests/test_frontend.js
sh -n install.sh install.command start.command
git diff --check
```

覆盖：rollout 重写、保持尾部不变的中段改写后追加、schema 1 → 2 的一次性重扫、继承记录与同流重复记录只修复定位、失效相邻边界跳过、错误分类、user 运行时块过滤、读取期间截断、HTTP 四组消息契约，以及单条展开和刷新后的重新读取。安装、回退、数据保留等原有测试继续通过。

## 真实历史与正式安装验证

先使用 SQLite 一致性备份保存正式索引，整个验证过程没有修改 Codex 原始日志。基线有 14,838 条用量记录，其中 1,033 条原位置失效，但对应文件仍存在。

- 在独立索引副本上执行 schema 升级和完整重扫：102 份日志、约 974 MB，扫描无错误；所有基线记录仍保留，所有原 token 字段一致。
- 56 条失效记录的目标 response 仍在当前历史中，重扫修复其位置。升级前逐一请求正式服务：56 次 HTTP 410；安装发布包 v0.1.2 并完成重扫后逐一请求：56 次 HTTP 200。
- 这些恢复响应包含 55 条上行、55 条下行；30 个响应带区间恢复或其他关联 warning。接口恢复并不表示原日志一定保存了完整 API 请求报文。context 与 tool_results 的非空案例另由 HTTP 集成测试覆盖。
- 剩余 977 个 response_id 在当时全部可读的 sessions / archived_sessions 中均未找到。文件存在不等于历史 response 还在，现有日志无法还原这些正文；继续保留用量和明确错误，不宣称已恢复。
- 对一份约 5 MB 的真实历史副本执行两次完整扫描：均为 5 条记录、632,490 tokens；按 ID 和各 token 字段计算的摘要完全一致，没有重复计费。
- 正式安装后核对 schema 为 2、服务版本为 0.1.2、扫描错误为空；原 14,838 条记录仍全部存在且各 token 字段没有变化。安装使用固定安装目录，删除临时解压目录后服务正常。

浏览器选择此前失败的历史请求，点击该行“对话”：只展开 1 条请求，显示左右两栏，各有 1 条消息，无错误或横向溢出。点击“刷新数据”后同一请求仍正常展示。Turn 顶部保持用量指标和输入上下文走势，下面为请求明细；没有聚合对话时间线。

发布包：`dist/token-lens-v0.1.2-macos.zip`，校验文件为同名 `.zip.sha256`。包仅含允许分发的程序与文档，不含 Git 历史、索引、真实日志或验证原始数据。
