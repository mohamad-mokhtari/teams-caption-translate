# translate-app — live meeting translation for Teams

**Problem.** Developers who read English adequately cannot follow spoken English at meeting
pace — accents, speed, overlapping speakers. They lose the thread, so they cannot answer even
when they know the answer. This costs the company access to good developers.

**Not solving:** perfect translation, or their speaking. Only *real-time listening
comprehension*.

---

## Decision: browser extension reading the caption DOM

Teams already runs ASR and puts the result in the page. We take that text directly.

```
Teams live captions (already in the DOM)
        │  MutationObserver — no OCR, no screenshots, ~0ms
        ▼
Segmenter: dedupe churn, wait for a finished sentence
        │
        ▼
Translator  ── local Ollama  |  OpenAI  |  Anthropic
        │
        ▼
Overlay panel beside the meeting
```

### Why not screenshot + OCR (the original idea)

| | Screenshot + OCR | DOM read |
|---|---|---|
| Added latency | 300–500 ms | ~0 ms |
| Accuracy | ASR errors + OCR errors | exact text |
| Speaker names | lost | available |
| Breaks when… | layout, theme, window moves, tiles overlap | Teams changes its markup |
| Caption churn | re-OCR the same evolving line ~20× | see each revision, cheap to dedupe |

Both break when Teams changes — but one breaks on a CSS selector we can re-point in a
minute, the other on pixel geometry.

### Why not capture audio and run Whisper

Considered, and it stays the fallback if Microsoft's markup proves unworkable or people
insist on the desktop client. It's strictly more work: audio loopback per OS, a model to
ship, and CPU-only ASR on a laptop is marginal for real time. Teams has already done the
ASR — using it is free.

---

## The two things that decide quality

**1. Segmentation, not model choice.** Live captions rewrite themselves as ASR revises:

```
"I think we should"
"I think we should deploy"
"I think we shouldn't deploy on Friday"
```

Translating every revision means flicker, wasted calls, and wrong meaning. Wait for a
segment to *settle* — no change for ~700 ms, or a sentence-ending punctuation, or a new
speaker — then translate once. This one decision matters more than which LLM you pick.

**2. Context and glossary.** Translating each sentence in isolation mangles pronouns and
domain terms. Carry the previous 2–3 segments as context, plus a small glossary
("tenant", "rule plan", "flagged" …) so internal vocabulary survives.

---

## Latency budget — the hard constraint

Target **under 2 seconds** from caption appearing to translation rendered. Above ~3s the
reader is answering a question two turns old, which defeats the purpose.

| Stage | Budget |
|---|---|
| DOM capture | ~0 ms |
| Settle wait | 500–700 ms *(deliberate; buys correctness)* |
| Translation | 300–800 ms |
| Render | ~10 ms |

That budget rules out a frontier model per segment. It wants a **small fast** model —
`gpt-4o-mini`-class hosted, or a small local model via Ollama.

---

## Providers — three, switchable

Same pattern as `main-app/app/llm.py`: one interface, provider behind it.

| | Latency | Cost | Data leaves the laptop |
|---|---|---|---|
| Local (Ollama) | depends on laptop | free | no |
| OpenAI | fast | ~$0.10–0.30 / meeting-hour | yes |
| Anthropic | fast | similar | yes |

Cloud is permitted, but local must work — some meetings will be too sensitive, and not
every developer will have a key.

---

## Where the LLM call happens: local companion, not the extension

A small FastAPI service on `127.0.0.1`, with the extension calling it.

- API keys live in a `.env`, not in browser storage on N laptops
- Provider switching is Python we already know how to write
- Ollama is reachable without fighting CORS
- Latency and cost are measurable in one place
- The extension stays small: capture DOM, render panel

Cost: developers run one extra process. They are developers.

---

## Privacy

Permitted by the company, but:

- Nothing persisted by default — translations live in memory and disappear on close
- Visible on/off toggle, and an indicator when text is being sent to a cloud provider
- Local mode for sensitive meetings
- Participants should be told; live transcription has consent implications in some
  jurisdictions and that is a people question, not a technical one

---

## What phase 0 established (2026-08-29)

Captions **are** readable from the Teams DOM. Confirmed markup:

```html
<div class="___18l92v8 ...">                        <!-- one caption line -->
  <div>
    <span class="fui-ChatMessageCompact__author">
      <span data-tid="author">Mohamad Mokhtari</span>
  <div>
    <span data-tid="closed-caption-text">Welcome to this channel.</span>
```

**Extraction keys off `data-tid` only.** The class names (`___18l92v8`, `fod5ikn`,
`fy9rknc`) are build-generated and change on any Teams release. `data-tid` values are
semantic and are what Microsoft's own tests hook into, so they are far more durable.

Line identity is a `WeakMap` keyed on the DOM node, not on text or index — Teams
mutates a line in place as the ASR revises it, so both of those change while the node
does not.

Three constraints found by running it, none of which a design document would have
surfaced:

| Constraint | Handling |
|---|---|
| Teams enforces a **Trusted Types** CSP; any `innerHTML` assignment throws | Build all DOM with `createElement` / `textContent` |
| The obvious container **concatenates every line** - speaker names inlined, whole history re-emitted as one growing segment | Keep only innermost text-holding elements; discard any that contain another candidate |
| The tool's own panel displays captions, so a text search **finds and attaches to its own output** | `isOurs()` guard on every search path, and `attach()` refuses outright |

Both follow-up issues are now fixed:

- **Speaker is paired with its utterance** — read from `[data-tid="author"]` within
  the same line element, so it never emits as a standalone segment.
- **Revisions replace instead of duplicating.** "What" settles, then "What country?"
  settles under the same key and is emitted with a `revised` flag, so phase 1 replaces
  the earlier translation rather than appending a superseded one.

## Phases

| Phase | Deliverable | Question it answers |
|---|---|---|
| **0** | Extension that captures captions and shows them raw — **no translation** | **Can we read Teams captions reliably?** |
| 1 | Local companion + one provider, translated overlay | Is end-to-end latency under 2s? |
| 2 | Segmentation tuning, context window, glossary | Is the translation actually followable? |
| 3 | Three providers, settings UI, per-user language | Is it usable by someone who isn't me? |
| 4 | Text-to-speech | Later. Only if reading proves insufficient |

**Phase 0 is the gate.** If Teams' markup can't be read reliably, the whole approach changes
and we fall back to audio capture. Find out first, in an afternoon.

---

## Before phase 1 — check the free options

1. Does your tenant already have Teams live translated captions? If yes for the languages
   you need, stop.
2. Do the developers follow **plain English captions** better than speech? Many people read
   English far better than they hear it. If English captions alone help enough, the problem
   is much smaller.

Neither costs anything to test, and either could shrink or remove this project.
