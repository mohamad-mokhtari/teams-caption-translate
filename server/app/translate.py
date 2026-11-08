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

import threading
from collections import OrderedDict

from . import languages
from .config import settings
from .providers import call

# What the live prompt asks for, and why -- measured against real meetings with
# tools/eval_translation.py (Nov 2026; 120 caption lines from three meetings, each
# speaker turn scored blind out of 10, long turns weighted more):
#
#   the old prompt, 3 bare English lines of context            5.7
#   this prompt, 12 lines with speakers + own earlier output   6.6 on gpt-4o-mini,
#                                                              7.6-7.8 on gpt-6-luna /
#                                                              gpt-5.6-luna
#   ... plus each speaker turn retranslated when it ends       9.1 (gpt-6-luna)
#
# Most of the damage was not translation at all. Teams' recogniser hears technical
# terms as ordinary words ("marriage request", "a in lease"), and a literal
# translator renders the mishearing -- "marriage proposal", "a lease" -- which
# reads as nonsense. Told to translate what the speaker plausibly SAID, with the
# conversation in front of it, a current model recovers "merge request" and "a list".
SYSTEM_TEMPLATE = """\
You translate live captions of a software team's meeting into {target}, one caption at
a time, for a developer who reads {target} far better than spoken English.{source_hint}

The captions come from automatic speech recognition (ASR). Expect:
- MISHEARD WORDS. Technical terms are often heard as ordinary words that sound alike.
  When a phrase makes no sense in a software meeting, translate what the speaker most
  plausibly SAID, judging from the conversation so far and the glossary -- not the
  literal mishearing. Do not do this when the literal text makes sense.
- Captions cut mid-sentence. A caption may continue the previous one or stop before
  its verb. Translate it so it reads naturally as a continuation of what came before.
  If the verb has not been said yet, end with "…" rather than inventing one.
- Repetitions, false starts and fillers ("I I", "so, so", "you know"): drop the noise
  and keep the meaning. A caption that is ONLY filler gets its closest natural
  equivalent, not silence.

Write natural, spoken {target} -- how a {target}-speaking developer would say it in a
meeting -- not a word-for-word rendering. Keep these in English: {glossary}, plus
product names, people's names, code identifiers, table names and numbers.
Use the same {target} wording as your earlier translations for the same terms.

If the caption is ALREADY in {target}, return it unchanged.

Output ONLY the translation of the CURRENT caption -- no preamble, notes or quotation
marks. Never translate or repeat the earlier ones; they are there so you understand it.
"""

TURN_TEMPLATE = """\
You translate live captions of a software team's meeting into {target}, one speaker's
turn at a time, for a developer who reads {target} far better than spoken English.{source_hint}

The CURRENT TURN is several consecutive captions from one speaker, produced by
automatic speech recognition (ASR) and cut wherever they paused. Expect:
- MISHEARD WORDS. Technical terms are often heard as ordinary words that sound alike.
  When a phrase makes no sense in a software meeting, translate what the speaker most
  plausibly SAID, judging from the conversation and the glossary. Do not do this when
  the literal text makes sense.
- Fragments, repetitions, false starts and fillers. Join the fragments into
  well-formed {target} sentences; drop the noise; keep every point the speaker made.

Do not add anything the speaker did not say, and do not leave out any item they
listed. Write natural, spoken {target} -- how a {target}-speaking developer would say
it -- not a word-for-word rendering. Keep these in English: {glossary}, plus product
names, people's names, code identifiers, table names and numbers. Use the same
{target} wording as your earlier translations for the same terms.

Output ONLY the translation of the CURRENT TURN -- no preamble, notes or quotation
marks. Never translate the earlier conversation.
"""


def _source_hint(source_name: str) -> str:
    return (f"\n\nThe captions appear to be in {source_name}, but trust the text in front "
            f"of you over that." if source_name else "")


def _conversation(history: list[dict], context: list[str]) -> tuple[list[str], list[str]]:
    """
    (conversation lines, the reader's own recent translations).

    `history` is what a current extension sends: [{speaker, text, translation}].
    `context` is the older shape -- bare English strings -- kept so an extension
    that has not been reloaded since the server was updated still works.

    The translations go in a block of their own. Interleaved under each line, as
    "[your Persian: ...]", small models copied the marker into their answer.
    """
    n = settings.context_segments
    if history:
        recent = history[-n:]
        convo = [f"{h.get('speaker') or 'Someone'}: {h.get('text', '')}".strip()
                 for h in recent if (h.get("text") or "").strip()]
        own = [(h.get("translation") or "").strip() for h in recent[-6:]]
        return convo, [t for t in own if t]
    return [c for c in context[-n:] if c and c.strip()], []


