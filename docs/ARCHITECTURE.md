# ForgeAgent 设计说明

## 1. 目标与边界

本项目刻意把重点放在 agent 的“控制平面”上，而不是界面：如何把模型的非确定性输出转为可验证的本地动作，如何让错误重新成为模型可见的观察，以及如何保证循环最终停止。运行时只使用 Python 标准库，未使用任何 agent 框架、SDK、托管代码执行器或文件服务。

安全模式是应用层防误操作机制，不宣称替代容器或操作系统沙箱。被允许的测试命令仍会执行目标仓库中的代码，因此不应在不可信项目上直接运行。

## 2. 组件

| 组件 | 责任 | 关键设计 |
| --- | --- | --- |
| `model.py` | OpenAI 兼容 HTTP 调用 | 标准库 HTTP；SSE 文本/tool-call/推理增量拼接；DeepSeek 推理上下文回传；指数退避 |
| `agent.py` | 决策循环与会话 | 工具结果回传、跨用户回合历史、会话重置、退出原因与多层熔断 |
| `history.py` | 上下文管理 | 固定保留系统规则和原始任务；只按完整 assistant/tool 块裁剪 |
| `sessions.py` | 会话持久化 | 原子 JSON 快照；工作区与版本校验；完整 ID/前缀/latest 恢复 |
| `tools/core.py` | 工具协议 | JSON 参数解析、轻量 schema 校验、异常隔离、长结果头尾截断 |
| `tools/filesystem.py` | 文件操作 | 13 个检查/读取/编辑工具；敏感路径拒绝；事务写入；哈希确认删除 |
| `tools/git.py` | 仓库检查 | status/diff/log 三个只读工具；禁用外部 diff；路径和输出受限 |
| `tools/command.py` | 命令执行 | 默认无 shell；允许列表；超时；标准输出和错误统一反馈 |
| `audit.py` | 可观测性 | 追加式 JSONL；敏感字段和常见 key 形式脱敏 |
| `ui.py` / `cli.py` | 交互入口 | token 流式展示；持续 REPL；多行粘贴；高风险工具确认；会话命令与运行摘要 |

## 3. 核心循环

```text
history = [system, user task]
for step in 1..max_steps:
    request = compact(history)
    assistant = model(request, tool_schemas)
    history += assistant
    if assistant has no tool calls:
        stop(completed)
    for call in assistant.tool_calls:
        if third consecutive identical call:
            result = loop_guard_error
        else:
            result = validate_and_execute(call)
        history += tool_result(result)
        audit(result)
    if six consecutive tool errors:
        stop(tool_error_budget)
stop(max_steps)
```

一次 `run` 对应一个用户回合；REPL 在后续回合调用 `run(..., continue_session=True)`，把新用户消息追加到同一个 `ConversationHistory`。`/new` 同时更换历史和审计文件，确保新会话没有隐式继承。

每个协议安全点还会把未压缩历史原子写入 `.forge/sessions/<session-id>.json`。恢复时重新构造 `ConversationHistory`，并继续向同 ID 的审计文件追加事件。快照包含消息和 DeepSeek `reasoning_content`，但不存储 API Key、Base URL 或其它配置凭据。`/new` 只切换到新会话，不删除旧快照。

工具失败不会抛出到主循环，而会序列化成带 `ok=false` 的 tool 消息。模型因此能看到“参数缺失、路径越界、精确替换出现多处匹配、测试失败”等事实，并在下一轮修正策略。

交互式 CLI 会在执行标记为高风险的工具前询问用户，包括文件创建/覆盖/编辑、目录创建、复制、移动、删除和命令执行。用户拒绝或输入结束信号时，工具不会运行，而是返回带 `confirmation_required` 的失败 observation；模型可以据此解释、缩小范围或等待新的指令。确认提示会隐藏常见密钥字段和 API key 形式。输入 `a`/`all` 会把当前进程后续高风险工具设为自动批准；命令行 `--yes`/`--auto-approve` 可在明确授权时从启动阶段跳过提示。通过 Python API 构造 `ConsoleUI` 时默认关闭提示，便于脚本和单元测试注入自己的审批回调。

## 4. 上下文为什么按块裁剪

