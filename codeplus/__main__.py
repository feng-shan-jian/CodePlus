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
    from codeplus.agent import (
        Agent,
        CompactNotification,
        ErrorEvent,
        LoopComplete,
        PermissionRequest,
        PermissionResponse,
        RetryEvent,
        StreamText,
        ThinkingText,
        ToolResultEvent,
        ToolUseEvent,
        TurnComplete,
        UsageEvent,
    )
    from codeplus.client import create_client, resolve_context_window
    from codeplus.conversation import ConversationManager
    from codeplus.memory.instructions import load_instructions
    from codeplus.permissions import (
        DangerousCommandDetector,
        PathSandbox,
        PermissionChecker,
        RuleEngine,
    )
    from codeplus.tools import create_default_registry
    from codeplus.agents.loader import AgentLoader
    from codeplus.agents.task_manager import TaskManager
    from codeplus.agents.trace import TraceManager
    from codeplus.tools.agent_tool import AgentTool
    from codeplus.tools.impl.tool_search import ToolSearchTool
    from codeplus.tools.mcp_call import McpCallTool
    from codeplus.teams.manager import TeamManager
    from codeplus.teams.models import BackendType
    from codeplus.tools.team_create import TeamCreateTool
    from codeplus.tools.team_delete import TeamDeleteTool
    from codeplus.worktree import WorktreeManager
    from codeplus.config import WorktreeConfig

    is_json = output_format == "stream-json"

    def emit_json(obj: dict) -> None:
        """输出一行 NDJSON 到 stdout"""
        print(json.dumps(obj, ensure_ascii=False), flush=True)

    provider = config.providers[0]
    client = create_client(provider)
    # 第 2 层：尽力从 provider 自动拉取模型的 context window（缓存在 provider 上）。
    # 不会抛异常或阻塞启动；失败则退化到映射表。
    await resolve_context_window(provider)
    work_dir = os.getcwd()
    home = Path.home()

    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(work_dir),
        rule_engine=RuleEngine(
            user_rules_path=home / ".codeplus" / "permissions.yaml",
            project_rules_path=Path(work_dir) / ".codeplus" / "permissions.yaml",
            local_rules_path=Path(work_dir) / ".codeplus" / "permissions.local.yaml",
        ),
        mode=permission_mode,
    )

    instructions = load_instructions(work_dir)
    registry = create_default_registry()
    registry.register(ToolSearchTool(registry, protocol=provider.protocol))
    registry.register(McpCallTool(registry))

    agent = Agent(
        client=client,
        registry=registry,
        protocol=provider.protocol,
        work_dir=work_dir,
        permission_checker=checker,
        context_window=provider.get_context_window(),
        instructions_content=instructions,
        hook_engine=hook_engine,
    )

    wt_cfg = config.worktree or WorktreeConfig()
    wt_manager = WorktreeManager(
        repo_root=work_dir,
        symlink_directories=wt_cfg.symlink_directories,
    )
    trace_manager = TraceManager()
    task_manager = TaskManager()
    agent_loader = AgentLoader(work_dir, enable_verification=config.enable_verification_agent)
    agent_loader.load_all()
    team_manager = TeamManager(worktree_manager=wt_manager, trace_manager=trace_manager)

    agent_tool = AgentTool(
        agent_loader=agent_loader,
        task_manager=task_manager,
        trace_manager=trace_manager,
        parent_agent=agent,
        enable_fork=config.enable_fork,
        provider_config=provider,
        worktree_manager=wt_manager,
        team_manager=team_manager,
    )
    registry.register(agent_tool)
    registry.register(TeamCreateTool(
        team_manager=team_manager,
        parent_agent=agent,
        teammate_mode="in-process",
        is_interactive=False,
        enable_coordinator_mode=config.enable_coordinator_mode,
    ))
    registry.register(TeamDeleteTool(team_manager=team_manager, parent_agent=agent))

    from codeplus.tools.send_message import SendMessageTool
    from codeplus.tools.synthetic_output import SyntheticOutputTool
    from codeplus.tools.task_stop import TaskStopTool

    registry.register(SyntheticOutputTool())
    registry.register(TaskStopTool(team_manager=team_manager))
    # Lead 给队员派活、续写都走这个工具，团队要等 TeamCreate 才存在，
    # 所以这里不绑定团队，发信时再取当前团队
    registry.register(SendMessageTool(team_manager=team_manager))

    # 连 MCP。放在所有内建工具注册完之后：MCP 工具的加载模式要按 schema 总量
    # 跟上下文窗口比，得等工具都在位才算得准。
    mcp_manager = None
    if config.mcp_servers:
        from codeplus.mcp import MCPManager
        from codeplus.mcp.loading_strategy import decide_and_apply

        mcp_manager = MCPManager()
        mcp_manager.load_configs(config.mcp_servers)
        connect_result = await mcp_manager.register_all_tools(registry)
        for err in connect_result.errors:
            print(f"MCP warning: {err}", file=sys.stderr)
        decide_and_apply(
            registry,
            base_url=provider.base_url,
            context_window=provider.get_context_window(),
        )

    # coordinator 模式由配置决定，开了就从第一轮起收窄工具集
    if config.enable_coordinator_mode:
        from codeplus.agents.tool_filter import apply_coordinator_filter

        agent.enable_coordinator_mode = True
        agent.registry = apply_coordinator_filter(agent.registry)

    def drain_notifications() -> list[str]:
        notes: list[str] = []
        for t in task_manager.poll_completed():
            notes.append(
                f"<task-notification>\n<task_id>{t.id}</task_id>\n"
                f"<status>{t.status}</status>\n<result>{t.result}</result>\n"
                f"</task-notification>"
            )
        notes.extend(team_manager.drain_lead_mailbox())
        return notes

    def drain_mailbox_only() -> list[str]:
        return team_manager.drain_lead_mailbox()

    agent.notification_fn = drain_mailbox_only

    # 使用事件驱动的 agent.run()，支持 text 和 stream-json 两种输出格式
    conv = ConversationManager()
    conv.add_user_message(prompt)

    start = time.monotonic()
    text_buf = ""
    total_input = 0
    total_output = 0
    tool_calls: list[dict] = []

    async for event in agent.run(conv):
        if isinstance(event, StreamText):
            text_buf += event.text
            if is_json:
                emit_json({"type": "assistant", "text": event.text})

        elif isinstance(event, ThinkingText):
            if is_json:
                emit_json({"type": "thinking", "text": event.text})

        elif isinstance(event, ToolUseEvent):
            tool_calls.append({"name": event.tool_name, "is_error": False})
            if is_json:
                emit_json({
                    "type": "tool_use",
                    "tool_name": event.tool_name,
                    "tool_id": event.tool_id,
                    "args": event.arguments,
                })

        elif isinstance(event, ToolResultEvent):
            # 回填最后一个同名 tool_call 的 is_error
            if tool_calls:
                tool_calls[-1]["is_error"] = event.is_error
            if is_json:
                emit_json({
                    "type": "tool_result",
                    "tool_name": event.tool_name,
                    "tool_id": event.tool_id,
                    "output": event.output,
                    "is_error": event.is_error,
                    "elapsed": round(event.elapsed, 3),
                })

        elif isinstance(event, UsageEvent):
            total_input = event.input_tokens
            total_output = event.output_tokens
            if is_json:
                emit_json({
                    "type": "usage",
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                })

        elif isinstance(event, TurnComplete):
            if is_json:
                emit_json({"type": "turn_complete", "turn": event.turn})

        elif isinstance(event, LoopComplete):
            # 最终结果：stream-json 输出 result 行，text 模式直接打印文本
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if is_json:
                emit_json({
                    "type": "result",
                    "result": text_buf,
                    "duration_ms": elapsed_ms,
                    "num_turns": event.total_turns,
                    "tool_calls": tool_calls,
                    "usage": {
                        "input_tokens": total_input,
                        "output_tokens": total_output,
                    },
                    "stop_reason": "end_turn",
                })
            else:
                print(text_buf, end="", flush=True)
            break

        elif isinstance(event, ErrorEvent):
            if is_json:
                emit_json({"type": "error", "message": event.message})
            else:
                print(f"Error: {event.message}", file=sys.stderr, flush=True)

        elif isinstance(event, CompactNotification):
            if is_json:
                emit_json({"type": "compact", "message": event.message})

        elif isinstance(event, RetryEvent):
            if is_json:
                emit_json({"type": "retry", "reason": event.reason})

        elif isinstance(event, PermissionRequest):
            # -p 非交互模式：自动批准所有权限请求
            event.future.set_result(PermissionResponse.ALLOW)

    # 如果有 team 在运行，轮询等待 teammate 完成
    if not team_manager._teams:
        return

    for i in range(90):
        await asyncio.sleep(2)
        running = {k: not t.done() for k, t in task_manager._async_tasks.items()}
        completed_ids = [t.id for t in task_manager._tasks.values() if t.status != "running"]
        print(f"[poll {i}] running={running} completed={completed_ids} teams={list(team_manager._teams.keys())} queue_size={task_manager._notify_queue.qsize()}", file=sys.stderr, flush=True)
        notes = drain_notifications()
        if not notes:
            has_running = any(v for v in running.values())
            if not has_running:
                print(f"[poll {i}] no running tasks, breaking", file=sys.stderr, flush=True)
                break
            continue
        for note in notes:
            conv.add_system_reminder(note)
        # 后续 team 轮询仍用 run_to_completion，避免重复事件循环
        last_result = await agent.run_to_completion(
            "Teammate notifications received. Process them and continue.", conv
        )
        if is_json:
            emit_json({"type": "assistant", "text": last_result})
        else:
            print(last_result, flush=True)

    if mcp_manager is not None:
        # 多个 stdio 服务器同时收尾时，底层的 anyio cancel scope 会互相打断并抛
        # CancelledError。结果已经输出完了，这里不该因为收尾失败而带崩整个命令。
        try:
            await mcp_manager.shutdown()
        except (Exception, asyncio.CancelledError):
            pass


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

