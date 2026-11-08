"""
Replay real meeting transcripts through different translation setups and score them.

    server/.venv/bin/python tools/eval_translation.py            # uses server/.env for the key

Every setup is replayed as if live: line N sees only lines < N (and, where the
setup uses them, its OWN earlier translations). Each speaker turn is then scored
blind by a strong judge model, all setups side by side in shuffled order.

Setups
  A  what the live app produced (read from the transcript file)      gpt-4o-mini, old prompt
  B  gpt-4o-mini, NEW prompt + richer context
  C  strong model, OLD prompt                                          model only
  D  strong model, NEW prompt + richer context                         model + context
  E  turn-level: a whole speaker turn retranslated when it ends        (strong, new prompt)
  F  whole stretch in one call -- "paste it into ChatGPT afterwards"   (reference ceiling)
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import pathlib
import random
import re
import statistics
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import httpx  # noqa: E402

from app.config import Settings  # noqa: E402

# server/.env, wherever this is run from (Settings alone reads ./.env).
settings = Settings(_env_file=ROOT / "server" / ".env")
from app.translate import SYSTEM_TEMPLATE  # noqa: E402  -- the prompt live uses today

if not settings.openai_api_key:
    sys.exit("OPENAI_API_KEY is not set in server/.env")

SAMPLES = ROOT / "sample_translate_meeting"
OUT = SAMPLES / "eval"
FAST_MODEL = "gpt-4o-mini"
STRONG_MODELS = ["gpt-5.4-mini", "gpt-6-sol"]
JUDGE = "gpt-6.1-sol"
WINDOW = 40                        # caption lines per stretch
TARGET = "Persian (Farsi)"

CLIENT = httpx.Client(timeout=180, limits=httpx.Limits(max_connections=40))
HEAD = {"Authorization": f"Bearer {settings.openai_api_key}"}
_print = threading.Lock()
USAGE: dict[str, dict] = {}          # model -> {calls, in, out, cached}
_usage_lock = threading.Lock()


def log(*a):
    with _print:
        print(*a, flush=True)


# ------------------------------------------------------------------ transcripts

def parse(path: pathlib.Path) -> list[dict]:
    """[{speaker, t, en, fa}] in order; notes skipped."""
    out, speaker, cur = [], "", None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        m = re.fullmatch(r"\*\*(.+?)\*\*", line)
        if m:
            speaker = m.group(1); continue
        m = re.match(r"`(\d\d:\d\d:\d\d)` (.*)", line)
        if m:
            cur = {"speaker": speaker, "t": m.group(1), "en": m.group(2).strip(), "fa": ""}
            out.append(cur); continue
        if line.startswith("> ") and cur is not None:
            cur["fa"] = (cur["fa"] + " " + line[2:]).strip()
    return [r for r in out if r["en"]]


def secs(t: str) -> int:
    h, m, s = map(int, t.split(":")); return h * 3600 + m * 60 + s


def turns_of(lines: list[dict]) -> list[list[int]]:
    """Consecutive lines by one speaker, broken on a pause > 10s."""
    turns: list[list[int]] = []
    for i, r in enumerate(lines):
        if turns and lines[turns[-1][-1]]["speaker"] == r["speaker"] \
                and secs(r["t"]) - secs(lines[turns[-1][-1]]["t"]) <= 10:
            turns[-1].append(i)
        else:
            turns.append([i])
    return turns


# ------------------------------------------------------------------ model calls

def chat(model: str, system: str, user: str, effort: str | None = "none",
         max_tokens: int = 1500) -> tuple[str, float]:
    body = {"model": model, "max_completion_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if model.startswith("gpt-4"):
        body["temperature"] = 0.2
    elif effort:
        body["reasoning_effort"] = effort
    last = "no attempt"
    for attempt in range(4):
        t0 = time.perf_counter()
        try:
            r = CLIENT.post("https://api.openai.com/v1/chat/completions", json=body, headers=HEAD)
        except httpx.HTTPError as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 + attempt * 3); continue
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code == 200:
            d = r.json()
            u = d.get("usage", {})
            with _usage_lock:
                a = USAGE.setdefault(model, {"calls": 0, "in": 0, "out": 0, "cached": 0})
                a["calls"] += 1
                a["in"] += u.get("prompt_tokens", 0)
                a["out"] += u.get("completion_tokens", 0)
                a["cached"] += (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            return d["choices"][0]["message"]["content"].strip(), ms
        if r.status_code == 400 and "reasoning_effort" in body and effort == "none":
            body["reasoning_effort"] = "low"; continue
        last = f"{r.status_code} {r.text[:200]}"
        time.sleep(2 + attempt * 3)
    raise RuntimeError(f"{model}: {last}")


# ------------------------------------------------------------------ prompts

def old_prompt(lines, i):
    """Exactly what the live app sends today: 3 English lines, no speakers."""
    system = SYSTEM_TEMPLATE.format(
        target=TARGET,
        source_hint="\n\nThe captions appear to be in English, but trust the text in front of you over that.",
        glossary=settings.glossary)
    ctx = [l["en"] for l in lines[max(0, i - 3):i]]
    if ctx:
        user = ("Earlier in the meeting (context only, do NOT translate):\n"
                + "\n".join(f"- {c}" for c in ctx) + f"\n\nCURRENT SEGMENT:\n{lines[i]['en']}")
    else:
        user = f"CURRENT SEGMENT:\n{lines[i]['en']}"
    return system, user


NEW_SYSTEM = """\
You translate live captions of a software team's meeting into {target}, one caption at
a time, for a developer who reads {target} far better than spoken English.

