"""Obscate for LangChain: reversible PII redaction around any LLM, chain or agent."""
from ._client import ObscateClient, ObscateError, RestoreContext
from .redactor import ObscateRedactor
from .runnable import ObscateRedaction, with_obscate
from .tools import ObscateDetectTool, ObscateRedactTool

__version__ = "0.1.1"
__all__ = ["ObscateClient", "ObscateError", "RestoreContext", "ObscateRedactor", "ObscateRedaction",
           "with_obscate", "ObscateMiddleware", "ObscateRedactTool", "ObscateDetectTool"]


def __getattr__(name):  # ObscateMiddleware needs the `langchain` package (agents), not just langchain-core
    if name == "ObscateMiddleware":
        from .middleware import ObscateMiddleware
        return ObscateMiddleware
    raise AttributeError(name)
