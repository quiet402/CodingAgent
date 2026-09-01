# DeepSeek 接入指南

CodingAgent 原生支持 DeepSeek 的 OpenAI 兼容 Chat Completions 接口，包括流式文本、原生工具调用和思考上下文回传。API Key 只从进程环境读取，不会写入配置文件、源码或安装包。

## 最快启动

在 PowerShell 中设置当前窗口的环境变量，然后启动持续会话：

```powershell
cd D:\NJU_TEST
$env:DEEPSEEK_API_KEY="替换为你的 DeepSeek API Key"
.\.venv\Scripts\forge.exe --provider deepseek
```

如果已经通过 pipx 全局安装，可在任意项目目录直接运行：

```powershell
cd D:\path\to\your-project
$env:DEEPSEEK_API_KEY="替换为你的 DeepSeek API Key"
forge --provider deepseek
```

启动横幅应显示：

```text
provider   deepseek
model      deepseek-v4-pro
```

随后在 `forge>` 后连续输入任务；用 `/paste` 输入多行任务，用 `/new` 开始新会话，用 `/quit` 退出。

## 模型与思考模式

默认预设为：

- API 地址：`https://api.deepseek.com`
- 模型：`deepseek-v4-pro`
- 思考模式：`enabled`
- 推理强度：`high`

低延迟模式：

```powershell
forge --provider deepseek --model deepseek-v4-flash
```

关闭思考或调整强度：

```powershell
forge --provider deepseek --thinking disabled
forge --provider deepseek --reasoning-effort max
```

`deepseek-chat` 和 `deepseek-reasoner` 是已退役的旧别名，本项目不把它们设为默认值。当前模型与能力应以 [DeepSeek 官方模型与价格页](https://api-docs.deepseek.com/quick_start/pricing/) 为准。

## 环境变量方式

以下方式等价，适合部署脚本：

```powershell
$env:FORGE_PROVIDER="deepseek"
$env:DEEPSEEK_API_KEY="替换为你的 DeepSeek API Key"
$env:DEEPSEEK_MODEL="deepseek-v4-pro"
$env:FORGE_THINKING="enabled"
$env:FORGE_REASONING_EFFORT="high"
forge
```

通用变量 `FORGE_API_KEY`、`FORGE_BASE_URL` 和 `FORGE_MODEL` 的优先级高于厂商专用变量，便于接入代理网关。命令行参数又高于环境变量。

## 为什么专门处理 reasoning_content

DeepSeek 思考模式把推理内容作为 `reasoning_content` 返回，并要求使用工具时在后续请求中完整回传此前 assistant 的该字段。漏传会导致请求被拒绝。CodingAgent 会：

1. 在普通 JSON 和 SSE 流中分别解析该字段；
2. 将分片完整拼接进 assistant 历史；
3. 在工具 observation 后的下一轮请求中原样回传；
4. 不把内部推理打印到终端，也不单独写入审计事件。

协议依据见 [DeepSeek 思考模式指南](https://api-docs.deepseek.com/guides/thinking_mode/)、[工具调用指南](https://api-docs.deepseek.com/guides/tool_calls/) 和 [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)。

## 联通性验收

首次使用建议在一个临时、可信的项目目录中执行：

```powershell
forge --provider deepseek --max-steps 6 "列出当前目录文件，不要修改；然后总结项目结构"
```

没有 Key 时可以运行全部离线测试，但不能进行真实模型请求。若直接选择 DeepSeek 且未设置 Key，程序会在发起网络请求前给出明确的配置错误。不要把 Key 写入项目文件、聊天、任务文本、README 或录屏；只通过进程环境变量或受管密钥服务提供。
