"""
Run the real extension against a fake Teams page and a fake service.

This is the test that would have caught both regressions that reached a live
meeting. It feeds captions to the actual content.js and asserts that the panel
shows them.

    server/.venv/bin/python tests/test_smoke.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import quickjs

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOM = (ROOT / "tests" / "dom.js").read_text()
SRC = (ROOT / "extension" / "content.js").read_text()

fails: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        fails.append(label)


PAGE = """
/*
 * A Teams caption area.
 *
 * `layout` picks between the shapes Teams has been seen to render:
 *
 *   "window"   one [data-tid=closed-caption-v2-window] holding every line
 *   "wrappers" each line in its own wrapper, with NO caption window -- which is
 *              what forces captionRoot down its fallback path
 *
 * The second exists because the first was too kind: with every caption under one
 * obvious ancestor, code that picks the wrong ancestor still looks correct.
 */
let layout = "window";
let ccWindow = null, stage = null;
let lineNo = 0;
let lines = {};

/*
 * A second copy of caption text that is not the live caption area.
 *
 * Teams renders an aria-live region for screen readers, and other caption-ish
 * text exists around the meeting UI. Anything that treats "caption text I am not
 * watching" as proof of a problem will see this and never stop reacting -- which
 * is what turned a whole meeting into its first sentence.
 */
/*
 * Google Meet's caption area, as observed on 2026-09-01:
 *
 *   div.iOzk7[jsname=dsyhDe] > div[role=region][aria-label=Captions]
 *     > div.nMcdL.bj4p3b        one speaker's turn
 *       > div.ygicle.VbkSUe     the words
 */
let meetRegion = null, meetTurns = {};
function buildMeetPage() {
  const outer = document.createElement("div");
  outer.className = "a4cQT P9KVBf";
  const mid = document.createElement("div");
  mid.className = "iOzk7";
  mid.setAttribute("jsname", "dsyhDe");
  meetRegion = document.createElement("div");
  meetRegion.className = "vNKgIf UDinHf";
  meetRegion.setAttribute("role", "region");
  meetRegion.setAttribute("aria-label", "Captions");
  mid.appendChild(meetRegion);
  outer.appendChild(mid);
  document.body.appendChild(outer);
  meetTurns = {};
}
function meetSay(speaker, text, id) {
  if (!meetRegion) buildMeetPage();
  id = id || ("turn" + (++lineNo));
  let turn = meetTurns[id];
  if (!turn) {
    turn = document.createElement("div");
    turn.className = "nMcdL bj4p3b";
    const words = document.createElement("div");
    words.className = "ygicle VbkSUe";
    turn.appendChild(words);
    meetRegion.appendChild(turn);
    meetTurns[id] = turn;
  }
  turn.querySelector("div.ygicle").textContent = text;
  return id;
}
function meetRows() {
  const log = document.querySelector("#mct-log");
  if (!log) return [];
  return log.querySelectorAll(".mct-seg").map(r => ({
    speaker: r.querySelector(".mct-spk").textContent,
    text:    r.querySelector(".mct-txt").textContent,
    tr:      r.querySelector(".mct-tr").textContent,
  }));
}

/** An old, empty caption window left behind in the page. Teams does this. */
function addEmptyCaptionWindow() {
  const orphan = document.createElement("div");
  orphan.setAttribute("data-tid", "closed-caption-v2-window");
  document.body.insertBefore
    ? document.body.appendChild(orphan) : document.body.appendChild(orphan);
}

function addDecoyCaptionText(text) {
  const region = document.createElement("div");
  region.setAttribute("aria-live", "polite");
  const span = document.createElement("span");
  span.setAttribute("data-tid", "closed-caption-text");
  span.className = "fui-StyledText";
  span.textContent = text;
  region.appendChild(span);
  document.body.appendChild(region);
}

function buildPage() {
  stage = document.createElement("div");
  stage.setAttribute("data-tid", "meeting-stage");
  document.body.appendChild(stage);
  if (layout === "window") {
    ccWindow = document.createElement("div");
    ccWindow.setAttribute("data-tid", "closed-caption-v2-window");
    stage.appendChild(ccWindow);
  } else {
    ccWindow = null;
  }
  lines = {};
}

