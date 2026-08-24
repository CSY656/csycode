"""Provider registry — maps protocol names to implementation classes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from csycode.config import ProviderConfig

if TYPE_CHECKING:
    from csycode.provider import BaseProvider

# Protocol name → Provider class
_providers: dict[str, type[BaseProvider]] = {}


def register_provider(protocol: str, cls: type[BaseProvider]) -> None:
    """Register a provider implementation for a protocol.

    Args:
        protocol: Protocol identifier (e.g. ``"anthropic"``, ``"openai"``).
        cls: A concrete ``BaseProvider`` subclass.

    Raises:
        ValueError: If the protocol is already registered with a different class.
    """
    if protocol in _providers and _providers[protocol] is not cls:
        raise ValueError(
            f"Protocol '{protocol}' is already registered by {_providers[protocol].__name__}. "
            f"Cannot re-register with {cls.__name__}."
        )
    _providers[protocol] = cls


def create_provider(config: ProviderConfig) -> BaseProvider:
    """Create a provider instance from configuration.

    Args:
        config: Validated provider configuration.

    Returns:
        An instance of the concrete provider class.

    Raises:
        ValueError: If no provider is registered for ``config.protocol``.
    """
    cls = _providers.get(config.protocol)
    if cls is None:
        available = ", ".join(sorted(_providers.keys())) if _providers else "(none)"
        raise ValueError(
            f"No provider registered for protocol '{config.protocol}'.\n"
            f"Available protocols: {available}"
        )
    return cls(config)


def list_protocols() -> list[str]:
    """Return a sorted list of registered protocol names."""
    return sorted(_providers.keys())
