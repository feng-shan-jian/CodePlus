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
    raise NotImplementedError("Implementation pending")


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