/** Say something, or revise what was last said under the same id. */
function say(speaker, text, id) {
  if (!stage) buildPage();
  id = id || ("line" + (++lineNo));
  let line = lines[id];
  if (!line) {
    // Real Teams: a per-line wrapper around the line, and the text span carries
    // both the data-tid and the Fluent class.
    const wrapper = document.createElement("div");
    wrapper.className = "___" + (1000 + lineNo);
    line = document.createElement("div");
    line.setAttribute("data-tid", "closed-caption-line");
    const who = document.createElement("span");
    who.setAttribute("data-tid", "author");
    who.textContent = speaker;
    const txt = document.createElement("span");
    txt.setAttribute("data-tid", "closed-caption-text");
    txt.className = "fui-StyledText";
    line.appendChild(who);
    line.appendChild(txt);
    wrapper.appendChild(line);
    (ccWindow || stage).appendChild(wrapper);
    lines[id] = line;
  }
  line.querySelector('[data-tid="closed-caption-text"]').textContent = text;
  return id;
}

/**
 * Throw away the caption area and build a new one, leaving the old node in the
 * page with its last lines still in it -- which is what Teams does, and what made
 * the old liveness check believe everything was fine forever.
 */
function moveCaptionsToANewContainer() {
  if (ccWindow) ccWindow.removeAttribute("data-tid");
  stage.removeAttribute("data-tid");
  stage = null;
  buildPage();
}

/** Rows the reader can actually see, ignoring any a filter hid. */
function visibleRows() {
  const log = document.querySelector("#mct-log");
  if (!log) return [];
  return log.querySelectorAll(".mct-seg")
    .filter(r => r.style.display !== "none")
    .map(r => ({ speaker: r.querySelector(".mct-spk").textContent,
                 text: r.querySelector(".mct-txt").textContent }));
}
function click(sel) { const n = document.querySelector(sel); if (n && n.onclick) n.onclick(); }
function clickChip(name) {
  for (const c of document.querySelectorAll(".mct-chip"))
    if (c.textContent.startsWith(name)) { c.onclick(); return true; }
  return false;
}
function panelNotes() {
  const log = document.querySelector("#mct-log");
  return log ? log.querySelectorAll(".mct-note").map(n => n.textContent) : [];
}

/** What the panel is showing: one entry per caption row. */
function panelRows() {
  const log = document.querySelector("#mct-log");
  if (!log) return [];
  return log.querySelectorAll(".mct-seg").map(r => ({
    speaker: r.querySelector(".mct-spk").textContent,
    text:    r.querySelector(".mct-txt").textContent,
    tr:      r.querySelector(".mct-tr").textContent,
  }));
}
"""

SERVICE = """
let detectAnswer = null;          // {lang, name} or null for "und"
let pinned = true;                // does the deployment configure a language?
let translatePrefix = "TR:";

routes["/config"] = () => ({
  target_lang: "fa", target_lang_name: "Persian (Farsi)", rtl: true, script: "arab",
  provider: "openai", context_segments: 3, target_lang_pinned: pinned,
  transcript_enabled: true, transcript_dir: "/tmp/x",
  languages: [
    {code:"en", name:"English",         native:"English", script:"latn", rtl:false},
    {code:"fa", name:"Persian (Farsi)", native:"Farsi",   script:"arab", rtl:true},
    {code:"es", name:"Spanish",         native:"Espanol", script:"latn", rtl:false},
  ],
});
routes["/detect"] = () => detectAnswer
  ? { lang: detectAnswer.lang, name: detectAnswer.name, confident: true, ms: 10 }
  : { lang: "und", name: "", confident: false, ms: 0 };
routes["/translate"] = (b) => ({
  translation: translatePrefix + b.text, cached: false, ms: 12,
  provider: "openai", key: b.key, passthrough: false,
});
routes["/transcript"] = () => ({ path: "/tmp/x/s.md", directory: "/tmp/x",
                                 written: 0, total: 0, bytes: 0 });
