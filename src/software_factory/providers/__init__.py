"""Model providers. Inference credentials live here and nowhere else."""

from software_factory.providers.base import (
    Completion,
    Message,
    Provider,
    ProviderError,
    Role,
    StopReason,
    ToolCall,
    Usage,
)
from software_factory.providers.stub import (
    StubProvider,
    UnavailableProvider,
    calls,
    fails,
    says,
)

__all__ = [
    "Completion",
    "Message",
    "Provider",
    "ProviderError",
    "Role",
    "StopReason",
    "StubProvider",
    "ToolCall",
    "UnavailableProvider",
    "Usage",
    "calls",
    "fails",
    "says",
]
