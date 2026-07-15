from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from codeplus import crashlog
from codeplus.config import ConfigError, load_config
from codeplus.hooks import HookConfigError, HookEngine, load_hooks
from codeplus.permissions import PermissionMode


def main() -> None:
    # 队友 worker 模式：由 tmux/iTerm2 窗格用 `-m codeplus --teammate ...` 拉起。
    # 必须在 argparse 之前拦截，走独立的 worker 分支而不是正常 TUI。
    teammate = _parse_teammate_flags(sys.argv[1:])
    if teammate is not None:
        asyncio.run(_run_teammate(*teammate))
        return

    # 先确保 .codeplus/ 目录存在，否则下面写 debug.log 会因目录不存在而崩溃
    Path(".codeplus").mkdir(parents=True, exist_ok=True)
    # 追加写：排查异常退出要看的往往是上一次运行的日志，覆盖会把现场冲掉
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        filename=".codeplus/debug.log",
        filemode="a",
    )
    crashlog.install()

    parser = argparse.ArgumentParser(prog="codeplus", description="CodePlus AI coding assistant")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in PermissionMode],
        default=None,
        help="Permission mode (overrides config.yaml)",
    )
    parser.add_argument(
        "-p",
        metavar="PROMPT",
        default=None,
        help="Run non-interactively: execute the prompt and print the result to stdout",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "stream-json"],
        default="text",
        help="Output format for -p mode: 'text' (default) prints final text, 'stream-json' emits NDJSON events",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        default=False,
        help="Start in remote mode: WebSocket server on 0.0.0.0:18888 with browser UI",
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    mode_str = args.mode if args.mode else config.permission_mode
    permission_mode = PermissionMode(mode_str)

    try:
        hooks = load_hooks(config.raw_hooks)
    except HookConfigError as e:
        print(f"Hook config error: {e}", file=sys.stderr)
        sys.exit(1)

    hook_engine = HookEngine(hooks) if hooks else None

    if args.p is not None:
        output_format = getattr(args, "output_format", "text")
        asyncio.run(_run_prompt(config, permission_mode, hook_engine, args.p, output_format))
        return

    # Remote 模式：启动 WebSocket 服务器，浏览器访问 http://localhost:18888
    if args.remote:
        from codeplus.remote import RemoteServer

        server = RemoteServer(
            providers=config.providers,
            mcp_servers=config.mcp_servers,
            hook_engine=hook_engine,
            config=config,
        )
        asyncio.run(server.run())
        return

    from codeplus.app import CodePlusApp
    from codeplus.driver import NoAltScreenDriver

    app = CodePlusApp(
        providers=config.providers,
        permission_mode=permission_mode,
        mcp_servers=config.mcp_servers,
        hook_engine=hook_engine,
        enable_fork=config.enable_fork,
        enable_verification_agent=config.enable_verification_agent,
        worktree_config=config.worktree,
        teammate_mode=config.teammate_mode,
        enable_coordinator_mode=config.enable_coordinator_mode,
        driver_class=NoAltScreenDriver,
        sandbox_config=config.sandbox,
    )
    # TUI 内部的异常由 App._handle_exception 落盘，这里兜住的是框架之外的部分：
    # 启动、事件循环收尾，以及 Textual 自身抛出的异常
    try:
        app.run()
    except BaseException as e:
        crashlog.record_exception("app.run", e)
        raise


async def _run_prompt(config, permission_mode, hook_engine, prompt: str, output_format: str = "text") -> None:
    raise NotImplementedError("Implementation pending")


def _parse_teammate_flags(args: list[str]) -> tuple[str, str] | None:
    raise NotImplementedError("Implementation pending")


async def _build_teammate_registry(
    work_dir: str,
    protocol: str,
    team_manager: "TeamManager",
    team_name: str,
    agent_name: str,
    mcp_servers: list,
    base_url: str = "",
    context_window: int = 0,
):
    raise NotImplementedError("Implementation pending")


async def _run_teammate(team_name: str, agent_name: str) -> None:
    raise NotImplementedError("Implementation pending")


if __name__ == "__main__":
    main()

