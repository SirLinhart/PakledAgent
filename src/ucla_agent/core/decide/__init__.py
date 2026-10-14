"""Asking a decision source (a local model, a native systemone server, Claude, the Jev service) to choose among
options or to write a short text, with a deadline and a fallback."""

from ucla_agent.core.decide.chain import ChainSource
from ucla_agent.core.decide.choice import (
    CIRCUIT_OPEN,
    ERROR,
    INVALID,
    NO_SOURCE,
    OUTCOMES,
    STALE_DISCARDED,
    TIMEOUT,
    USED,
    ChoiceRequest,
    ChoiceResult,
    Option,
)
from ucla_agent.core.decide.chooser import AsyncChooser
from ucla_agent.core.decide.claude import ClaudeSettings, ClaudeSource
from ucla_agent.core.decide.jev import JevFields, JevSource
from ucla_agent.core.decide.local import LocalModelSource
from ucla_agent.core.decide.settings import build_chain
from ucla_agent.core.decide.source import DecisionSource
from ucla_agent.core.decide.systemone import SystemoneSource
from ucla_agent.core.decide.text import TextRequest, TextResult

__all__ = [
    "AsyncChooser",
    "CIRCUIT_OPEN",
    "ERROR",
    "INVALID",
    "NO_SOURCE",
    "OUTCOMES",
    "STALE_DISCARDED",
    "TIMEOUT",
    "USED",
    "ChainSource",
    "ChoiceRequest",
    "ChoiceResult",
    "ClaudeSettings",
    "ClaudeSource",
    "DecisionSource",
    "JevFields",
    "JevSource",
    "LocalModelSource",
    "Option",
    "SystemoneSource",
    "TextRequest",
    "TextResult",
    "build_chain",
]