原生 tool calling 协议要求 assistant 的 `tool_calls` 与随后相同 ID 的 tool 消息配对。简单地保留最后 N 条消息可能留下孤立 tool 消息，导致网关拒绝请求。ForgeAgent 把一次 assistant 响应及其全部 tool 结果视为不可分割的块：从最新块向前装入预算，旧块生成确定性摘要。系统规则和用户原始任务永远固定保留。

这里使用字符预算而非 tokenizer：它不绑定某家模型，也无额外依赖。代价是 token 估计不够精确，所以默认留有余量；生产版本可以注入特定模型的 tokenizer。

## 5. 文件一致性

- `Workspace.resolve` 将相对和绝对路径统一规范化，再检查是否仍位于根目录内，可阻止 `../` 和绝对路径越界。
- 写入先落到同目录临时文件，再由 `os.replace` 原子替换，避免进程中断留下半个文件。
- 覆盖已有文件必须显式声明 `overwrite=true`。
- 精确替换默认只允许一个匹配；数量不符则完全不写入，避免模型误改同名代码。
- `apply_edits` 在内存中验证全部替换后一次写入，任一编辑失败则整体不变。
- 移动文件拒绝覆盖目标；删除文件必须提供与当前内容一致的 SHA-256。
- `.env*`、`.forge`、`.git`、虚拟环境和依赖缓存由工具层强制拒绝访问。
- 工具记录修改前后 SHA-256，Agent 据此汇总真正发生变化的文件。

## 6. 命令策略

安全模式不启用 shell，而是先解析参数，再用 `subprocess.run(..., shell=False)` 执行。默认允许 Git、Python 测试、Node、Java、Go、Rust、.NET 等常见开发程序；拒绝管道、重定向、命令拼接、父目录跳转和 `python -c`/`node -e` 等内联代码。每个命令有 1-300 秒超时。

当模型调用 `python`、`python3` 或 `py` 时，执行器会将其替换为启动 ForgeAgent 的 `sys.executable`，确保测试和脚本使用同一个虚拟环境，而不是意外落到系统 Python。

这是可解释的折中：白名单降低误操作面，却会拦截某些合法构建工具。受控环境可用 `--unsafe` 关闭命令策略，但文件工具仍受工作区边界保护。

## 7. 终止与故障处理

正常终止由模型返回无 tool calls 的文本触发。非正常终止都有独立原因：`model_error`、`empty_response`、`tool_error_budget`、`max_steps`，人工 Ctrl+C 返回 130。相同工具和相同规范化参数连续出现三次时，第三次不会执行，而把熔断错误反馈给模型。

API 层仅对可能恢复的 429、5xx、网络错误和超时重试；其他 4xx 立即失败。流式响应一旦已经向终端输出内容便不自动重试，避免用户看到重复片段。重试次数用尽后不会掩盖错误，审计轨迹会记录停止点。

## 8. 流式协议

客户端直接解析 Chat Completions 的 SSE `data:` 行。普通文本 delta 到达后立即交给 UI；tool call 的 ID、函数名和 JSON 参数可能分散在多个 chunk 中，因此按 `index` 分桶拼接，收到 `[DONE]` 后再构造完整调用。部分兼容网关不支持流式传输，可用 `--no-stream` 回退到普通 JSON 响应。

DeepSeek 的思考模型还会把 `reasoning_content` 放在独立 delta 中。ForgeAgent 对其完整拼接，但不把内部推理流打印到终端；assistant 消息进入历史时保留该字段，并在工具结果后的下一次请求中原样回传。这样既保持界面只展示最终文本，也满足 DeepSeek 在携带 `tools` 时的连续会话协议。`provider` 预设只负责默认地址、模型、密钥变量和思考参数，核心 Agent 循环没有厂商分支。

## 9. 可测试性

`AgentRunner` 依赖 `ModelClient` 协议而非具体网络客户端。测试可注入按顺序返回响应的 `ScriptedClient`，在无 API key、无网络条件下覆盖完整闭环。测试还覆盖路径逃逸、事务替换、schema 错误、命令策略、上下文配对和循环熔断。

## 10. 可继续演进的方向

1. 为不同模型注入精确 tokenizer 和缓存感知的上下文预算。
2. 在工具层增加用户审批回调，而不是只靠静态策略。
3. 用容器/低权限账户承载命令执行，形成真正的系统隔离。
4. 增加 AST 感知编辑与结构化 Git 差异摘要。
5. 为大型仓库增加增量索引和更精确的检索预算。
