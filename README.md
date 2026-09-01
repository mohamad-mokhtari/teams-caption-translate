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

### Setting it up from zero

On your own laptop, once:

```bash
git clone https://github.com/mohamad-mokhtari/teams-caption-translate.git
cd teams-caption-translate

cp server/.env.example server/.env
#  edit it:  OPENAI_API_KEY=sk-...
#            TARGET_LANG=<your language: es, fr, ja, zh, fa ...>

server/run.sh            # Windows: server\run.bat
```

`run.sh` creates the virtual environment and installs everything on first run, then
starts the companion on `127.0.0.1:8100`. **Leave that window open** — it is the thing
doing the translating. One command every time after that.

Then load the extension (below), open Teams **in the browser**, join a meeting and turn
on live captions. The panel opens by itself. Press **⚙** to change your language.

> **Each person needs their own OpenAI API key.** That, not the terminal, is the real
> barrier for anyone who is not a developer. Beyond about three people, host the
> companion once for everybody instead — they then install only the extension.
> `PROVIDER=ollama` avoids the key entirely if a local model is acceptable.

### Install the extension (Edge or Chrome)

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


---

## Other platforms

Everything except caption discovery is platform-independent — the settle rules,
translation, the panel, transcripts, the language handling. Only the selectors know
what Teams is, and they live in one table (`PLATFORMS` at the top of
`extension/content.js`). Adding a platform is an entry there, not a second copy of
the file.

### Google Meet — captured, not yet attributed

Observed in a real call on 2026-09-01:

```
div.a4cQT.P9KVBf
 └ div.iOzk7 [jsname="dsyhDe"]
    └ div [role="region"][aria-label="Captions"]     the caption area
       └ div.nMcdL.bj4p3b                            one speaker's turn
          ├ div.adE6rb                               the speaker
          │  ├ img.Z6byG                             avatar, no text
          │  └ div.KcIKyf.jxFHg > span.NWpY1d        "You" / "mohamad mokhtari"
          └ div.ygicle.VbkSUe                        the words
```

Captions, speakers, colours, the per-speaker filter and summaries all work. Meet
writes **"You"** for the local participant, and that is left as it stands — it is
what Meet says, and inventing a name for the person reading their own transcript
would be worse than repeating Meet's word for it.

Each person gets **one turn block that grows** while they hold the floor, which is
why sentence splitting exists (below) and why it is on for Meet and off for Teams.

Every class name there is compiler-generated and will change without notice, so
they are last resorts. `jsname` is generated too but changes far less often, and
`role="region"` is the only genuinely semantic hook Meet offers — its `aria-label`
is **localised** ("Sous-titres" in French), so it can be a hint and never the sole
selector.

Every class name there is generated and will change. The selectors try them first
and fall back to **structure** — inside a turn, the speaker block is the first child
and the words are the last — which is what will survive the next time Google
regenerates them.

If Meet ever stops working, [`tools/probe.js`](tools/probe.js) is how it was worked
out: paste it into the console of a live call and it reports the markup around words
that were actually spoken. It reads the page and nothing else — no network, no
changes to the meeting — and finds the captions **by their content** rather than by
guessing a selector.

The console log names the platform and says whether anyone has checked it:

```
[caption] platform: meet (SELECTORS NOT VERIFIED — expect to use ⚙ → find captions)
[caption] platform: teams (selectors verified 2026-08-29, and again ...)
```

## Panel features

### Opening and closing

The panel stays out of the way until it is needed:

- **Turn on live captions in Teams** → the panel opens by itself
- **Turn them off** → it closes
- **×** closes it by hand. A small `● captions` pill appears bottom-right to reopen it
- **Click the pill any time** — before a meeting, or mid-meeting with nobody talking —
  and it opens and *stays* open, showing an empty transcript rather than nothing
- Closing **never clears anything**. Reopen and the whole conversation is still there

An explicit choice — opened from the pill, or dismissed with **×** — holds until
captions are next switched on or off. After that the panel goes back to following
them. So dismissing it does not silently disable the tool for the rest of the day,
and opening it by hand is not undone a second later by the poller.

| Control | Does |
|---|---|
| **Live / Summary** tabs | Running captions, or a per-speaker summary |
| **×** | Close (reopen with the pill; nothing is lost) |
| **⤡** | Cycles **small → large → full screen**. The tooltip names the next one. `Esc` leaves full screen. Drag the bottom-right corner for any size in between |
| **–** | Collapse to the title bar |
| Speaker chips | Colour key **and** filter — click a name to see only that person, **All** to go back |

