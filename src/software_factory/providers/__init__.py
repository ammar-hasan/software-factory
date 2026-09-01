"""Model providers. Inference credentials live here and nowhere else."""

from software_factory.providers.anthropic import AnthropicProvider
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
from software_factory.providers.openai_compatible import OpenAICompatibleProvider
from software_factory.providers.registry import (
    Endpoint,
    ProviderSpec,
    Resolution,
    UnknownProviderError,
    endpoint_for,
    known_providers,
    resolve,
    spec_for,
)
from software_factory.providers.stub import (
    StubProvider,
    UnavailableProvider,
    calls,
    fails,
    filtered,
    says,
    silent,
    truncated,
)
from software_factory.providers.transport import (
    RetryingTransport,
    Transport,
    UrllibTransport,
    redact_headers,
)

__all__ = [
    "AnthropicProvider",
    "Completion",
    "Endpoint",
    "Message",
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderError",
    "ProviderSpec",
    "Resolution",
    "RetryingTransport",
    "Role",
    "StopReason",
    "StubProvider",
    "ToolCall",
    "Transport",
    "UnavailableProvider",
    "UnknownProviderError",
    "UrllibTransport",
    "Usage",
    "calls",
    "endpoint_for",
    "fails",
    "filtered",
    "known_providers",
    "redact_headers",
    "resolve",
    "says",
    "silent",
    "spec_for",
    "truncated",
]