The captions come from automatic speech recognition (ASR). Expect:
- MISHEARD WORDS. Technical terms are often heard as ordinary words that sound alike.
  When a phrase makes no sense in a software meeting, translate what the speaker most
  plausibly SAID, judging from the conversation so far and the glossary -- not the
  literal mishearing. Do not do this when the literal text makes sense.
- Captions cut mid-sentence. A caption may continue the previous one or stop before
  its verb. Translate it so it reads naturally as a continuation of what came before.
  If the verb has not been said yet, end with "…" rather than inventing one.
- Repetitions, false starts and fillers ("I I", "so, so", "you know"): drop the noise
  and keep the meaning.

Write natural, spoken {target} -- how a {target}-speaking developer would say it in a
meeting -- not a word-for-word rendering. Keep these in English: {glossary}, plus
product names, people's names, code identifiers, table names and numbers.
Use the same {target} wording as your earlier translations for the same terms.

Output ONLY the translation of the CURRENT caption. Never translate or repeat the
earlier ones; they are there so you understand it.
"""

NEW_CTX = 12


def new_prompt(lines, i, own):
    """
    English conversation first, then the reader's recent translations as a block
    of their own. Interleaving them ("[your Persian: ...]" under each line) made
    small models copy the marker into their answer.
    """
    system = NEW_SYSTEM.format(target=TARGET, glossary=settings.glossary)
    lo = max(0, i - NEW_CTX)
    ctx = [f"{lines[j]['speaker']}: {lines[j]['en']}" for j in range(lo, i)]
    prior = [own[j] for j in range(max(0, i - 6), i) if own.get(j)]
    user = ""
    if ctx:
        user += "Conversation so far (English, context only):\n" + "\n".join(ctx) + "\n\n"
    if prior:
        user += (f"How you translated the last few captions (reuse the same terms):\n"
                 + "\n".join(prior) + "\n\n")
    user += f"CURRENT CAPTION -- {lines[i]['speaker']}:\n{lines[i]['en']}\n\nTranslation:"
    return system, user


TURN_SYSTEM = NEW_SYSTEM.replace(
    "one caption at\na time", "one speaker's turn at\na time").replace(
    "Output ONLY the translation of the CURRENT caption. Never translate or repeat the\n"
    "earlier ones; they are there so you understand it.",
    "The CURRENT TURN is several captions from one speaker, cut where they paused. Join\n"
    "them into well-formed {target} sentences.\n\nOutput ONLY the translation of the "
    "CURRENT TURN. Never translate the earlier conversation.")


def turn_prompt(lines, turn, prior_turns_fa):
    system = TURN_SYSTEM.format(target=TARGET, glossary=settings.glossary)
    first = turn[0]
    ctx = [f"{lines[j]['speaker']}: {lines[j]['en']}" for j in range(max(0, first - NEW_CTX), first)]
    user = ""
    if ctx:
        user = "Conversation so far (context only):\n" + "\n".join(ctx) + "\n\n"
    if prior_turns_fa:
        user += f"Your recent {TARGET} (keep terms consistent):\n" + "\n".join(prior_turns_fa[-3:]) + "\n\n"
    user += f"CURRENT TURN -- {lines[first]['speaker']}:\n" + " ".join(lines[j]["en"] for j in turn)
    return system, user


WHOLE_SYSTEM = f"""\
Below is an automatic transcript of part of a software team's meeting, with speech
recognition errors. Translate it into natural, fluent {TARGET} for a developer who
follows {TARGET} far better than English. Fix misheard technical words from context.
Keep in English: {{glossary}}, names, identifiers and numbers.