While a filter is on, a banner says whose lines are hidden and offers **show everyone**.
It stays up for as long as the filter does. Anything that hides content has to keep
saying so — a one-off status message is overwritten by the next caption, and a filter
left on by a stray click then looks exactly like one person's captions having stopped.
| **⚙** | Language picker, the detected caption language, and the capture tools |
| **reconnect captions** (in ⚙) | Find the captions again without reloading the page |
| **copy** | Whole transcript to the clipboard |
| **save** | Write the transcript to disk now, without waiting for the timer |
| **↓ jump to latest** | Appears when you scroll up; takes you back to the newest line |

### Scrolling back

Scroll up and the panel **stops following the captions**, so you can re-read
something while people keep talking. It resumes on its own when you scroll back to
the bottom, or immediately if you press **↓ jump to latest**.

Your place is kept across **Summary** and **collapse** too. Hiding an element with
`display: none` discards its scroll position — the browser has no box to keep it
on — so both are saved and put back by hand. If you were following the newest line
when you left, you come back to the newest line, not the one that was newest then.

Rows are dropped from the top after 200 of them, and the scroll position is adjusted
to compensate — otherwise what you were reading would jump up by a line every time
somebody spoke.

### Moving it

Drag the title bar. A click that does not move is just a click, and the panel always
keeps 120px on screen so it cannot be thrown somewhere you can no longer reach it.
Full screen is not draggable — it already covers the page.

Pressing **⤡** re-anchors the panel to the bottom-right corner. That is deliberate:
the size classes cannot take effect while the inline styles left behind by dragging
and the resize handle are still there.

### Languages — you choose only your own

**There is no "translate from X to Y".** You pick the language *you* want to read,
and nothing else. Which language the meeting is captioned in is the meeting's
business — it is detected automatically, per meeting.

That matters because a source setting goes stale. Someone switches the meeting to
Spanish, three people still have "from English" configured, and the translator reads
Spanish as English and produces confident nonsense. The only way to avoid that is
not to have the setting.

Press **⚙** and choose from the dropdown. Your choice is remembered.

Before you have chosen, the order is: **what `TARGET_LANG` in `server/.env` says**, then
your browser's own language, then whatever the service reports. The deployment setting
comes first on purpose — guessing from the browser is right for someone with no
configuration, and wrong against a team that deliberately set one.

**Your scenario, working:** a meeting captioned in Spanish, with three people in it.

| Reader | Sees | Cost |
|---|---|---|
| Spanish speaker | the Spanish captions, untouched | nothing — no call is made |
| English speaker | English | one call per line |
| Persian speaker | Persian, right-to-left | one call per line |

Nobody configured Spanish. The panel worked it out from the first few lines.

**The reader who already speaks the meeting's language is not translated for at
all.** No round trip, no cost, and no second copy of every line under the first.
This is enforced in code rather than by asking the model — told to translate
captions into Spanish and handed Spanish, `gpt-4o-mini` returns English.

When that happens the panel **says so**, in a banner you can click to change
language. It is a correct state that looks exactly like a broken tool, so it is not
allowed to be silent.

The first line or two of a meeting are translated before detection can answer — it
needs some text to work from. Those are hidden once pass-through turns on.

**48 languages**, each with its own font stack and text direction: Arabic and Hebrew
scripts flip the panel right-to-left; Chinese, Japanese and Korean get CJK fonts and
break lines by character rather than by word; Thai, which has no spaces at all, gets
`break-all` so lines wrap instead of running off the side. Adding a language is one
line in `server/app/languages.py` — the picker builds itself from what the service
sends.

### When there are no translations

Two different things look identical on screen — captions arriving with nothing
underneath them — so the panel says which one it is, in a banner that stays up while
it applies:

| | Banner says |
|---|---|
| The companion is not running | *…not running, so nothing is being translated. Start it with `server/run.sh` and leave that window open.* Click to retry |
| Your language already matches the captions | *The captions are already in English…* Click to pick a different one |

A grey clause in the footer was not enough. `translator offline at
http://127.0.0.1:8100` is not an instruction for a colleague whose terminal window got
closed.

### If captions stop reaching the panel

When the panel finds captions somewhere it is not watching, it **widens** to take
them in rather than letting go and searching again. That distinction matters: on some
builds, caption-like text exists outside the live caption area permanently — an
aria-live region, a second copy in the meeting UI — so "captions I am not watching"
is always true. Treating that as a fault meant letting go every few seconds, and
because letting go used to discard lines mid-sentence, a whole meeting became its
first sentence. Widening cannot do that: each step strictly grows the subtree, so it
converges and then stops, and nothing in flight is dropped.

Letting go only happens when the container has produced **nothing at all** for a
while and captions are arriving elsewhere — a container that is working updates
constantly, so it cannot happen mid-meeting.

Teams does not render its caption area the same way everywhere. Some builds put every
line inside one `closed-caption-v2-window`; others give **each line its own wrapper**
with no such window at all. The panel attaches to an ancestor of *every* caption line
it can see, not to the first line's parent — that parent holds one line, so on the
second layout everything after the opening sentence was outside the element being
watched, and the observer sat on a box that would never change again.

