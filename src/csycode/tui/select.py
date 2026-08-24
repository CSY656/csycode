"""Provider selection screen via OptionList."""

from __future__ import annotations

from textual.containers import Vertical


def build_selector(providers) -> Vertical:
    """Build a provider selection widget.

    Args:
        providers: List of ProviderConfig objects.

    Returns:
        A Vertical container with instructions and an OptionList.
    """
    # Currently, provider options are built inline in app.py's _populate_selector.
    # This function is available for future use.
    container = Vertical(id="selector")
    return container
