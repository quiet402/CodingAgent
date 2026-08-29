"""Command-line interface for ForgeAgent."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import __version__
from .agent import AgentRunner
from .config import AgentConfig
from .model import OpenAICompatibleClient
from .sessions import SessionError
from .tools import (
    CommandPolicy,
    ToolRegistry,
    Workspace,
    build_command_tool,
    build_filesystem_tools,
    build_git_tools,
)
from .ui import ConsoleUI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forge-agent",
        description="A small auditable coding agent with no agent framework.",
    )
    parser.add_argument("task", nargs="*", help="Programming task; prompts interactively when omitted")
    parser.add_argument("-w", "--workspace", default=".", help="Workspace root (default: current directory)")
    parser.add_argument(
        "--provider",
        choices=["openai", "deepseek", "custom"],
        help="API preset; otherwise FORGE_PROVIDER (default: openai)",
    )
    parser.add_argument("--model", help="Model name; otherwise provider default or FORGE_MODEL")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL")
    parser.add_argument(
        "--thinking",
        choices=["enabled", "disabled"],
        help="Enable or disable provider thinking mode",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=["low", "high", "max"],
        help="Reasoning effort for providers that support it",
    )
    parser.add_argument("--max-steps", type=int, help="Maximum model turns")
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="Resume a saved workspace session (or use 'latest')",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Keep accepting tasks after an initial command-line task",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Wait for complete model responses instead of streaming text",
    )
    parser.add_argument(
        "--unsafe",
        action="store_true",
        help="Disable command allowlist; workspace file boundary still applies",
    )
    parser.add_argument("--version", action="version", version=f"ForgeAgent {__version__}")
    return parser


REPL_HELP = """Session commands:
  /help       show this help
  /paste      enter a multiline task; finish with a line containing only .
  /history    show current session, message count, and storage paths
  /sessions   list saved sessions for this workspace
  /resume ID  resume by list number, unique ID prefix, or latest
  /new        start a new session; the current saved session is retained
  /quit       exit ForgeAgent
"""


def _read_multiline() -> str:
    print("Paste the task below. Finish with a line containing only '.'")
    lines: list[str] = []
    while True:
        try:
            line = input("... ")
        except EOFError:
            break
        if line == ".":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _print_sessions(runner: AgentRunner) -> None:
    sessions = runner.list_sessions()
    if not sessions:
        print("No saved sessions in this workspace. Send at least one task first.")
        return
    print("Saved sessions (newest first):")
    for number, item in enumerate(sessions, start=1):
        updated = item.updated_at.replace("T", " ")[:19]
        print(
            f"  [{number}] {item.session_id}  {updated}  {item.message_count} msg  "
            f"{item.model}  {item.task}"
        )
    print("Use /resume <number>, /resume <session-id>, or /resume latest.")


def run_repl(
    runner: AgentRunner,
    initial_task: str = "",
    *,
    continue_session: bool = False,
) -> int:
    print("Interactive session started. Type /help for commands; /quit to exit.\n")
    pending = initial_task
    while True:
        if pending:
            task = pending
            pending = ""
        else:
            try:
                task = input("forge> ").strip()
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:
                print("\nUse /quit to exit, or enter another task.")
                continue
        if not task:
            continue
        command = task.casefold()
        if command in {"/quit", "/exit"}:
            return 0
        if command == "/help":
            print(REPL_HELP)
            continue
        if command == "/paste":
            pending = _read_multiline()
            continue
        if command == "/history":
            count = runner.history.message_count if runner.history else 0
            history_path = Path(runner.session_path)
            saved = history_path.is_file()
            print(
                f"session={runner.audit.session_id}  messages={count}  "
                f"saved={'yes' if saved else 'no'}\n"
                f"history={runner.session_path}\n"
                f"audit={runner.audit.path}"
            )
            if not saved:
                print("This empty session has not been saved; send a task first.")
            continue
        if command == "/sessions":
            _print_sessions(runner)
            continue
        if command.startswith("/resume"):
            parts = task.split(maxsplit=1)
            if len(parts) != 2 or not parts[1].strip():
                _print_sessions(runner)
                continue
            try:
                loaded = runner.resume_session(parts[1].strip())
            except SessionError as exc:
                print(f"Cannot resume session: {exc}")
                continue
            continue_session = True
            print(
                f"Resumed {loaded.session_id}: "
                f"{loaded.history.message_count} messages, saved model={loaded.model}."
            )
            continue
        if command == "/new":
            runner.reset_session()
            continue_session = False
            print("Started a new conversation session.")
            continue
        try:
            runner.run(task, continue_session=continue_session)
            continue_session = True
        except KeyboardInterrupt:
            runner.ui.end_stream()
            print("\nCurrent turn interrupted. You can continue the same session or use /new.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    task = " ".join(args.task).strip()

    try:
        config = AgentConfig.from_env(
            Path(args.workspace),
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            thinking=args.thinking,
            reasoning_effort=args.reasoning_effort,
            max_steps=args.max_steps,
            safe_mode=not args.unsafe,
            stream=not args.no_stream,
        )
        workspace = Workspace(config.workspace)
    except (ValueError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    specs = build_filesystem_tools(workspace)
    specs.extend(build_git_tools(workspace))
    specs.append(
        build_command_tool(
            workspace,
            CommandPolicy(safe_mode=config.safe_mode),
            config.command_timeout,
        )
    )
    registry = ToolRegistry(specs, config.tool_output_chars)
    client = OpenAICompatibleClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        retries=config.api_retries,
        thinking=config.thinking,
        reasoning_effort=config.reasoning_effort,
    )
    ui = ConsoleUI()
    ui.banner(
        config.provider,
        config.model,
        str(config.workspace),
        config.safe_mode,
        config.stream,
    )
    runner = AgentRunner(client, registry, config, ui=ui)
    resumed = False
    if args.resume:
        try:
            loaded = runner.resume_session(args.resume)
        except SessionError as exc:
            print(f"Cannot resume session: {exc}", file=sys.stderr)
            return 2
        resumed = True
        print(
            f"Resumed {loaded.session_id}: "
            f"{loaded.history.message_count} messages, saved model={loaded.model}."
        )
    if args.interactive or not task:
        return run_repl(runner, task, continue_session=resumed)
    try:
        result = runner.run(task, continue_session=resumed)
    except KeyboardInterrupt:
        print("\nInterrupted by user. Partial changes were left in the workspace.", file=sys.stderr)
        return 130
    print(f"audit={result.audit_path}")
    return 0 if result.stop_reason == "completed" else 1