The first caption arrives alone, and one line cannot tell you where the caption area
starts. So it attaches straight away — catching that line — and widens once a second
line shows the container was too narrow. One re-attach, then it settles.


Teams sometimes rebuilds its caption area mid-meeting, leaving the old element in the
page with its last few lines still in it. The panel now notices — the signal is caption
text appearing somewhere it is **not** watching — and re-attaches on its own within a
second or two.

If it ever does not, **⚙ → reconnect captions** does the same thing by hand: it drops
the caption element and hunts for it again. That is what reloading the page used to be
for, minus dropping out of the meeting and losing the transcript.

> The old check asked whether the container still *held* caption text and treated that
> as proof it was still the live one. It is not, and that state reads as healthy
> forever — which is why a reload was the only cure.

### When a language changes

**Nothing already translated is thrown away.** Ten lines in Italian, then you switch
to Persian: the Italian stays, and line eleven onwards is Persian. Each row keeps the
script and direction of the language it was translated into, so a mixed panel renders
both halves correctly. The saved file has always worked this way and the panel now
agrees with it.

**A divider says where it happened**, in the panel and in the file:

```
────────  15:02:03 — Now translating into Persian (Farsi)  ────────
```

Three kinds get marked:

| | Shown as |
|---|---|
| Detection finishes | *Captions are in Spanish — translating into Persian (Farsi)* |
| You change your language | *Now translating into Persian (Farsi)* |
| The meeting changes its caption language | *Meeting captions changed to Spanish — translating into Persian (Farsi)* |

The caption language is **re-checked every 40 lines**, so a meeting switched from
English to Spanish mid-call is noticed rather than quietly producing a wrong source
hint — and possibly translating for someone who no longer needs it, or not for
someone who now does.

> If you see the divider appear and disappear between two similar languages
> (Spanish/Portuguese, Croatian/Serbian), tell me — the fix is to require two
> agreeing checks before accepting a change, and I would rather add that after
> seeing it than guess at the threshold.

**One honest limit:** distant pairs are worse than close ones. Japanese to Persian or
Chinese to French are low-resource *pairs*, and the model will pivot through English
internally. Test the pairs you actually need before promising them.

### One row per sentence — on Meet only

**Teams is not split, deliberately.** Teams already breaks its captions where the
speaker actually paused, so splitting them again second-guesses something that is
already right. One caption line stays one row there, exactly as it always has.

**Google Meet appends to the same line for as long as one person keeps talking.**
Translating "the line" then means retranslating a paragraph that grows every few
seconds, and the translation rewrites itself under the reader:

```
Hello, everyone.
Hello, everyone. Uh, my name is Mohammad.
Hello, everyone. Uh, my name is Mohammad. And let's stay to join other person.
   ... one row, retranslated in full, every few seconds
```

So finished text is committed to rows — **grouped**, not one row per full stop:

```
Hello, everyone. Uh, my name is Mohammad. And let's stay to join other person
to this meeting.                                              → translated once
Other sentence. Uh, after stop and speed again.               → translated once
```

Sentences are gathered until a row holds about a line of text, breaking only at a
sentence end. Splitting at every full stop was technically correct and horrible to
read — *"Of course. I can help you. And maybe you can help me."* became three rows,
each a clause on its own. It also translated worse: *"Of course."* alone gives the
model nothing to work from, and the right Persian for it depends on what follows.

A finished sentence too short to be worth a row **waits**. Half a second of silence
is somebody thinking; two seconds is a real stop.

What you have already read stays read. Abbreviations, initials and decimals are not
mistaken for sentence ends (`Dr.`, `e.g.`, `3.5`). An unfinished tail is shown greyed
and is only committed once the speaker has actually stopped.

**A committed row is never re-cut.** Rows are tracked by where they end in the
line, so text arriving later starts a new row instead of quietly extending one you
have already read. The words inside a row can still be rewritten if the recogniser
revises them — boundaries are fixed, contents are not.

**Rows are keyed by position, not by text.** That is
what keeps this safe: live recognition revises itself across a full stop it has
already produced — *"Good morning everyone."* becomes *"Good morning everyone, thanks
for joining."* a second later. Positional keys mean sentence 0 stays sentence 0 and
its row is rewritten in place. Treating a finished sentence as immutable instead
produced two rows, the second of them the fragment *", thanks for joining."*

**Nothing here reaches Teams.** `split: false` on its platform entry, and running
identical Teams captions through the build before this work and the one after gives
byte-for-byte the same rows, the same translator calls and the same attach
behaviour. A test holds Teams to one row per caption line so Meet's needs cannot
follow it back.

### When a line gets translated

A caption line is translated once it has **stopped changing** *and* **finished a
sentence**. Those are two different questions, and treating them as one is what used
to make the translation rewrite itself while you were reading it:

