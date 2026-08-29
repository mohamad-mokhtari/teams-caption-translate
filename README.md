# translate-app

Live translation of Teams meeting captions, for developers who read English better than
they hear it.

See [DESIGN.md](DESIGN.md) for the architecture and why it reads the DOM instead of
screenshotting the screen.

**Status: phase 0 PASSED** — captions confirmed readable from the Teams DOM
(2026-08-29). Two real constraints found, both handled:

| Constraint | Handling |
|---|---|
| Teams enforces **Trusted Types** CSP — any `innerHTML` assignment throws | All DOM built with `createElement` / `textContent` |
| The obvious container **concatenates every line**, inlining speaker names and re-emitting the whole history as one growing segment | Keep only the innermost text-holding elements, then regroup by parent into speaker + utterance |

Next: tune line detection against the real markup (use the **dump** button), then phase 1.

---

## Phase 0: does this even work?

Everything depends on one question: *can we read Teams live captions reliably?* If yes, the
rest is ordinary work. If no, the approach changes completely (capture audio, run ASR
ourselves), and it's better to know that in an afternoon than in a month.

### Install (Edge or Chrome)

Copy the `extension/` folder to the laptop you use for Teams, then:

1. `edge://extensions` (or `chrome://extensions`)
2. Turn on **Developer mode**
3. **Load unpacked** -> select `extension/`
4. Open a Teams meeting and turn on live captions

Two files only: `manifest.json` and `content.js`. Styles are inlined in the JS — a
separate stylesheet was one file too many to copy correctly, and its absence failed
silently.

After changing `content.js`: click **Reload** on the extension card, then **refresh
the Teams tab**. Content scripts only inject on page load.

### console-test.js

The same code, pasteable into DevTools with nothing installed. Keep it — it separates
"does capture work" from "does my extension package load", which saved several rounds
during phase 0.

**It is generated.** Run `./build.sh` after editing `extension/content.js`; never edit
it directly.


### What to report back

| Question | Why it matters |
|---|---|
| Did auto-detect find the captions, or did you need "pick area"? | Decides how fragile the selectors are |
| Are speaker names captured? | Multi-speaker meetings are much easier to follow with them |
| Segments per minute? | Sets the cost and rate budget for translation |
| Does the settled text read correctly, or is it fragmented? | Tunes the 700 ms settle window |
| Does the panel get in the way? | Layout for phase 1 |

Click **copy** at the end to get the whole transcript on the clipboard, and paste me a
minute of it.

---

## Privacy in phase 0

This makes **no network requests at all**. Nothing is stored — the transcript lives in the
page's memory and disappears when you close the tab. You can verify both in DevTools →
Network.

Cloud translation arrives in phase 1, with a local (Ollama) option and a visible indicator
when text is leaving the machine.

---

## Before you spend more time on this

Two free checks that could shrink or kill the project — worth doing first:

1. **Does your tenant already have Teams live translated captions** for the languages you
   need? If so, stop and use it.
2. **Do the developers follow plain English captions better than speech?** Many people read
   English far better than they hear it. If captions alone are enough, you don't need
   translation at all.

Both take ten minutes. Neither costs anything.

---

## Next: phase 1

A local FastAPI companion on `127.0.0.1` that the extension calls to translate each settled
segment. Keys stay in a `.env` rather than in browser storage on every laptop, provider
switching (Ollama / OpenAI / Anthropic) is Python we already know, and latency and cost are
measurable in one place.

Not built yet — phase 0 has to pass first.