routes["/summarize"] = () => ({ summary: "s", segments: 1, ms: 1, provider: "openai" });
"""


def pump(c: quickjs.Context, rounds: int = 200) -> None:
    """Run queued promise callbacks.

    quickjs only advances promises when asked. Without this the extension's
    fetches resolve into nothing: /config never lands, so `server.ok` stays false
    and no translation is ever requested. The first run of this harness looked
    exactly like the bug it was written to find, for that reason and not the real
    one -- worth remembering before trusting a red result here.
    """
    while rounds > 0 and c.execute_pending_job():
        rounds -= 1


def tick(c: quickjs.Context, ms: int) -> None:
    """Let time pass, draining promises around every timer that fires."""
    pump(c)
    c.eval(f"advance({ms})")
    pump(c)
    c.eval("flushMutations()")
    pump(c)


def boot(setup: str = "") -> quickjs.Context:
    c = quickjs.Context()
    c.eval(DOM)
    c.eval(SERVICE)
    c.eval(PAGE)
    if setup:
        c.eval(setup)
    c.eval(SRC)
    tick(c, 200)                # let loadConfig resolve
    return c


def chosen(c):
    """The language shown in the picker — what the reader would see selected."""
    return c.eval('document.querySelector("#mct-lang").value || ""')


def is_passthrough(c):
    return bool(c.eval('document.querySelector("#mct-panel").classList.contains("passthrough")'))


def rows(c):
    return c.eval("JSON.stringify(panelRows())")


# ---------------------------------------------------------------------------

def test_captions_reach_the_panel():
    print("Smoke: captions arrive, settle, and are translated")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    c.eval('say("Sarah", "Good morning everyone.")')
    tick(c, 1500)

    got = json.loads(rows(c))
    check("  one row", len(got), 1)
    if got:
        check("  speaker", got[0]["speaker"], "Sarah")
        check("  caption text", got[0]["text"], "Good morning everyone.")
        check("  translated", got[0]["tr"], "TR:Good morning everyone.")


def test_it_keeps_going():
    """
    The regression that reached a live meeting.

    A deployment with TARGET_LANG=fa, a reader on an English browser, and an
    English meeting. The panel translated the first couple of lines and then went
    quiet: the browser guess had replaced Persian with English, detection found
    the captions were English too, and pass-through correctly stopped translating
    something that no longer needed it. Every step was working; the combination
    was not.
    """
    print("Smoke: a configured language survives an English browser")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    check("  reader's language", chosen(c), "fa")
    check("  not pass-through", is_passthrough(c), False)

    said = [
        ("Sarah",  "Good morning everyone, thanks for joining us today."),
        ("Sarah",  "Today we are going to review the feature extraction pipeline."),
        ("Mohamad","Sorry, could you repeat the part about the deployment?"),
        ("Sarah",  "Of course. We deploy the new model on Friday afternoon."),
        ("Mohamad","Understood, thank you very much."),
        ("Sarah",  "Any other questions before we finish?"),
    ]
    for who, what in said:
        c.eval(f'say({who!r}, {what!r})'.replace("'", '"'))
        tick(c, 1500)

    got = json.loads(rows(c))
    # Rows are sentences now, not caption lines: "Of course. We deploy the new
    # model on Friday afternoon." arrives as one caption and becomes two segments,
    # because a finished sentence is never revisited and so its translation never
    # changes again.
    joined = " ".join(r["text"] for r in got)
    for _, what in said:
        check(f"  {what[:34]!r}...", what in joined, True)
    check("  the last line is there", got[-1]["text"] if got else "", said[-1][1])
    missing = [r["text"] for r in got if not r["tr"]]
    check("  every sentence translated", missing, [])


def test_guess_when_nothing_is_configured():
    print("Smoke: with nothing configured, the browser's language is used")
    c = boot('pinned = false; detectAnswer = {lang:"es", name:"Spanish"};')
    check("  guessed from en-US", chosen(c), "en")


def test_detection_note():
    print("Smoke: detection adds one divider, and only one")
    c = boot('detectAnswer = {lang:"es", name:"Spanish"};')
    for i in range(6):
        c.eval(f'say("Carlos", "Buenos dias a todos, esta es la linea numero {i} de la reunion.")')
        tick(c, 1500)
    notes = json.loads(c.eval("JSON.stringify(panelNotes())"))
    check("  one divider", len(notes), 1)
    check("  names both languages",
          bool(notes) and "Spanish" in notes[0] and "Persian" in notes[0], True)


def test_passthrough():
    print("Smoke: a reader whose language matches is not translated for")
    c = boot('detectAnswer = {lang:"fa", name:"Persian (Farsi)"};')
    for i in range(6):
        c.eval(f'say("Ali", "This is line number {i} of a reasonably long meeting sentence.")')
        tick(c, 1500)
    got = json.loads(rows(c))
    check("  captions still shown", len(got), 6)
    check("  marked pass-through", is_passthrough(c), True)

    # Detection needs a couple of lines of text before it can answer, so the lines
    # before it lands are translated -- unavoidable, and hidden by the CSS the
    # moment pass-through turns on. What matters is that it then STOPS.
    before = c.eval("calls.filter(x=>x.path=='/translate').length")
    for i in range(6):
        c.eval(f'say("Ali", "Another perfectly ordinary sentence, number {i + 10} of this meeting.")')
        tick(c, 1500)
    check("  translating stops once it is known",
          c.eval("calls.filter(x=>x.path=='/translate').length"), before)
    check("  captions keep arriving", len(json.loads(rows(c))), 12)

    print("Smoke: and it says why, rather than just going quiet")
    # The lane vanishing with no explanation is what made a correct state look
    # like a broken tool.
    why = c.eval('document.querySelector("#mct-why").textContent')
    check("  explains itself", "already in Persian (Farsi)" in why, True)
    check("  offers the way out", "pick a different one" in why, True)



def test_summary_round_trip():
    """
    Reported from a real meeting: after showing a colleague the Summary tab and
    coming back, only one person's new captions appeared.
    """
    print("Smoke: two speakers, a trip to Summary, and back")
    c = boot('detectAnswer = {lang:"en", name:"English"};')

    for who, what in [("Mohamad", "Good morning, can everybody hear me clearly today?"),
                      ("Reza",    "Yes, I can hear you perfectly well thank you.")]:
        c.eval(f'say("{who}", "{what}")')
        tick(c, 1500)
    check("  both speakers before", len(json.loads(c.eval("JSON.stringify(visibleRows())"))), 2)

    c.eval('click("#mct-tab-sum")');  tick(c, 300)
    c.eval('click("#mct-tab-live")'); tick(c, 300)

    for who, what in [("Mohamad", "Right, let me carry on with the next point please."),
                      ("Reza",    "Sure, go ahead, I am following along with you.")]:
        c.eval(f'say("{who}", "{what}")')
        tick(c, 1500)

    seen = json.loads(c.eval("JSON.stringify(visibleRows())"))
    check("  all four visible after", len(seen), 4)
    check("  both speakers still visible",
          sorted({r["speaker"] for r in seen}), ["Mohamad", "Reza"])


def test_filter_survives_summary():
    """The same trip, but with a speaker filter deliberately left on."""
    print("Smoke: a filter left on, then Summary and back")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    for who, what in [("Mohamad", "Good morning, can everybody hear me clearly today?"),
                      ("Reza",    "Yes, I can hear you perfectly well thank you.")]:
        c.eval(f'say("{who}", "{what}")')
        tick(c, 1500)

    c.eval('clickChip("Reza")'); tick(c, 100)
    check("  filtered to Reza", len(json.loads(c.eval("JSON.stringify(visibleRows())"))), 1)

    c.eval('click("#mct-tab-sum")');  tick(c, 300)
    c.eval('click("#mct-tab-live")'); tick(c, 300)
    c.eval('say("Mohamad", "Another sentence from me while the filter is still on.")')
    tick(c, 1500)

    seen = json.loads(c.eval("JSON.stringify(visibleRows())"))
    check("  still Reza only", sorted({r["speaker"] for r in seen}), ["Reza"])
    check("  the chip still says so",
          bool(c.eval('document.querySelectorAll(".mct-chip").some('
                      'x => x.textContent.startsWith("Reza") && x.classList.contains("on"))')), True)

    print("Smoke: a filter keeps saying who it is hiding")
    # It used to say so in the status line, which emit() rewrites on every
    # caption -- so a filter left on by a stray click on the chip row became
    # invisible within a second, and looked like one person's captions had
    # stopped arriving.
    check("  banner is up",
          bool(c.eval('document.querySelector("#mct-filtered").classList.contains("on")')), True)
    check("  names who",
          "Showing only Reza" in c.eval('document.querySelector("#mct-filtered-txt").textContent'), True)

    c.eval('say("Reza", "A few more sentences go by while the filter is still set.")')
    tick(c, 1500)
    c.eval('say("Reza", "And another one after that, just to be sure about it.")')
    tick(c, 1500)
    check("  still up several captions later",
          bool(c.eval('document.querySelector("#mct-filtered").classList.contains("on")')), True)

    print("Smoke: and offers the way out")
    c.eval('click("#mct-filtered-clear")'); tick(c, 100)
    check("  banner gone",
          bool(c.eval('document.querySelector("#mct-filtered").classList.contains("on")')), False)
    check("  everyone back",
          sorted({r["speaker"] for r in json.loads(c.eval("JSON.stringify(visibleRows())"))}),
          ["Mohamad", "Reza"])



def test_captions_move():
    """
    Reported from a real meeting: captions kept appearing in Teams but stopped
    reaching the panel, and only reloading the page and rejoining fixed it.

    Teams can start writing captions into a new element while the old one stays
    in the page holding its last few lines. The old check asked whether our
    container still HELD caption text and read that as healthy -- so it never
    looked for the new one.
    """
    print("Smoke: Teams moves its captions to a new element")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    c.eval('say("Sarah", "This is the first sentence, before anything moves.")')
    tick(c, 1500)
    check("  captured before", len(json.loads(rows(c))), 1)

    c.eval("moveCaptionsToANewContainer()")
    c.eval('say("Sarah", "And this one arrives after Teams rebuilt the caption area.")')
    # Recovery is deliberately not instant here: the old and new areas share no
    # sensible ancestor, so widening cannot help and the container has to be given
    # up on -- which only happens once it has gone properly quiet, so that a
    # working container is never abandoned mid-meeting.
    tick(c, 12000)

    got = json.loads(rows(c))
    check("  captured after the move", len(got), 2)
    check("  the new line is there",
          got[-1]["text"] if got else "",
          "And this one arrives after Teams rebuilt the caption area.")
    check("  and it was translated", bool(got and got[-1]["tr"]), True)


def test_reconnect_button():
    """The button used to re-check the translator only, which was never the thing
    that had broken."""
    print("Smoke: the reconnect button re-finds the captions")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    c.eval('say("Sarah", "One sentence to get us attached to something.")')
    tick(c, 1500)

    c.eval("moveCaptionsToANewContainer()")
    c.eval('click("#mct-retry")')
    tick(c, 300)
    c.eval('say("Sarah", "A sentence right after pressing reconnect.")')
    tick(c, 1500)

    got = json.loads(rows(c))
    check("  back to capturing", len(got), 2)
    check("  without a page reload", got[-1]["text"] if got else "",
          "A sentence right after pressing reconnect.")


def test_no_reattach_thrash():
    """A shadow-piercing search must not mistake our own container's contents for
    captions living somewhere else."""
    print("Smoke: a healthy meeting does not re-attach on a loop")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    c.eval("logLines.length = 0;")
    for i in range(5):
        c.eval(f'say("Sarah", "An ordinary sentence, number {i}, in a healthy meeting.")')
        tick(c, 1500)
    n = c.eval('logLines.filter(l => l[1].indexOf("re-attaching") >= 0).length')
    check("  no re-attaches", n, 0)



def test_collapse_and_tabs():
    """Collapse and the Summary tab both hide the log, but only one of them owns
    the collapsed flag."""
    print("Smoke: collapse, then a trip to Summary and back")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    c.eval('say("Sarah", "A sentence so there is something to collapse.")')
    tick(c, 1500)

    shown = lambda: c.eval('document.querySelector("#mct-log").style.display') != "none"
    label = lambda: c.eval('document.querySelector("#mct-hide").textContent')

    c.eval('click("#mct-hide")'); tick(c, 50)
    check("  collapsed", shown(), False)
    check("  button offers to expand", label(), "+")

    c.eval('click("#mct-tab-sum")');  tick(c, 100)
    c.eval('click("#mct-tab-live")'); tick(c, 100)
    # Clicking "Live" is a request to see the captions, so it un-collapses too.
    check("  log is back after the tabs", shown(), True)
    # The two controls used to keep separate ideas of this. The button said "+"
    # over an expanded log, and the next press flipped only the flag.
    check("  button agrees with what is on screen", label(), "\u2013")

    c.eval('click("#mct-hide")'); tick(c, 50)
    check("  one press collapses it again", shown(), False)

    print("Smoke: collapse is not offered where it means nothing")
    c.eval('click("#mct-tab-sum")'); tick(c, 100)
    check("  hidden on the Summary tab",
          c.eval('document.querySelector("#mct-hide").style.display'), "none")
    c.eval('click("#mct-tab-live")'); tick(c, 100)
    check("  back on Live", c.eval('document.querySelector("#mct-hide").style.display'), "")



def test_service_down():
    """What a reader sees when the companion is not running."""
    print("Smoke: the companion is not running")
    c = boot('delete routes["/config"]; delete routes["/translate"];')
    for i in range(3):
        c.eval(f'say("Sarah", "A sentence number {i} spoken while nothing is listening.")')
        tick(c, 1500)

    got = json.loads(rows(c))
    check("  captions still shown", len(got), 3)
    check("  no translations", [r["tr"] for r in got], ["", "", ""])

    # This used to be explained only by a clause in a small grey footer:
    # "translator offline at http://127.0.0.1:8100", which is not an instruction
    # for a colleague whose run.sh window got closed.
    why = c.eval('document.querySelector("#mct-why").textContent')
    check("  banner is up",
          bool(c.eval('document.querySelector("#mct-panel").classList.contains("why")')), True)
    check("  says what is wrong", "companion is not running" in why, True)
    check("  says how to fix it", "server/run.sh" in why, True)
    check("  not confused with pass-through",
          bool(c.eval('document.querySelector("#mct-panel").classList.contains("passthrough")')),
          False)

    print("Smoke: and it clears itself when the companion comes back")
    c.eval("""routes["/config"] = () => ({
      target_lang: "fa", target_lang_name: "Persian (Farsi)", rtl: true, script: "arab",
      provider: "openai", context_segments: 3, target_lang_pinned: true,
      transcript_enabled: false, transcript_dir: "/tmp/x",
      languages: [{code:"en",name:"English",native:"English",script:"latn",rtl:false},
                  {code:"fa",name:"Persian (Farsi)",native:"Farsi",script:"arab",rtl:true}],
    });
    routes["/translate"] = (b) => ({ translation: "TR:" + b.text, cached: false, ms: 5,
                                     provider: "openai", key: b.key, passthrough: false });""")
    tick(c, 16000)          # the reconnect poll runs every 15s
    check("  banner gone",
          bool(c.eval('document.querySelector("#mct-panel").classList.contains("why")')), False)

    c.eval('say("Sarah", "A sentence spoken once the companion is back up again.")')
    tick(c, 1500)
    got = json.loads(rows(c))
    check("  translating again", bool(got[-1]["tr"]), True)



def test_no_caption_window():
    """
    Teams without a [data-tid=closed-caption-v2-window], each line in its own
    wrapper. captionRoot falls back to "the parent of the first line" -- which
    holds exactly one line, so every other caption is somewhere else.
    """
    print("Smoke: captions in per-line wrappers, no caption window")
    c = boot('layout = "wrappers"; detectAnswer = {lang:"en", name:"English"};')
    said = ["Good morning everyone, thanks for joining us here today.",
            "Today we are reviewing the feature extraction pipeline together.",
            "Does anybody have a question before we get properly started?",
            "Right, then let me share my screen with all of you now."]
    for t in said:
        c.eval(f'say("Sarah", "{t}")')
        tick(c, 1500)

    got = json.loads(rows(c))
    check("  every line captured", len(got), len(said))
    check("  the last one too", got[-1]["text"] if got else "", said[-1])

    # The first caption arrives alone, and one line cannot tell you how far up the
    # caption area starts. So it attaches immediately -- catching that line -- and
    # widens once a second line proves the container was too narrow. What matters
    # is that it converges and then stops, not that it guesses right first time.
    # The first caption arrives alone and one line cannot say where the caption area
    # starts, so it attaches immediately -- catching that line -- and then WIDENS to
    # take in the next one. Widening rather than letting go and searching again is
    # what makes this safe: each step strictly grows the subtree, so it converges,
    # and nothing in flight is dropped on the way.
    log = c.eval('logLines.map(l => l[1]).join(" | ")')
    check("  widened rather than let go", "widened to take in" in log, True)
    check("  never let go", "re-attaching" in log, False)
    check("  not mislabelled as shadow DOM",
          "shadow DOM" in c.eval('logLines.map(l => l[1]).join(" ")'), False)

    print("Smoke: a long meeting in that layout stays attached")
    c.eval("logLines.length = 0")
    for i in range(8):
        c.eval(f'say("Reza", "Sentence number {i + 10}, spoken well into the meeting.")')
        tick(c, 1500)
    check("  no further re-attaches",
          c.eval('logLines.filter(l => l[1].indexOf("re-attaching") >= 0).length'), 0)
    check("  all of them captured", len(json.loads(rows(c))), 12)



def test_decoy_caption_text():
    """
    Caption text that is not the live caption area -- an aria-live region, a
    second copy somewhere in the meeting UI.

    This is the shape that broke a real meeting on Linux and then on Windows: the
    condition "caption text exists somewhere I am not watching" was permanently
    true, so the panel let go of its container every few seconds and, because
    letting go discarded pending lines, showed the first sentence and nothing
    else.
    """
    print("Smoke: a second copy of caption text elsewhere in the page")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    c.eval('addDecoyCaptionText("some other caption-ish text that never changes")')

    said = [f"Sentence number {i}, spoken in a meeting that should just keep working."
            for i in range(10)]
    for t in said:
        c.eval(f'say("Sarah", "{t}")')
        tick(c, 1500)

    got = json.loads(rows(c))
    check("  every line captured", len(got), len(said))
    check("  the last one too", got[-1]["text"] if got else "", said[-1])
    check("  all translated", [r["text"] for r in got if not r["tr"]], [])

    log = c.eval('logLines.map(l => l[1]).join(" | ")')
    check("  never let go of the container", "re-attaching" in log, False)
    widened = c.eval('logLines.filter(l => l[1].indexOf("widened") >= 0).length')
    # Widening is bounded and monotonic, so even when it cannot help it stops.
    check("  widening stopped by itself", widened <= 6, True)

    print("Smoke: and it survives a long stretch of it")
    c.eval("logLines.length = 0")
    for i in range(20):
        c.eval(f'say("Reza", "A later sentence, number {i + 100}, still going along fine.")')
        tick(c, 1500)
    check("  still capturing", len(json.loads(rows(c))), 30)
    check("  and still not letting go",
          "re-attaching" in c.eval('logLines.map(l => l[1]).join(" | ")'), False)



def test_stalled_capture_is_visible():
    """
    Capture failing used to look like an empty panel and nothing else.

    The banner for it is a backstop and is not exercised end to end here: every
    break this harness can construct is one the panel recovers from by itself,
    which is the right way round. What is tested is that recovery happens, and
    that nothing stale is left on screen afterwards.
    """
    print("Smoke: the caption area moves somewhere unrelated")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    c.eval('say("Sarah", "One sentence that does get through normally.")')
    tick(c, 1500)
    check("  quiet meeting says nothing",
          bool(c.eval('document.querySelector("#mct-panel").classList.contains("why")')), False)

    # Teams throws the caption area away and builds a new one somewhere with no
    # sensible shared ancestor, so widening cannot help and the container has to be
    # given up on.
    c.eval("moveCaptionsToANewContainer()")
    before = len(json.loads(rows(c)))
    for i in range(4):
        c.eval(f'say("Sarah", "A sentence spoken after the caption area moved, {i}.")')
        tick(c, 4000)

    got = json.loads(rows(c))
    check("  recovered on its own", len(got) > before, True)
    check("  the newest line is there", "after the caption area moved" in got[-1]["text"], True)
    # Recovery beat the stalled banner to it, which is the right way round: fixing
    # itself is better than telling somebody how to fix it.
    check("  no banner needed",
          bool(c.eval('document.querySelector("#mct-panel").classList.contains("why")')), False)
    check("  and no stale text left behind",
          c.eval('document.querySelector("#mct-why").textContent'), "")



def test_empty_caption_window():
    """An old, empty caption window left in the page must not win the attach."""
    print("Smoke: two caption windows, one of them empty")
    c = quickjs.Context()
    c.eval(DOM); c.eval(SERVICE); c.eval(PAGE)
    c.eval('detectAnswer = {lang:"en", name:"English"};')
    # The empty one first, so "take the first match" picks exactly the wrong box.
    c.eval("addEmptyCaptionWindow(); buildPage();")
    c.eval(SRC); tick(c, 200)

    said = ["Good morning, this should reach the panel despite the empty window.",
            "And so should this second sentence, spoken shortly afterwards."]
    for t in said:
        c.eval(f'say("Sarah", "{t}")')
        tick(c, 1500)

    got = json.loads(rows(c))
    check("  captured anyway", len(got), 2)
    check("  attached to the one with captions in it",
          "best of" in c.eval('logLines.map(l => l[1]).join(" ")'), True)



def test_platform_table():
    """Which platform's selectors get used, per host."""
    print("Smoke: the right platform for the host")
    src = (ROOT / "extension" / "content.js").read_text()
    i = src.index("  const PLATFORMS = [")
    table = src[i:src.index("\n  /**\n   * Is this node part of our own UI?", i)]

    for host, want in [("teams.cloud.microsoft", "teams"),
                       ("teams.microsoft.com", "teams"),
                       ("emea.teams.microsoft.com", "teams"),
                       ("teams.live.com", "teams"),
                       ("meet.google.com", "meet"),
                       ("zoom.us", "unknown"),
                       # A lookalike must not match: cloud.microsoft is matched on
                       # a boundary, so this is somebody else's domain.
                       ("nocloud.microsoft.evil.com", "unknown")]:
        # A fresh context each time: the table declares consts, and re-evaluating
        # them in one context throws rather than re-binding.
        c = quickjs.Context()
        c.eval(f'var location = {{ hostname: "{host}" }};' + table)
        check(f"  {host}", c.eval("PLATFORM.name"), want)

    print("Smoke: every platform declares what it needs and whether it is checked")
    c = quickjs.Context()
    c.eval('var location = { hostname: "x" };' + table)
    keys = json.loads(c.eval(
        'JSON.stringify(PLATFORMS.map(p => Object.keys(p).sort()))'))
    for k in keys:
        check("  has every field",
              [x for x in ("author", "candidates", "hosts", "name", "text",
                           "verified", "window") if x not in k], [])
    # `verified` is a note about when a human last looked, or null. It is what the
    # console line reports, so a platform nobody has checked cannot pass silently
    # as one that has been.
    check("  verified is a note or null",
          bool(c.eval('PLATFORMS.every(p => p.verified === null'
                      ' || typeof p.verified === "string")')), True)