> Someone says *"So the pipeline"* — pauses to think — *"writes to ClickHouse."*
> The pause is longer than the settle window, so the fragment got translated, and
> then the finished sentence got translated again over the top of it.

Now a line that ends on a full stop goes out after 0.7s; one that does not is held
for up to 2s, and any further speech resets that. A sentence built up a few words at
a time is translated **once, at the end**.

If the caption stream turns out not to punctuate at all — Teams does this well in
English and unevenly elsewhere — the panel notices after a few lines and stops
waiting, rather than adding two seconds to every line in pursuit of a full stop that
is never coming.

**One case this does not cover:** if Teams *extends* a line after a sentence has
already completed, the whole line is retranslated and the wording can shift. Fully
removing that means treating each sentence as its own row rather than each Teams
caption line — a bigger change, worth doing only if you still notice it.

### Saving the meeting to a file

Every conversation is appended to a Markdown file so a meeting can be re-read later.

**Where:** `~/teams-captions/` — `C:\Users\<you>\teams-captions\` on Windows. One
file per session, named for when it started: `2026-08-31_1502_zz01.md`. The panel
shows the full path on its bottom line; **click it to copy**.

Change the folder in `server/.env`:

```
TRANSCRIPT_DIR=D:/meetings
TRANSCRIPT_ENABLED=true
```

**What it looks like:**

```markdown
# Meeting transcript

- **Started:** 31/08/2026, 15:02:11
- **Source:** Microsoft Teams live captions
- **Translated into:** Persian (Farsi)

---

**Mohamad Mokhtari**

`15:02:14` Good morning everyone, can you all hear me?
> صبح بخیر همگی، صدای من را می‌شنوید؟

`15:02:22` Today I want to walk through the feature extraction pipeline.
> امروز می‌خواهم پایپ‌لاین استخراج ویژگی را مرور کنم.

**Sarah Chen**

`15:02:41` Is that the one that writes to ml_rules_features?
> همان است که در ml_rules_features می‌نویسد؟
```

The speaker's name is written only when it changes, so a run of short lines from one
person does not become a wall of headings.

**Three decisions worth knowing:**

*The file is written by the local service, not the extension.* A browser extension
cannot write to a folder of your choosing — the best it could do is drop a file in
Downloads, and it cannot append. The companion is already running on the same
machine, so it does the writing and tells the panel where the file is. **No
transcript is saved while the companion is not running**, and the panel's bottom line
says so.

*It writes every 60 seconds, not every 3–5 minutes.* The interval is really a
data-loss window: close the tab and you lose whatever has not been written yet. The
write itself is a local request appending a few lines and costs nothing, so there is
no reason to make that window larger than it has to be. It also writes when captions
are switched off, when the tab is closed, and whenever you press **save**.

*A line is written ~20 seconds after it is spoken.* Live captions rewrite themselves
for a few seconds after they first appear, and the translation arrives a second or
two after that. Waiting for a line to settle is what lets the file be pure
append — nothing already written ever has to be corrected. You are reading this file
after the meeting, so the delay costs nothing.

> **The transcript is meeting content on disk, in clear text.** Everything said in
> every meeting accumulates in that folder. Know that before you turn it loose on
> anything confidential, and set `TRANSCRIPT_ENABLED=false` if you would rather it
> did not.

### Tests

`test_smoke.py` runs the **real `content.js`** against a fake Teams page and a fake
service, in a small DOM built for the purpose (`tests/dom.js`). It feeds captions in
and asserts the panel shows them.

This exists because two regressions reached a live meeting. It is the only test that
would have caught either.

```bash
server/.venv/bin/python -m pip install quickjs
tests/run_all.sh
```

198 checks, all offline — nothing calls a provider, so they run without a key.



`syntax_check.py` compiles the file inside a function expression that is never
called, so every syntax error surfaces without a DOM to run against.

`tests/test_panel.py` lifts the state machine **verbatim out of `content.js`** and
runs it, rather than restating the logic — so it fails if the real code changes
behaviour, instead of testing a copy of itself.

### Speaker colours

Derived from a hash of the name, not assigned randomly — so the same person keeps the
same colour for the whole meeting, across a reload, and on everyone's screen. The
palette is hand-picked for legibility on the dark panel; arbitrary hues produce some
that cannot be read against it.

### Per-speaker summary

Pick a speaker, press **Summarise**, and their whole contribution so far is sent to
the local service and comes back as a structured summary in your language: their
overall position, a bullet list of specifics (decisions, numbers, commitments,
questions), and anything they asked that was left unanswered under `Open:`.

It summarises the **English** transcript rather than the translations — summarising a
translation compounds whatever the translator got wrong. It is user-triggered, never
automatic: it reads the whole transcript for that person and costs meaningfully more
than one caption line.
