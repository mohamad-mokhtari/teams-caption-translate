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

from . import languages
from .config import settings
from .providers import call

SYSTEM_TEMPLATE = """\
You translate live meeting captions into {target}.{source_hint}

The text comes from automatic speech recognition of a live meeting, so expect
fragments, false starts, filler words, and missing punctuation. The reader is a
developer following the meeting in real time.

Rules:
- Output ONLY the translation. No preamble, no notes, no quotation marks.
- If the segment is ALREADY in {target}, return it unchanged. Do not paraphrase it
  and do not translate it into anything else.
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


def build_messages(text: str, context: list[str], target_name: str,
                   source_name: str = "") -> tuple[str, str]:
    """
    The source language is a *hint*, never a requirement.

    The reader chooses only their own language. Which language the meeting is
    being captioned in is the meeting's business, and it can change between
    meetings or be set to something nobody expected. Naming a source that turns
    out to be wrong is worse than naming none: the model tries to read Spanish as
    English and produces confident nonsense. So the hint is included only when the
    detector has actually seen the caption stream, and the model is told it may be
    wrong.
    """
    system = SYSTEM_TEMPLATE.format(
        target=target_name,
        source_hint=(f"\n\nThe captions appear to be in {source_name}, but trust the text "
                     f"in front of you over that." if source_name else ""),
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
              provider: str | None = None, target: str | None = None,
              source: str | None = None) -> dict:
    """`target` is a language code chosen by the reader; it falls back to the
    server default so an older extension keeps working."""
    target_code = target or settings.target_lang
    target_name = languages.name_of(target_code, settings.target_lang_name)
    source_name = languages.name_of(source or "", "")

    text = (text or "").strip()
    if not text:
        return {"translation": "", "cached": True, "ms": 0.0,
                "provider": "none", "passthrough": False}
    if len(text) > settings.max_chars:
        text = text[: settings.max_chars]

    # The reader already speaks the language the meeting is in: hand the line
    # straight back.
    #
    # The prompt does say "if the segment is already in {target}, return it
    # unchanged", and the model ignores it — asked to translate captions into
    # Spanish, given Spanish, gpt-4o-mini returns English. That is why this is a
    # branch and not a rule. It is also what we want regardless: a Spanish speaker
    # in a Spanish meeting should not be paying for, waiting on, and reading a
    # second copy of every line.
    if source and target_code and languages.get(source) is languages.get(target_code) \
            and languages.get(target_code) is not None:
        return {"translation": text, "cached": True, "ms": 0.0,
                "provider": "none", "passthrough": True}

    # The target is part of the key: one service now serves readers in different
    # languages, and without it the first person to hear a line would decide what
    # everyone else sees.
    key = (text, target_code, provider or settings.provider)
    hit = _cache.get_or_none(key)
    if hit is not None:
        stats["cache_hits"] += 1
        return {"translation": hit, "cached": True, "ms": 0.0,
                "provider": provider or settings.provider, "passthrough": False}

    system, user = build_messages(text, context or [], target_name, source_name)
    out, ms, used = call(system, user, provider)

    stats["calls"] += 1
    stats["total_ms"] += ms
    _cache.put(key, out)
    return {"translation": out, "cached": False, "ms": round(ms, 1),
            "provider": used, "passthrough": False}


# ---------------------------------------------------------------------------
# Per-speaker summary
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM = """\
You summarise one person's contributions to a live meeting, in {target}.

You are given everything that ONE speaker said, in order, transcribed automatically.
Expect fragments, false starts and recognition errors — read through them.

Write for a reader who was present but could not follow the language spoken in the
meeting. They need to know what this person actually contributed, quickly.

Structure:
1. Two or three sentences on their main point or position overall.
2. A short bullet list of the specific things they said that matter: decisions,
   commitments, questions they asked, problems they raised, numbers or dates.
3. If they asked something that was not obviously answered, note it under a final
   line beginning "Open:".

Rules:
- Write in {target}, whatever language the transcript itself is in.
- Only what they actually said. Do not infer, do not add advice, do not invent
  detail to fill gaps.
- Keep technical terms in English: {glossary}
- Keep names, numbers, dates and code identifiers unchanged.
- If the input is too short or too garbled to summarise, say exactly that in
  {target} rather than producing something plausible.
"""


def summarize_speaker(speaker: str, segments: list[str],
                      provider: str | None = None, target: str | None = None) -> dict:
    """
    Summarise everything one speaker said.

    Deliberately not cached: the transcript grows through the meeting, so the same
    speaker summarised twice is a different question with a different answer.
    """
    lines = [s.strip() for s in segments if s and s.strip()]
    if not lines:
        return {"summary": "", "segments": 0, "ms": 0.0,
                "provider": provider or settings.provider}

    system = SUMMARY_SYSTEM.format(
        target=languages.name_of(target or settings.target_lang, settings.target_lang_name),
        glossary=settings.glossary,
    )
    body = "\n".join(f"- {l}" for l in lines)
    user = f"Everything {speaker or 'this speaker'} said, in order:\n{body}"

    out, ms, used = call(system, user, provider)
    return {"summary": out, "segments": len(lines),
            "ms": round(ms, 1), "provider": used}


# ---------------------------------------------------------------------------
# What language are the captions in?
# ---------------------------------------------------------------------------

DETECT_SYSTEM = """\
You identify the language of a transcript.

You will be given a few lines from automatic speech recognition of a live meeting.
Reply with the BCP-47 language code and NOTHING else — no name, no punctuation, no
explanation. Examples of valid replies: en, es, fa, ja, zh, zh-TW, pt.

If the lines are too short or too garbled to tell, reply: und
"""


def detect_language(samples: list[str], provider: str | None = None) -> dict:
    """
    Work out what the meeting is being captioned in, once.

    The reader chooses only their own language, so this is the other half of the
    pair — and it is worth a dedicated call rather than inferring it per line. It
    decides two things: the source hint given to the translator, and whether the
    reader needs translating for at all. Someone whose language already matches the
    captions should not be paying for, waiting on, and reading a second copy of
    every line.
    """
    text = "\n".join(s.strip() for s in samples if s and s.strip())[: settings.max_chars]
    if len(text) < 12:
        # Too little to go on. "und" is an answer; a guess from four words is not.
        return {"lang": "und", "name": "", "confident": False, "ms": 0.0}

    out, ms, _ = call(DETECT_SYSTEM, text, provider)
    code = (out or "").strip().strip(".").split()[0] if (out or "").strip() else ""

    row = languages.get(code)
    if not row:
        return {"lang": "und", "name": "", "confident": False, "ms": round(ms, 1)}
    return {"lang": row["code"], "name": row["name"], "confident": True, "ms": round(ms, 1)}
