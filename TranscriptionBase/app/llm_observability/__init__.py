"""LangSmith tracing + LangChain Hub prompt management + cross-provider router.

See plan/langchain_langsmith_integration.md.
"""

from .prompt_loader import (
    PromptBundle,
    get_prompt,
    get_prompt_sync,
    prewarm_all,
)
from .tracing import (
    traceable_llm,
    traceable_chain,
    current_prompt_name,
    prompt_name_context,
)
from .model_router import (
    CallOverrides,
    ModelUnavailableError,
    resolve_async_provider,
    resolve_sync_provider,
)
from .router_helpers import (
    routed_sync_call,
)
from .provider_registry import (
    init_registry,
    get_async_provider,
    get_sync_provider,
    registry_snapshot,
)

__all__ = [
    # Prompt loader
    "PromptBundle",
    "get_prompt",
    "get_prompt_sync",
    "prewarm_all",
    # Tracing
    "traceable_llm",
    "traceable_chain",
    "current_prompt_name",
    "prompt_name_context",
    # Router (Phase 7a)
    "CallOverrides",
    "ModelUnavailableError",
    "resolve_async_provider",
    "resolve_sync_provider",
    "routed_sync_call",
    # Registry (Phase 7a)
    "init_registry",
    "get_async_provider",
    "get_sync_provider",
    "registry_snapshot",
]
