"""Ferro domain agent package.

Public symbols are loaded on demand so importing a lightweight feature, such
as the article store, does not bootstrap cognition, browsers or runtime state.
"""
from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "ContentArtifact": ("editorial.content_artifact", "ContentArtifact"),
    "ContentKind": ("editorial.content_artifact", "ContentKind"),
    "ContentSection": ("editorial.content_artifact", "ContentSection"),
    "ContentSourceRef": ("editorial.content_artifact", "ContentSourceRef"),
    "ContentAgent": ("content_service", "ContentAgent"),
    "TrafficLearner": ("learning", "TrafficLearner"),
    "AudienceProfile": ("models", "AudienceProfile"),
    "ContentObjective": ("models", "ContentObjective"),
    "ContentPackage": ("models", "ContentPackage"),
    "EmotionAngle": ("models", "EmotionAngle"),
    "PerformanceObservation": ("models", "PerformanceObservation"),
    "TietuDraft": ("models", "TietuDraft"),
    "TopicSignal": ("models", "TopicSignal"),
    "WeChatTrafficAgent": ("service", "WeChatTrafficAgent"),
}
__all__ = list(_EXPORTS)


def __getattr__(name: str):
    try:
        module, symbol = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(f".{module}", __name__), symbol)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
