# ForgeAgent

ForgeAgent is a compact coding agent implemented from first principles. It uses an OpenAI-compatible Chat Completions endpoint and native tool calling, but no agent framework, hosted code interpreter, or hosted file service.

Repository: https://github.com/quiet402/CodingAgent

## Why it is interesting

- The model/tool/observation loop is explicit in `forge_agent/agent.py`.
- Seventeen local tools cover inspection, glob/search, batch reads, transactional edits, copy/move, hash-confirmed deletion, read-only Git inspection, and commands.
- Existing files require an explicit overwrite flag; exact replacement is transactional.
- Interactive CLI asks for confirmation before file mutations and command execution; denied actions return to the model as observations.
- Enter `a` at a confirmation prompt, or pass `--yes`, to approve later high-risk actions for the current run.
- Safe mode runs commands without a shell and rejects shell operators, inline programs, path traversal, and unlisted executables.
- History compaction preserves complete assistant/tool-call groups.
- Interactive sessions retain context across user turns and stream model text as it arrives.
- Sessions are atomically persisted per workspace and can be listed or resumed after restart.
- Repeated-call, tool-error, and maximum-step guards make termination explainable.
- Every session produces a redacted, append-only JSONL trace.
- The runtime has no third-party Python dependency.

## Quick start

Requires Python 3.10 or newer.

For a Claude Code-like command available in every project, install with `pipx`:

```powershell
pipx install D:\path\to\forge-agent
cd C:\path\to\your-project
forge
```

The same installation can use the public Git repository. `forge` and `forge-agent` are equivalent commands. See [INSTALL.md](docs/INSTALL.md) for installation, upgrades, uninstalling, and PATH troubleshooting.

```powershell
pipx install git+https://github.com/quiet402/CodingAgent.git
```

```powershell
$env:FORGE_API_KEY="your-key"
$env:FORGE_MODEL="gpt-5.6"
python main.py -w C:\path\to\project
```

This opens a persistent session. Enter as many follow-up tasks as needed:

```text
forge> Inspect the code, fix the bug, and run tests
forge> Explain why the fix is correct and add one boundary test
forge> /quit
```

Use `/paste` for multiline tasks, `/history` for session status, `/sessions` to list saved conversations, `/resume <id>` to restore one, and `/new` to start another without deleting the old session. Model text streams by default; pass `--no-stream` for gateways without streaming support. To run exactly one task and exit, put it on the command line:

```powershell
python main.py -w C:\path\to\project "Fix the bug and run tests"
```

Resume the newest session after restarting:

```powershell
python main.py --resume latest
```

See [SESSIONS.md](docs/SESSIONS.md) for the storage format, commands, and privacy boundary.

On Windows, the checked-in launcher uses the project virtual environment directly, so activation is optional:

```powershell
.\run.ps1 -Workspace C:\path\to\project
```

Any service that implements the OpenAI-compatible `/v1/chat/completions` tool-calling format can be selected:

```powershell
$env:FORGE_BASE_URL="https://your-provider.example/v1"
$env:FORGE_MODEL="provider-model-name"
```

Credentials are read only from environment variables. Do not commit them.

### DeepSeek

DeepSeek is available as a built-in provider, so no base URL needs to be copied manually:

```powershell
$env:DEEPSEEK_API_KEY="your-key"
forge --provider deepseek
```

The preset uses `https://api.deepseek.com`, `deepseek-v4-pro`, thinking mode, and high reasoning effort. For lower latency, pass `--model deepseek-v4-flash`; to suppress thinking, pass `--thinking disabled`. ForgeAgent preserves DeepSeek's `reasoning_content` between tool-call rounds as required by its API, without printing that private reasoning to the terminal. See [the DeepSeek guide](docs/DEEPSEEK.md).

## Test

```powershell
python -m unittest discover -v
```

The test suite uses a scripted model client, so it validates the full agent loop without network access or an API key.

## Architecture

```text
task + system policy
        |
        v
conversation history ---> model client ---> assistant tool calls
        ^                                      |
        |                                      v
        +---------- tool observations <--- tool registry
                                               |
                     workspace boundary + command policy
```

See [the architecture note](docs/ARCHITECTURE.md) for design trade-offs, [the DeepSeek guide](docs/DEEPSEEK.md) for provider setup, [the video script](docs/VIDEO_SCRIPT.md) for a two-minute demonstration, and [the defense notes](docs/DEFENSE_QA.md) for likely interview questions.

## Security boundary

Safe mode substantially reduces accidental damage; it is not an operating-system sandbox. A permitted test program can still execute code from the target repository. Run ForgeAgent only on trusted projects and use a container or disposable account for stronger isolation. `--unsafe` is an explicit opt-out for controlled environments.

## License

MIT
