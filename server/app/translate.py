"""
Prompt construction and caching for live caption translation.

Two things do most of the work here, and neither is the model:

  CONTEXT — a caption line is a fragment of a conversation. Translated alone,
  "he said that would break it" loses who "he" is and what "it" refers to. Sending
  the previous few segments fixes the referents. Persian in particular needs this:
  verb forms and pronoun dropping make an isolated fragment genuinely ambiguous.

  GLOSSARY — internal vocabulary must survive. Without it, "rule plan" becomes a
  literal rendering nobody on the team recognises, and "tenant" turns into a
  landlord. Naming the terms and telling the model to keep them in English is
  cheaper and more reliable than post-processing.
"""
from __future__ import annotations

from collections import OrderedDict

from .config import settings
from .providers import call

SYSTEM_TEMPLATE = """\
You translate live meeting captions from {source} into {target}.

The text comes from automatic speech recognition of a live meeting, so expect
fragments, false starts, filler words, and missing punctuation. The reader is a
developer following the meeting in real time.

Rules:
- Output ONLY the translation. No preamble, no notes, no quotation marks.
- Translate the CURRENT segment only. Earlier context is there to resolve pronouns
  and references — do not translate or repeat it.
- Keep it natural and spoken, not formal or literary. This is speech.
- If the segment is an incomplete fragment, translate it as a fragment. Do not
  invent an ending.
- Keep these technical terms in their original English form: {glossary}
- Keep product names, people's names, code identifiers, file names and numbers
  unchanged.
- If the segment is only filler ("um", "you know", "right"), return it as the
  closest natural equivalent rather than dropping it silently.
"""


def build_messages(text: str, context: list[str]) -> tuple[str, str]:
    system = SYSTEM_TEMPLATE.format(
        source=settings.source_lang_name,
        target=settings.target_lang_name,
        glossary=settings.glossary,
    )
    if context:
        recent = "\n".join(f"- {c}" for c in context[-settings.context_segments:])
        user = f"Earlier in the meeting (context only, do NOT translate):\n{recent}\n\nCURRENT SEGMENT:\n{text}"
    else:
        user = f"CURRENT SEGMENT:\n{text}"
    return system, user


class _Cache(OrderedDict):
    """
    Small LRU keyed on (text, target, provider).

    Meetings repeat themselves — greetings, names, "can you hear me", and every
    revision of a line that only gained a full stop. Caching those is free latency
    and free money.
    """

    def get_or_none(self, key):
        if key in self:
            self.move_to_end(key)
            return self[key]
        return None

    def put(self, key, value):
        self[key] = value
        self.move_to_end(key)
        while len(self) > settings.cache_size:
            self.popitem(last=False)


_cache = _Cache()
stats = {"calls": 0, "cache_hits": 0, "errors": 0, "total_ms": 0.0}


def translate(text: str, context: list[str] | None = None,
              provider: str | None = None) -> dict:
    text = (text or "").strip()
    if not text:
        return {"translation": "", "cached": True, "ms": 0.0, "provider": "none"}
    if len(text) > settings.max_chars:
        text = text[: settings.max_chars]

    key = (text, settings.target_lang, provider or settings.provider)
    hit = _cache.get_or_none(key)
    if hit is not None:
        stats["cache_hits"] += 1
        return {"translation": hit, "cached": True, "ms": 0.0,
                "provider": provider or settings.provider}

    system, user = build_messages(text, context or [])
    out, ms, used = call(system, user, provider)

    stats["calls"] += 1
    stats["total_ms"] += ms
    _cache.put(key, out)
    return {"translation": out, "cached": False, "ms": round(ms, 1), "provider": used}
