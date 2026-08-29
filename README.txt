ForgeAgent：从零实现的编程智能体

Git仓库：https://github.com/quiet402/CodingAgent

项目简介
ForgeAgent 不使用 LangChain、AutoGen 等 agent 框架。它直接调用 OpenAI 兼容的 Chat Completions/tool calling 接口，自行完成“模型决策—本地工具执行—结果回传—继续决策”的循环。项目仅依赖 Python 3.10+ 标准库。

运行方法
1. 通过环境变量提供凭据，切勿写入仓库：
   Windows PowerShell：$env:FORGE_API_KEY="你的密钥"
2. DeepSeek：设置 DEEPSEEK_API_KEY 后运行 forge --provider deepseek；其它网关使用 FORGE_BASE_URL、FORGE_MODEL。
3. 安装：python -m pip install .；随后可在任意项目目录运行 forge。
4. 源码交互：python main.py -w 目标项目目录；在 forge> 后反复输入任务。
5. 单次运行：forge -w 目标项目目录 "修复问题并运行测试"
6. Windows 也可运行：.\run.ps1 -Workspace 目标项目目录
7. 测试：python -m unittest discover -v

特色功能
内置批量读取、模式搜索、事务编辑、复制移动、哈希确认删除、只读 Git 检查和命令执行等十七个工具；路径限制在工作区，凭据及内部目录强制不可读。交互模式对文件修改、删除、移动、复制和命令执行请求确认，输入 a 可放行本次后续操作，也可用 --yes 启动时跳过确认，拒绝后反馈模型。支持流式输出、连续会话和重启恢复。安全模式禁止 shell 管道、内联脚本及未授权程序。Agent 具备 API 重试、错误回传、重复调用熔断、最大步数和人工中断。上下文超限时按完整 assistant/tool 块压缩。每次运行生成脱敏审计轨迹并汇总结果。

部署说明见 docs/INSTALL.md；设计说明见 docs/ARCHITECTURE.md；演示脚本见 docs/VIDEO_SCRIPT.md；答辩准备见 docs/DEFENSE_QA.md。