def test_google_meet_capture():
    """Meet's caption area, built from the markup a real call reported."""
    print("Smoke: Google Meet")
    c = quickjs.Context()
    c.eval(DOM); c.eval(SERVICE); c.eval(PAGE)
    c.eval('location.hostname = "meet.google.com"; detectAnswer = {lang:"en", name:"English"};')
    c.eval("buildMeetPage();")
    c.eval(SRC); tick(c, 200)

    check("  picked the Meet selectors",
          "platform: meet" in c.eval('logLines.map(l => l[1]).join(" ")'), True)

    said = ["Hello everyone, this is the first thing said on the Meet call.",
            "And here is a second sentence, spoken a little while later on.",
            "A third one, to be sure it keeps working past the opening line."]
    for t in said:
        c.eval(f'meetSay("Sarah", "{t}")')
        tick(c, 1500)

    got = json.loads(c.eval("JSON.stringify(meetRows())"))
    check("  every line captured", len(got), len(said))
    check("  the last one too", got[-1]["text"] if got else "", said[-1])
    check("  all translated", [r["text"] for r in got if not r["tr"]], [])
    check("  never let go of the container",
          "re-attaching" in c.eval('logLines.map(l => l[1]).join(" | ")'), False)

    # Known gap, asserted so it cannot be forgotten: where Meet puts the speaker's
    # name is still unknown, so lines are captured but attributed to nobody.
    check("  speaker not yet attributed", {r["speaker"] for r in got}, {""})


