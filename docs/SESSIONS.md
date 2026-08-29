# 会话保存与恢复

ForgeAgent 0.4.0 会在每个工作区的 `.forge/sessions` 目录自动保存完整会话。关闭终端、退出 PyCharm或重启电脑后，都可以恢复并继续提问。

## 交互命令

```text
/history          查看当前会话 ID、消息数、历史文件和审计文件
/sessions         列出当前工作区最近保存的会话
/resume           显示可恢复的会话列表
/resume 1         按 `/sessions` 中的序号恢复
/resume latest    恢复最近一次会话
/resume <ID>      按完整 ID 或唯一前缀恢复
/new              开始新会话；旧会话仍然保留
/quit             退出程序
```

典型操作：

```text
forge> /sessions
forge> /resume 1
forge> 继续刚才的工作并运行测试
```

也可以在启动时直接恢复：

```powershell
.\run.ps1
# 进入 forge> 后输入 /resume latest
```

或者使用 Python 入口：

```powershell
.\.venv\Scripts\python.exe main.py --resume latest
```

## 保存内容

会话 JSON 保存系统消息、用户消息、assistant 回复、工具调用、工具结果和 DeepSeek 的 `reasoning_content`。它不保存 `DEEPSEEK_API_KEY`、`FORGE_API_KEY`、Base URL 或其它配置凭据。保存使用同目录临时文件加原子替换，程序中断不会留下半个 JSON 文件。

恢复时会验证：

- 会话 ID 只能使用规定格式，不能包含路径字符；
- JSON 的格式版本必须受支持；
- 文件内部 ID 必须与文件名一致；
- 保存的工作区必须与当前工作区一致；
- 保存时使用的 provider 必须与当前 provider 一致。

模型名称允许改变，便于在同一 provider 下从 Pro 切换到 Flash；恢复提示会显示保存时使用的模型。

## 隐私边界

`.forge/` 已在 `.gitignore` 中，不会正常进入 Git。但会话中可能包含源码片段、文件内容和工具输出，所以不要主动上传 `.forge`，也不要在任务文本里粘贴密钥。审计日志会做基础脱敏；可恢复会话为了保证协议完整性保存原始消息，因此应把 `.forge/sessions` 视为本机私有数据。

早于 0.4.0 创建的 JSONL 审计日志不是完整历史快照，不能可靠恢复；升级后的新会话会自动保存。