def build_messages(text: str, context: list[str], target_name: str,
                   source_name: str = "", history: list[dict] | None = None,
                   speaker: str = "") -> tuple[str, str]:
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
    system = SYSTEM_TEMPLATE.format(target=target_name, source_hint=_source_hint(source_name),
                                    glossary=settings.glossary)
    convo, own = _conversation(history or [], context or [])
    user = ""
    if convo:
        user += "Conversation so far (context only):\n" + "\n".join(convo) + "\n\n"
    if own:
        user += ("How you translated the last few captions (reuse the same terms):\n"
                 + "\n".join(own) + "\n\n")
    who = f" -- {speaker}" if speaker else ""
    user += f"CURRENT CAPTION{who}:\n{text}\n\nTranslation:"
    return system, user


def build_turn_messages(lines: list[str], speaker: str, target_name: str,
                        source_name: str = "", history: list[dict] | None = None,
                        prior_turns: list[str] | None = None) -> tuple[str, str]:
    system = TURN_TEMPLATE.format(target=target_name, source_hint=_source_hint(source_name),
                                  glossary=settings.glossary)
    convo, _ = _conversation(history or [], [])
    user = ""
    if convo:
        user += "Conversation before this turn (context only):\n" + "\n".join(convo) + "\n\n"
    prior = [t.strip() for t in (prior_turns or [])[-3:] if t and t.strip()]
    if prior:
        user += "Your recent translations (keep terms consistent):\n" + "\n".join(prior) + "\n\n"
    who = f" -- {speaker}" if speaker else ""
    user += f"CURRENT TURN{who}:\n" + " ".join(l.strip() for l in lines if l.strip())
    user += "\n\nTranslation:"
    return system, user


class _Cache(OrderedDict):
    """
    Small LRU keyed on (text, target, provider).

    Meetings repeat themselves — greetings, names, "can you hear me", and every
    revision of a line that only gained a full stop. Caching those is free latency
    and free money.

    Locked: requests run in parallel threads, and "is it there? then move it" is
    two steps another thread's eviction can fall between.
    """

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()

    def get_or_none(self, key):
        with self._lock:
            if key in self:
                self.move_to_end(key)
                return self[key]
            return None

    def put(self, key, value):
        with self._lock:
            self[key] = value
            self.move_to_end(key)
            while len(self) > settings.cache_size:
                self.popitem(last=False)


_cache = _Cache()
stats = {"calls": 0, "turn_calls": 0, "cache_hits": 0, "errors": 0, "total_ms": 0.0}
_stats_lock = threading.Lock()


def count(name: str, amount: float = 1) -> None:
    """Thread-safe `stats[name] += amount`; requests run in parallel threads."""
    with _stats_lock:
        stats[name] += amount


def translate(text: str, context: list[str] | None = None,
              provider: str | None = None, target: str | None = None,
              source: str | None = None, history: list[dict] | None = None,
              speaker: str = "") -> dict:
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
        count("cache_hits")
        return {"translation": hit, "cached": True, "ms": 0.0,
                "provider": provider or settings.provider, "passthrough": False}

    system, user = build_messages(text, context or [], target_name, source_name,
                                  history=history, speaker=speaker)
    out, ms, used = call(system, user, provider)

    count("calls")
    count("total_ms", ms)
    _cache.put(key, out)
    return {"translation": out, "cached": False, "ms": round(ms, 1),
            "provider": used, "passthrough": False}


def translate_turn(lines: list[str], speaker: str = "", history: list[dict] | None = None,
                   prior_turns: list[str] | None = None, provider: str | None = None,
                   target: str | None = None, source: str | None = None) -> dict:
    """
    One speaker's turn, retranslated as a whole once they stop.

    Line by line, the translator never sees a sentence whole: Teams cuts captions
    wherever the speaker paused, and a verb-final language like Persian cannot be
    written well from half a sentence. Given the whole turn, even a small model
    joins the fragments into sentences -- this is the single biggest quality step
    measured (7.6 -> 9.1 on the same model).

    Not cached: a turn is not going to recur word for word.
    """
    target_code = target or settings.target_lang
    target_name = languages.name_of(target_code, settings.target_lang_name)
    source_name = languages.name_of(source or "", "")

    lines = [l.strip() for l in (lines or []) if l and l.strip()]
    if not lines:
        return {"translation": "", "ms": 0.0, "provider": "none", "passthrough": False}
    if source and languages.get(source) is not None \
            and languages.get(source) is languages.get(target_code):
        return {"translation": " ".join(lines), "ms": 0.0, "provider": "none",
                "passthrough": True}

    # A turn is bounded by the extension, but never trust that blindly.
    budget, kept = settings.max_chars * 2, []
    for l in lines:
        if sum(len(k) for k in kept) + len(l) > budget:
            break
        kept.append(l)

    system, user = build_turn_messages(kept or lines[:1], speaker, target_name, source_name,
                                       history=history, prior_turns=prior_turns)
    out, ms, used = call(system, user, provider)
    count("turn_calls")
    return {"translation": out, "ms": round(ms, 1), "provider": used, "passthrough": False}


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