test_platform_table()

def test_meet_appends_to_one_line():
    """
    Google Meet does not start a new line while one person keeps talking -- it
    appends to the same one, for as long as they hold the floor.

    Taken from a real call. Translating "the line" then means retranslating a
    paragraph that grows every few seconds, so the translation rewrites itself
    under the reader. Sentences that have finished must never be touched again.
    """
    print("Smoke: Meet appends to one growing line")
    c = quickjs.Context()
    c.eval(DOM); c.eval(SERVICE); c.eval(PAGE)
    c.eval('location.hostname = "meet.google.com"; detectAnswer = {lang:"en", name:"English"};')
    c.eval("buildMeetPage();")
    c.eval(SRC); tick(c, 200)

    # One caption line, growing, exactly as Meet delivered it.
    growth = [
        "Hello, everyone.",
        "Hello, everyone. Uh, my name is Mohammad.",
        "Hello, everyone. Uh, my name is Mohammad. And let's stay to join other person.",
        "Hello, everyone. Uh, my name is Mohammad. And let's stay to join other person. "
        "Other sentence.",
        "Hello, everyone. Uh, my name is Mohammad. And let's stay to join other person. "
        "Other sentence. Uh, after stop and speed again.",
    ]
    seen = []
    for step in growth:
        c.eval(f'meetSay("Mohammad", {json.dumps(step)}, "one")')
        tick(c, 2500)
        seen.append(json.loads(c.eval("JSON.stringify(meetRows())")))

    final = seen[-1]
    check("  one sentence per row", [r["text"] for r in final], [
        "Hello, everyone.",
        "Uh, my name is Mohammad.",
        "And let's stay to join other person.",
        "Other sentence.",
        "Uh, after stop and speed again.",
    ])

    print("Smoke: and a finished sentence is never re-translated")
    # The whole point. Each snapshot's rows must be a prefix of the next: rows
    # only ever get added, never rewritten.
    for before, after in zip(seen, seen[1:]):
        earlier = [(r["text"], r["tr"]) for r in before]
        later = [(r["text"], r["tr"]) for r in after][:len(earlier)]
        check(f"  {len(earlier)} row(s) unchanged by the next growth", later, earlier)

    print("Smoke: each sentence was translated exactly once")
    asked = json.loads(c.eval(
        "JSON.stringify(calls.filter(x => x.path === '/translate').map(x => x.body.text))"))
    check("  no sentence sent twice", len(asked), len(set(asked)))
    # The whole paragraph is never sent -- that was the bug.
    check("  the growing paragraph was never sent",
          [a for a in asked if a.count(".") > 1], [])


test_meet_appends_to_one_line()
test_google_meet_capture()
test_empty_caption_window()
test_stalled_capture_is_visible()
test_decoy_caption_text()
test_no_caption_window()
test_service_down()
test_collapse_and_tabs()
test_captions_move()
test_reconnect_button()
test_no_reattach_thrash()
test_summary_round_trip()
test_filter_survives_summary()
test_captions_reach_the_panel()
test_it_keeps_going()
test_guess_when_nothing_is_configured()
test_detection_note()
test_passthrough()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