Return JSON only: {{{{"turns": ["<{TARGET} for turn 1>", "<turn 2>", ...]}}}} -- exactly one
string per numbered turn, in order.
"""


# ------------------------------------------------------------------ setups

def run_lines(lines, model, prompt, label):
    out, ms = {}, []
    for i in range(len(lines)):
        if prompt == "old":
            s, u = old_prompt(lines, i)
        else:
            s, u = new_prompt(lines, i, out)
        out[i], t = chat(model, s, u)
        ms.append(t)
    log(f"    done {label}")
    return out, ms


def run_turns(lines, turns, model, label):
    out, ms, prior = {}, [], []
    for k, turn in enumerate(turns):
        s, u = turn_prompt(lines, turn, prior)
        out[k], t = chat(model, s, u)
        prior.append(out[k]); ms.append(t)
    log(f"    done {label}")
    return out, ms


def run_whole(lines, turns, model):
    body = "\n".join(f"{k + 1}. {lines[t[0]]['speaker']}: " + " ".join(lines[j]["en"] for j in t)
                     for k, t in enumerate(turns))
    txt, ms = chat(model, WHOLE_SYSTEM.format(glossary=settings.glossary), body, effort="low",
                   max_tokens=16000)
    txt = txt[txt.find("{"): txt.rfind("}") + 1]
    arr = json.loads(txt)["turns"]
    log("    done F")
    return {k: (arr[k] if k < len(arr) else "") for k in range(len(turns))}, [ms]


# ------------------------------------------------------------------ judging

JUDGE_SYSTEM = f"""\
You are a professional English->{TARGET} interpreter who works with software teams.
You grade live caption translations for a {TARGET}-speaking developer in the meeting.

For the CURRENT TURN you get the ASR English (with recognition errors), the
conversation before it, and several candidate translations in random order.

Score each candidate 1-10 on how well a {TARGET} reader would understand what the speaker
actually MEANT:
  10  meaning fully right (incl. correctly recovering misheard technical terms), natural
   7  meaning right, somewhat literal or clumsy
   4  partly wrong, or a misheard term translated literally (e.g. a homophone nonsense)
   1  wrong / misleading / unreadable
Penalise invented content. Do not reward length.

