"""Conversation context as a *fallback* query for skill-retrieval.

Why a fallback and not a permanent query expansion. ``BM25Index.retrieve``
de-duplicates query tokens (``seen``), so a context token weighs exactly as
much as a question token and repeating a term cannot down-weight it. Blending
the history into every query therefore lets the conversation outvote the
question, and the *relative* relevance floor (``MIN_SCORE_RATIO``) then cuts
results against a top score the context raised. Both failure modes are silent.

So the context is consulted only when the question alone decided nothing:

* the normalized question carries fewer than ``CONTEXT_MIN_TOKENS`` indexable
  tokens -- "step 1 validated, go" has none that name a domain; or
* retrieval came back empty after the floor.

Whenever the plugin already answers, this module changes nothing at all. That
property is what makes the patch safe to run in production, and it is the one
the tests pin.

Shape of ``conversation_history``: unknown at writing time, and deliberately
not assumed. Hermes' own hook payloads have already proved three suppositions
wrong on this project, so each plausible shape is tried and the first call logs
which one actually matched -- read the log rather than the guess.

Configuration, all optional:

    SKILL_RETRIEVAL_CONTEXT=0            disable entirely (default: on)
    SKILL_RETRIEVAL_CONTEXT_TURNS=2      how many trailing turns to read
    SKILL_RETRIEVAL_CONTEXT_CHARS=300    per-turn truncation
    SKILL_RETRIEVAL_CONTEXT_MIN_TOKENS=3 below this, the question is too poor
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

__all__ = ["ENABLED", "needs_context", "augmented_query", "build_context"]


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r — using default %d", name, raw, default)
        return default
    if value < minimum:
        logger.warning("%s=%r must be >= %d — using default %d", name, raw, minimum, default)
        return default
    return value


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    logger.warning("Invalid %s=%r — using default %s", name, raw, default)
    return default


ENABLED = _env_bool("SKILL_RETRIEVAL_CONTEXT", True)
CONTEXT_TURNS = _env_int("SKILL_RETRIEVAL_CONTEXT_TURNS", 2)
CONTEXT_CHARS = _env_int("SKILL_RETRIEVAL_CONTEXT_CHARS", 300)
CONTEXT_MIN_TOKENS = _env_int("SKILL_RETRIEVAL_CONTEXT_MIN_TOKENS", 3)

# The keys the history may arrive under, and the keys a turn's text may carry.
# First present wins, and the relevé says which one it was.
_HISTORY_KEYS = ("conversation_history", "history", "messages", "turns")
_TEXT_KEYS = ("content", "text", "message", "response_text", "value")

_shape_logged = False


def _normalized(text: str) -> list[str]:
    """The plugin's own analysis pipeline, so the count matches the index.

    Falls back to a whitespace split if the private helper moves: a renamed
    internal must cost a coarser count, never a broken hook.
    """
    try:
        from bm25_retriever import _normalize_tokens
    except Exception:  # pragma: no cover - defensive
        return [t for t in str(text).split() if t]
    try:
        return _normalize_tokens(text)
    except Exception:  # pragma: no cover - defensive
        return [t for t in str(text).split() if t]


def _turn_text(entry) -> str:
    """The text of one turn, whatever shape it arrives in."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in _TEXT_KEYS:
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value
            # Multimodal turns carry a list of parts; keep the text ones.
            if isinstance(value, (list, tuple)):
                parts = [
                    part.get("text") for part in value
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                ]
                if parts:
                    return " ".join(parts)
        return ""
    for key in _TEXT_KEYS:  # an object rather than a mapping
        value = getattr(entry, key, None)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _log_shape_once(key: str | None, history) -> None:
    global _shape_logged
    if _shape_logged:
        return
    _shape_logged = True
    first = None
    if isinstance(history, (list, tuple)) and history:
        head = history[0]
        first = sorted(head) if isinstance(head, dict) else type(head).__name__
    logger.info(
        "conversation_history relevé: key=%r type=%s len=%s first=%r",
        key, type(history).__name__,
        len(history) if isinstance(history, (list, tuple)) else None, first,
    )


def build_context(hook_kwargs: dict, turns: int | None = None, chars: int | None = None) -> str:
    """The last turns of the conversation, the agent's replies included.

    The agent's replies are the point: they name the domain far more explicitly
    than a terse follow-up question does.
    """
    turns = CONTEXT_TURNS if turns is None else turns
    chars = CONTEXT_CHARS if chars is None else chars
    history = key = None
    for candidate in _HISTORY_KEYS:
        value = (hook_kwargs or {}).get(candidate)
        if isinstance(value, (list, tuple)) and value:
            history, key = value, candidate
            break
    _log_shape_once(key, history)
    if not history:
        return ""
    pieces = []
    for entry in list(history)[-max(1, turns):]:
        text = _turn_text(entry).strip()
        if text:
            pieces.append(text[:chars])
    return "\n".join(pieces)


def needs_context(user_message: str, results) -> bool:
    """True when the question alone decided nothing.

    Two cases, and no third: an empty result after the floor, or a question
    whose normalized form carries too few tokens to name anything.
    """
    if not ENABLED:
        return False
    if not results:
        return True
    return len(set(_normalized(user_message))) < CONTEXT_MIN_TOKENS


def augmented_query(user_message: str, hook_kwargs: dict) -> str:
    """The question followed by the context, or "" when there is no context.

    The question comes first and is never dropped: the context orients, it does
    not replace. An empty return means "nothing to retry with", so the caller
    keeps the result it already has.
    """
    if not ENABLED:
        return ""
    context = build_context(hook_kwargs)
    if not context:
        return ""
    return f"{user_message}\n{context}"
