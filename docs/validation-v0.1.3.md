# v0.1.3 导航与工具摘要验收

验证日期：2026-09-14，macOS Apple Silicon / Python 3.9。

- 44 项 Python 测试通过；包含新增的工具调用归属、字符与 UTF-8 字节统计、空返回与缺失返回、迟到结果、重写、复制、继承历史和 schema 升级测试。HTTP 集成测试验证 Turn 接口新增的工具摘要。
- 前端测试通过：初始树导航不加载对话正文，单条展开继续按需请求；跨页跳转、侧栏收起与展开、状态保存、手动刷新恢复均有覆盖。
- Python 3.9 语法与编译、JavaScript 语法、shell 语法及 Git diff 检查通过。
- 使用真实历史建立工具索引；抽查 12 条请求，摘要的工具返回字符数与对话接口中完整消息的 total_chars 完全一致。
- 浏览器验证：侧栏从 264px 收起至 68px，主内容同步扩宽；刷新后仍保持收起状态，再次展开恢复导航。树节点能展开工具名称与返回字符数，点击工具定位并仅展开一条请求，工具结果附属详情正常；没有横向溢出或新增聚合对话时间线。
- 正式服务通过 v0.1.3 ZIP 升级并完成 schema 3 的历史索引回填；程序、发布包及安装目录的运行文件逐一一致，SHA-256 校验通过。

工具摘要只保存工具名、调用 ID、事件位置、返回文本长度。字符数并非 token 数；不把工具返回另加到 token 总量，不存储工具参数或返回正文。无用量 marker 的调用暂时无法归属请求；缺失日志或定位失效显示未知状态。

复验命令：

```sh
python3 -B -m unittest discover -s tests -v
node tests/test_frontend.js
node --check static/app.js
sh -n install.sh install.command start.command
git diff --check
```
