from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from codeplus.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from codeplus.agent import Agent
    from codeplus.agents.loader import AgentLoader
    from codeplus.agents.task_manager import TaskManager
    from codeplus.agents.trace import TraceManager
    from codeplus.client import LLMClient

log = logging.getLogger(__name__)


class AgentToolParams(BaseModel):
    prompt: str
    description: str
    subagent_type: str | None = None
    model: str | None = None
    run_in_background: bool = False
    name: str | None = None
    isolation: str | None = None
    plan_mode_required: bool = Field(
        default=False,
        description=(
            "Only meaningful together with team_name. When true, the teammate starts in "
            "plan mode: it can read and investigate but cannot modify anything until it "
            "submits a plan and you approve it via SendMessage with "
            "message_type='plan_approval_response'. Use it for risky or ambiguous tasks "
            "where a wrong direction would cost a lot of rework."
        ),
    )
    team_name: str | None = Field(
        default=None,
        description=(
            "REQUIRED when creating team members. Spawns the agent as a long-running "
            "teammate under this team (created via TeamCreate). Unlike regular sub-agents, "
            "team members run in their own terminal, persist after the lead returns, and "
            "communicate with each other via SendMessage. Without team_name the agent "
            "runs as a one-shot sub-agent that blocks and returns inline."
        ),
    )


PERMISSION_MODE_MAP = {
    "default": "DEFAULT",
    "acceptEdits": "ACCEPT_EDITS",
    "bypassPermissions": "BYPASS",
}


FORK_QUERY_SOURCE = "agent:builtin:fork"

# 省略 subagent_type 且 fork 被关掉时，回退到这个通用 agent
GENERAL_PURPOSE_AGENT_TYPE = "general-purpose"

TEAMMATE_ADDENDUM = (
    "\n\nIMPORTANT: You are running as an agent in a team.\n"
    "Just writing a response in text is not visible to others\n"
    "on your team - you MUST use the SendMessage tool.\n"
    "The user interacts primarily with the team lead.\n"
    "Your work is coordinated through the task system\n"
    "and teammate messaging.\n\n"
    "You are working in an isolated Git worktree. "
    "All file paths you use MUST be relative to your current working directory. "
    "Do NOT use absolute paths from the original project — they are outside your sandbox and will be rejected."
)


class AgentTool(Tool):
    name = "Agent"
    description = (
        "Launch a sub-agent to handle a task in an isolated context. "
        "Use subagent_type to select a predefined agent type (e.g. Explore, Plan, general-purpose), "
        "or leave it empty to fork the current conversation. "
        "Use team_name to spawn a teammate in an existing team."
    )
    params_model = AgentToolParams
    category = "command"
    is_concurrency_safe = False


    def __init__(
        self,
        agent_loader: AgentLoader,
        task_manager: TaskManager,
        trace_manager: TraceManager,
        parent_agent: Agent,
        enable_fork: bool = False,
        provider_config: Any = None,
        worktree_manager: Any = None,
        team_manager: Any = None,
    ) -> None:
        self._agent_loader = agent_loader
        self._task_manager = task_manager
        self._trace_manager = trace_manager
        self._parent_agent = parent_agent
        self._enable_fork = enable_fork
        self._provider_config = provider_config
        self._worktree_manager = worktree_manager
        self._team_manager = team_manager
        self.query_source: str = ""

    def _inherited_rule_engine(self) -> Any:
        """子 Agent 沿用父 Agent 的规则引擎：子 Agent 只换权限模式，
        父级配置的 allow/deny/ask 规则同样约束它，不能靠派子 Agent 绕开。
        父级没有权限检查器时退化为空规则集。
        """
        from codeplus.permissions import RuleEngine

        parent_checker = getattr(self._parent_agent, "permission_checker", None)
        return parent_checker.rule_engine if parent_checker else RuleEngine()

    async def execute(self, params: BaseModel) -> ToolResult:
        raise NotImplementedError("Implementation pending")

    async def _execute_as_teammate(self, p: AgentToolParams) -> ToolResult:
        raise NotImplementedError("Implementation pending")


    def _spawn_pane_teammate(
        self, p: Any, team: Any, member: Any, backend: Any, wt: Any,
        agent_id: str, teammate_name: str,
    ) -> ToolResult:
        raise NotImplementedError("Implementation pending")


    def _select_llm(
        self,
        params: AgentToolParams,
        definition: AgentDef,
    ) -> LLMClient:
        from codeplus.agents.parser import AgentDef

        model_override = params.model or (
            definition.model if definition.model != "inherit" else None
        )

        if model_override and model_override != "inherit":
            client = self._create_client_for_model(model_override)
            if client is not None:
                return client

        return self._parent_agent.client


    async def _execute_with_worktree(self, p: AgentToolParams) -> ToolResult:
        raise NotImplementedError("Implementation pending")


    def _create_client_for_model(self, model_alias: str) -> LLMClient | None:
        raise NotImplementedError("Implementation pending")