Return JSON only: {{"scores": {{"<id>": <int>, ...}}, "notes": "<one short sentence>"}}
"""


def judge_turn(lines, turns, k, cands):
    turn = turns[k]
    first = turn[0]
    ctx = "\n".join(f"{lines[j]['speaker']}: {lines[j]['en']}" for j in range(max(0, first - 10), first))
    ids = list(cands)
    random.Random(f"{first}").shuffle(ids)
    letters = {cid: chr(ord("P") + n) for n, cid in enumerate(ids)}
    body = (f"Conversation before (context):\n{ctx or '(start)'}\n\n"
            f"CURRENT TURN -- {lines[first]['speaker']}:\n" + " ".join(lines[j]['en'] for j in turn)
            + "\n\nCandidates:\n" + "\n".join(f"[{letters[c]}] {cands[c]}" for c in ids))
    txt, _ = chat(JUDGE, JUDGE_SYSTEM, body, effort="medium", max_tokens=4000)
    d = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    back = {v: k2 for k2, v in letters.items()}
    return {back[L]: int(s) for L, s in d["scores"].items() if L in back}, d.get("notes", "")


# ------------------------------------------------------------------ main

def pick_windows():
    """One technical stretch from each of three meetings, after detection settled."""
    picks = [("2026-10-19_0923_qvhw.md", 0), ("2026-10-15_0929_a501.md", 120),
             ("2026-10-29_0929_x2a8.md", 150)]
    out = []
    for name, start in picks:
        lines = parse(SAMPLES / name)
        out.append((name, lines[start:start + WINDOW]))
    return out


def main():
    OUT.mkdir(exist_ok=True)
    windows = pick_windows()
    results = []
    with cf.ThreadPoolExecutor(max_workers=24) as pool:
        jobs = {}
        for wi, (name, lines) in enumerate(windows):
            turns = turns_of(lines)
            log(f"{name}: {len(lines)} lines, {len(turns)} turns")
            jobs[(wi, "B")] = pool.submit(run_lines, lines, FAST_MODEL, "new", f"{name} B")
            for m in STRONG_MODELS:
                jobs[(wi, f"C:{m}")] = pool.submit(run_lines, lines, m, "old", f"{name} C {m}")
                jobs[(wi, f"D:{m}")] = pool.submit(run_lines, lines, m, "new", f"{name} D {m}")
                jobs[(wi, f"E:{m}")] = pool.submit(run_turns, lines, turns, m, f"{name} E {m}")
            jobs[(wi, "F")] = pool.submit(run_whole, lines, turns, "gpt-5.5")
        got = {k: f.result() for k, f in jobs.items()}

        latency: dict[str, list[float]] = {}
        judge_jobs = []
        for wi, (name, lines) in enumerate(windows):
            turns = turns_of(lines)
            per_turn = []
            for k, turn in enumerate(turns):
                c = {"A": " ".join(lines[j]["fa"] for j in turn)}
                for key in [x for (w, x) in got if w == wi]:
                    out, ms = got[(wi, key)]
                    latency.setdefault(key.split(":")[0] + (":" + key.split(":")[1] if ":" in key else ""), []).extend(ms)
                    if key.startswith(("B", "C", "D")):
                        c[key] = " ".join(out[j] for j in turn)
                    else:
                        c[key] = out[k]
                per_turn.append(c)
                judge_jobs.append((wi, k, pool.submit(judge_turn, lines, turns, k, c)))
            results.append({"name": name, "lines": lines, "turns": turns, "cands": per_turn})
        for wi, k, f in judge_jobs:
            scores, notes = f.result()
            results[wi]["cands"][k] = {"text": results[wi]["cands"][k], "scores": scores, "notes": notes}
        log("judged")

    # ---- summarise
    keys = sorted({k for r in results for c in r["cands"] for k in c["text"]})
    summary = {}
    for key in keys:
        sc = [c["scores"].get(key) for r in results for c in r["cands"] if c["scores"].get(key)]
        # weight by turn length so a one-word "OK" counts less than a paragraph
        w = [len(" ".join(r["lines"][j]["en"] for j in r["turns"][i]))
             for r in results for i, c in enumerate(r["cands"]) if c["scores"].get(key)]
        lat = latency.get(key, [])
        summary[key] = {
            "mean": round(statistics.mean(sc), 2) if sc else None,
            "weighted": round(sum(a * b for a, b in zip(sc, w)) / sum(w), 2) if sc else None,
            "low(<=4)": sum(1 for s in sc if s <= 4),
            "n": len(sc),
            "p50_ms": round(statistics.median(lat)) if lat else None,
            "p90_ms": round(sorted(lat)[int(len(lat) * .9) - 1]) if len(lat) > 9 else None,
        }
    (OUT / "results.json").write_text(json.dumps({"summary": summary, "results": results},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


# Published per-1M-token prices (input, output), from third-party trackers,
# Nov 2026 -- confirm on openai.com/api/pricing before relying on them.
PRICES = {
    "gpt-4o-mini": (0.15, 0.60), "gpt-4.1-mini": (0.40, 1.60), "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.4-mini": (0.75, 4.50), "gpt-5.6-luna": (0.20, 1.20), "gpt-6-luna": (0.10, 0.50),
    "gpt-6-sol": (2.00, 10.00),
}


def compare(models, turn_model):
    """New prompt (D) across `models`, plus turn retranslation (E) on `turn_model`,
    judged together with what the live app produced (A)."""
    OUT.mkdir(exist_ok=True)
    windows = pick_windows()
    results, latency = [], {}
    with cf.ThreadPoolExecutor(max_workers=32) as pool:
        jobs = {}
        for wi, (name, lines) in enumerate(windows):
            turns = turns_of(lines)
            for m in models:
                jobs[(wi, f"D:{m}")] = pool.submit(run_lines, lines, m, "new", f"{name} D {m}")
            jobs[(wi, f"E:{turn_model}")] = pool.submit(run_turns, lines, turns, turn_model,
                                                        f"{name} E {turn_model}")
        got = {k: f.result() for k, f in jobs.items()}
        line_usage = {m: dict(v) for m, v in USAGE.items()}   # before the judge adds to it

        judge_jobs = []
        for wi, (name, lines) in enumerate(windows):
            turns = turns_of(lines)
            per_turn = []
            for k, turn in enumerate(turns):
                c = {"A": " ".join(lines[j]["fa"] for j in turn)}
                for (w, key), (out, ms) in got.items():
                    if w != wi:
                        continue
                    latency.setdefault(key, []).extend(ms) if k == 0 else None
                    c[key] = " ".join(out[j] for j in turn) if key.startswith("D") else out[k]
                per_turn.append(c)
                judge_jobs.append((wi, k, pool.submit(judge_turn, lines, turns, k, c)))
            results.append({"name": name, "lines": lines, "turns": turns, "cands": per_turn})
        for wi, k, f in judge_jobs:
            scores, notes = f.result()
            results[wi]["cands"][k] = {"text": results[wi]["cands"][k], "scores": scores, "notes": notes}

    n_lines = sum(len(l) for _, l in windows)
    rows = []
    for key in sorted({k for r in results for c in r["cands"] for k in c["text"]}):
        sc, w = [], []
        for r in results:
            for i, c in enumerate(r["cands"]):
                if key in c["scores"]:
                    sc.append(c["scores"][key])
                    w.append(len(" ".join(r["lines"][j]["en"] for j in r["turns"][i])))
        lat = sorted(latency.get(key, []))
        model = key.split(":", 1)[1] if ":" in key else ""
        u = line_usage.get(model)
        cost_hr = None
        if u and model in PRICES and key.startswith("D"):
            # One meeting-hour is ~400 caption lines in these transcripts.
            pin, pout = PRICES[model]
            per_line = (u["in"] * pin + u["out"] * pout) / 1e6 / max(u["calls"], 1)
            cost_hr = round(per_line * 400, 3)
        rows.append({
            "setup": key, "weighted": round(sum(a * b for a, b in zip(sc, w)) / sum(w), 2),
            "mean": round(statistics.mean(sc), 2), "low": sum(1 for s in sc if s <= 4),
            "p50": round(statistics.median(lat)) if lat else None,
            "p90": round(lat[int(len(lat) * .9) - 1]) if len(lat) > 9 else None,
            "usd_per_hour": cost_hr,
            "in_tok_per_line": round(u["in"] / u["calls"]) if u and key.startswith("D") else None,
        })
    rows.sort(key=lambda r: -r["weighted"])
    (OUT / "compare.json").write_text(json.dumps({"rows": rows, "results": results, "usage": USAGE,
                                                  "lines": n_lines}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    print(json.dumps(rows, indent=1))


# ------------------------------------------------------------------ the real app

def run_app(lines, model):
    """
    Replay through the app's own code: app.translate's prompt builders, and the
    extension's turn rules (same speaker; ends on a pause, at 6 lines or 700
    characters; one-line turns keep their line translation).

    Returns (line translations, what the reader finally sees per line index group).
    """
    from app import translate as T   # noqa: E402 -- the code that ships
    own, ms = {}, []
    history = []
    for i, l in enumerate(lines):
        s, u = T.build_messages(l["en"], [], TARGET, "English", history=history[-12:],
                                speaker=l["speaker"])
        own[i], t = chat(model, s, u); ms.append(t)
        history.append({"speaker": l["speaker"], "text": l["en"], "translation": own[i]})

    # Turns as the extension forms them.
    app_turns, cur = [], []
    for i, l in enumerate(lines):
        if cur:
            prev = lines[cur[-1]]
            chars = sum(len(lines[j]["en"]) for j in cur)
            # The extension keeps a turn open while the speaker's next line is
            # still being spoken, and closes it after 3 s with nothing new. A
            # Teams line is itself 4-6 s of speech, so a real 3 s pause shows up
            # here as a gap of about 9 s between line timestamps.
            if prev["speaker"] != l["speaker"] or secs(l["t"]) - secs(prev["t"]) > 9 \
                    or len(cur) >= 6 or chars >= 700:
                app_turns.append(cur); cur = []
        cur.append(i)
    if cur:
        app_turns.append(cur)

    final, done = {}, []
    for turn in app_turns:
        if len(turn) < 2:
            final[tuple(turn)] = own[turn[0]]; continue
        hist = [{"speaker": lines[j]["speaker"], "text": lines[j]["en"], "translation": own[j]}
                for j in range(max(0, turn[0] - 12), turn[0])]
        s, u = T.build_turn_messages([lines[j]["en"] for j in turn], lines[turn[0]]["speaker"],
                                     TARGET, "English", history=hist, prior_turns=done[-3:])
        out, t = chat(model, s, u); ms.append(t)
        final[tuple(turn)] = out; done.append(out)
    log(f"    done app {model}")
    return own, final, ms


def app_check(models):
    """The shipped prompts and turn rules, per model, against what the live app produced."""
    OUT.mkdir(exist_ok=True)
    windows = pick_windows()
    results, latency = [], {}
    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        jobs = {(wi, m): pool.submit(run_app, lines, m)
                for wi, (_, lines) in enumerate(windows) for m in models}
        got = {k: f.result() for k, f in jobs.items()}
        usage = {m: dict(v) for m, v in USAGE.items()}
        judge_jobs = []
        for wi, (name, lines) in enumerate(windows):
            turns = turns_of(lines)
            per_turn = []
            for k, turn in enumerate(turns):
                c = {"A (live today)": " ".join(lines[j]["fa"] for j in turn)}
                for m in models:
                    own, final, ms = got[(wi, m)]
                    if k == 0:
                        latency.setdefault(m, []).extend(ms)
                    c[f"lines only: {m}"] = " ".join(own[j] for j in turn)
                    c[f"lines + turns: {m}"] = " ".join(v for key, v in sorted(final.items())
                                                        if key[0] in turn)
                per_turn.append(c)
                judge_jobs.append((wi, k, pool.submit(judge_turn, lines, turns, k, c)))
            results.append({"name": name, "lines": lines, "turns": turns, "cands": per_turn})
        for wi, k, f in judge_jobs:
            scores, notes = f.result()
            results[wi]["cands"][k] = {"text": results[wi]["cands"][k], "scores": scores, "notes": notes}

    n = sum(len(l) for _, l in windows)
    rows = []
    for key in sorted({k for r in results for c in r["cands"] for k in c["text"]}):
        sc, w = [], []
        for r in results:
            for i, c in enumerate(r["cands"]):
                if key in c["scores"]:
                    sc.append(c["scores"][key]); w.append(len(" ".join(r["lines"][j]["en"] for j in r["turns"][i])))
        model = key.split(": ", 1)[1] if ": " in key else ""
        cost = None
        if model in usage and model in PRICES and key.startswith("lines + turns"):
            pin, pout = PRICES[model]
            cost = round((usage[model]["in"] * pin + usage[model]["out"] * pout) / 1e6 / n * 400, 3)
        rows.append({"setup": key, "weighted": round(sum(a * b for a, b in zip(sc, w)) / sum(w), 2),
                     "low": sum(1 for x in sc if x <= 4), "usd_per_hour": cost})
    rows.sort(key=lambda r: -r["weighted"])
    (OUT / "app_check.json").write_text(json.dumps({"rows": rows, "results": results, "usage": usage,
                                                    "latency": {m: sorted(v) for m, v in latency.items()}},
                                                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rows, indent=1))


if __name__ == "__main__":
    if sys.argv[1:2] == ["app"]:
        app_check(sys.argv[2:] or ["gpt-5.6-luna", "gpt-6-luna"])
    elif sys.argv[1:2] == ["compare"]:
        compare(["gpt-4o-mini", "gpt-4.1-mini", "gpt-5.4-nano", "gpt-5.6-luna", "gpt-6-luna",
                 "gpt-5.4-mini", "gpt-6-sol"], turn_model="gpt-6-luna")
    else:
        main()
