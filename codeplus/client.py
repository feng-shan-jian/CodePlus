from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from codeplus.config import ProviderConfig
from codeplus.conversation import ConversationManager
from codeplus.conversation_pairing import ensure_tool_pairing
from codeplus.serialization import (
    build_anthropic_messages,
    build_chat_completion_messages,
    build_openai_input,
)
from codeplus.tools.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)


# 限制自动拉取模型元数据的超时时间，防止慢响应或挂起的
# /v1/models 端点拖延启动。超时后降级为 None（即"未知"），
# 由下一层 context window 解析逻辑接管。
ANTHROPIC_MODEL_FETCH_TIMEOUT = 3.0


_EPHEMERAL = {"type": "ephemeral"}


def _mark_last_user_tail_for_cache(messages: list[dict[str, Any]]) -> None:
    raise NotImplementedError("Implementation pending")




def _mark_last_tool_for_cache(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not tools:
        return tools
    marked = list(tools)
    marked[-1] = {**tools[-1], "cache_control": _EPHEMERAL}
    return marked


class LLMError(Exception):
    pass


class AuthenticationError(LLMError):
    pass


class RateLimitError(LLMError):


    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class NetworkError(LLMError):
    pass


class LLMClient(ABC):
    @abstractmethod
    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        yield TextDelta("")

    def set_max_output_tokens(self, tokens: int) -> None:
        pass


def _supports_adaptive_thinking(model: str) -> bool:
    for family in ("claude-opus-4-", "claude-sonnet-4-"):
        if model.startswith(family):
            rest = model[len(family):]
            if rest and rest[0].isdigit() and int(rest[0]) >= 6:
                return True
    return False


class AnthropicClient(LLMClient):
    def __init__(self, config: ProviderConfig) -> None:
        self.model = config.model
        self.thinking = config.thinking
        self.max_output_tokens = config.get_max_output_tokens()
        api_key = config.resolve_api_key()
        if not api_key:
            raise AuthenticationError(
                "Anthropic API key not found. "
                "Set it in .codeplus/config.yaml or via ANTHROPIC_API_KEY env var."
            )
        self._client = AsyncAnthropic(api_key=api_key, base_url=config.base_url)

    def set_max_output_tokens(self, tokens: int) -> None:
        self.max_output_tokens = tokens

    async def fetch_model_context_window(self) -> int | None:
        raise NotImplementedError("Implementation pending")

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if False:
            yield None
        raise NotImplementedError("Implementation pending")


class OpenAIClient(LLMClient):
    def __init__(self, config: ProviderConfig) -> None:
        self.model = config.model
        self.max_output_tokens = config.get_max_output_tokens()
        api_key = config.resolve_api_key()
        if not api_key:
            raise AuthenticationError(
                "OpenAI API key not found. "
                "Set it in .codeplus/config.yaml or via OPENAI_API_KEY env var."
            )
        self._client = AsyncOpenAI(api_key=api_key, base_url=config.base_url)

    def set_max_output_tokens(self, tokens: int) -> None:
        self.max_output_tokens = tokens

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if False:
            yield None
        raise NotImplementedError("Implementation pending")


class OpenAICompatClient(LLMClient):
    """面向 OpenAI 兼容 provider 的客户端，使用 Chat Completions API。

    与面向较新的 Responses API（``/responses``）的 ``OpenAIClient`` 不同，
    本客户端使用受广泛支持的 Chat Completions 端点（``/chat/completions``），
    因此能兼容任何暴露 OpenAI 兼容接口的 provider（例如 vLLM、Ollama、
    Together、Azure OpenAI 等）。
    """

    def __init__(self, config: ProviderConfig) -> None:
        self.model = config.model
        self.max_output_tokens = config.get_max_output_tokens()
        api_key = config.resolve_api_key()
        if not api_key:
            raise AuthenticationError(
                "OpenAI-compatible API key not found. "
                "Set it in .codeplus/config.yaml or via OPENAI_API_KEY env var."
            )
        self._client = AsyncOpenAI(api_key=api_key, base_url=config.base_url)

    def set_max_output_tokens(self, tokens: int) -> None:
        self.max_output_tokens = tokens

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise NotImplementedError("Implementation pending")

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if False:
            yield None
        raise NotImplementedError("Implementation pending")


def create_client(config: ProviderConfig) -> LLMClient:
    if config.protocol == "anthropic":
        return AnthropicClient(config)
    elif config.protocol == "openai":
        return OpenAIClient(config)
    elif config.protocol == "openai-compat":
        return OpenAICompatClient(config)
    raise ValueError(f"Unknown protocol: {config.protocol}")


async def resolve_context_window(config: ProviderConfig) -> None:
    raise NotImplementedError("Implementation pending")
