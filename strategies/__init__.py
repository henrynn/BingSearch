"""
strategies package – exports all three search strategy classes and build_strategy().
"""

from strategies.base import FoundryBaseStrategy, RequestResult, Strategy
from strategies.foundry_bing_grounding import FoundryBingGroundingStrategy
from strategies.foundry_web_search import FoundryWebSearchStrategy
from strategies.webiq import WebIQStrategy


def build_strategy(name: str) -> Strategy:
    """
    Factory that returns a strategy instance by name.

    Supported names: "webiq", "foundry_bing_grounding", "foundry_web_search"
    """
    if name == "webiq":
        return WebIQStrategy()
    if name == "foundry_bing_grounding":
        return FoundryBingGroundingStrategy()
    if name == "foundry_web_search":
        return FoundryWebSearchStrategy()
    raise ValueError(
        f"Unknown strategy: '{name}'. "
        "Choose from: webiq, foundry_bing_grounding, foundry_web_search"
    )


__all__ = [
    "RequestResult",
    "Strategy",
    "FoundryBaseStrategy",
    "WebIQStrategy",
    "FoundryBingGroundingStrategy",
    "FoundryWebSearchStrategy",
    "build_strategy",
]
