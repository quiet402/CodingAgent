# Installation and deployment

CodingAgent is packaged as a standard Python command-line application. After one installation, open any project directory and run `forge`, similar to invoking `claude` or `codex`.

## Recommended: isolated global command with pipx

Install Python 3.10 or newer, then install pipx once:

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
```

Restart the terminal. From a local source checkout:

```powershell
pipx install D:\path\to\CodingAgent
```

After this project has a public Git repository:

```powershell
pipx install git+https://github.com/quiet402/CodingAgent.git
```

`pipx` creates an isolated environment and publishes the `forge` and `forge-agent` commands on the user PATH. Project dependencies cannot pollute CodingAgent, and CodingAgent does not pollute the project.

## Use it in any project

```powershell
cd D:\path\to\some-project
$env:DEEPSEEK_API_KEY="your-key"
forge
```

The current directory is the workspace by default. You can target another directory or start with an initial task:

```powershell
forge --workspace D:\path\to\some-project
forge -w D:\path\to\some-project "Inspect the failure and fix it"
forge -i -w D:\path\to\some-project "Fix the failure, then wait for follow-up tasks"
```

Use `--no-stream` if an OpenAI-compatible gateway does not implement SSE streaming.

## Provider configuration

Credentials must be supplied through process environment variables and are never loaded from project configuration files or installed with the package:

```powershell
$env:FORGE_API_KEY="your-key"
$env:FORGE_BASE_URL="https://provider.example/v1"
$env:FORGE_MODEL="provider-model-name"
```

`FORGE_BASE_URL` and `FORGE_MODEL` are optional when using the defaults. Never place a real key in a repository, README, command history, or video.

DeepSeek is the default provider; use its dedicated secret variable:

```powershell
$env:DEEPSEEK_API_KEY="your-key"
forge
```

The current preset uses `deepseek-v4-pro`. Use `--model deepseek-v4-flash` when latency matters more than maximum capability. See [DEEPSEEK.md](DEEPSEEK.md) for thinking controls and environment-variable equivalents.

## Upgrade or uninstall

For a local checkout that has changed:

```powershell
pipx reinstall forge-coding-agent
```

For a Git installation:

```powershell
pipx upgrade forge-coding-agent
```

Remove the command and its isolated environment:

```powershell
pipx uninstall forge-coding-agent
```

## Per-project or development installation

To keep the command only in this repository:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
forge --version
```

The checked-in `run.ps1` is an installation-free Windows fallback that directly uses `.venv`:

```powershell
.\run.ps1 -Workspace D:\path\to\some-project
```

## PATH troubleshooting

If installation succeeds but `forge` is not recognized:

1. Run `py -m pipx ensurepath`.
2. Close and reopen PowerShell.
3. Run `pipx list` and confirm `forge` appears under exposed applications.
4. As a fallback, run `python -m forge_agent` from an activated environment.

## Deployment boundary

The installed process still executes development commands on the local machine. Safe mode reduces accidental command risk but is not an OS sandbox. Use a container or disposable low-privilege account for untrusted repositories.
