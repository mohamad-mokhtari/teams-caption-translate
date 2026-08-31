"""
Tests for the Markdown transcript writer.

    server/.venv/bin/python tests/test_transcript.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

TMP = tempfile.mkdtemp(prefix="mct-transcript-")
os.environ["TRANSCRIPT_DIR"] = TMP

from app import transcript as T          # noqa: E402  (after the env var)

fails: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        fails.append(label)


def seg(i: str, spk: str, text: str, tr: str = "", t: str = "2026-08-31T14:32:07Z") -> dict:
    return {"id": i, "t": t, "speaker": spk, "text": text, "translation": tr}


SESSION = "2026-08-31_1432_a3f9"

print("Writing")
r = T.append(SESSION, "2026-08-31 14:32", [
    seg("1", "Ali", "Hello everyone.", "سلام به همه."),
    seg("2", "Ali", "I will share my screen.", "صفحه‌ام را به اشتراک می‌گذارم."),
    seg("3", "Sara", "Yes, we can see it.", "بله، می‌بینیم."),
])
check("segments written", r["written"], 3)
body = Path(r["path"]).read_text()
check("file is under the configured dir", Path(r["path"]).parent, Path(TMP))

print("A retried flush must not duplicate")
# The extension resends a batch whose response it never saw. If the file grew,
# every network hiccup during a meeting would double a few lines of it.
r2 = T.append(SESSION, "", [seg("3", "Sara", "Yes, we can see it.", "بله، می‌بینیم."),
                            seg("4", "Ali", "Good.", "خوب.")])
check("only the new one written", r2["written"], 1)
body = Path(r["path"]).read_text()
check("'we can see it' appears once", body.count("Yes, we can see it."), 1)

print("Speaker headings appear on change, not on every line")
check("Ali named twice (two runs)", body.count("**Ali**"), 2)
check("Sara named once", body.count("**Sara**"), 1)

print("A segment with no translation yet is still written")
T.append(SESSION, "", [seg("5", "Sara", "One moment.")])
body = Path(r["path"]).read_text()
check("original present", "One moment." in body, True)
check("no empty blockquote", "\n> \n" in body, False)

print("Reopening the session does not repeat the header")
# Happens whenever the companion is restarted mid-meeting.
T._sessions.clear()
T.append(SESSION, "2026-08-31 14:32", [seg("6", "Ali", "After a restart.", "پس از راه‌اندازی.")])
body = Path(r["path"]).read_text()
check("one header", body.count("# Meeting transcript"), 1)
check("the later line is there", "After a restart." in body, True)

print("A session id is never allowed to choose a path")
for bad in ("../../../../tmp/pwned", "a/b", "with space", "", "x" * 65, "..", "sess;rm -rf"):
    try:
        T.append(bad, "", [seg("1", "x", "y")])
        check(f"rejected {bad!r}", "accepted", "rejected")
    except T.TranscriptError:
        print(f"  PASS  rejected {bad!r}")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
print(f"(transcript written to {TMP})")
sys.exit(1 if fails else 0)
