"""
Meeting transcripts, appended to a Markdown file on disk.

Why the server and not the extension: a browser extension cannot write to a path
of your choosing. The best it could do is drop a file in Downloads, and it cannot
append — it would have to rewrite the whole transcript every time. The companion
already runs on the same machine and has a real filesystem, so it does the writing
and tells the panel where the file is.

Append-only by design. The extension sends a segment only once it is *sealed*
(see SEAL_MS there): live captions revise themselves for a few seconds after they
first appear, and rewriting lines already on disk would mean holding the whole
file in memory and truncating it on every flush. Waiting instead costs nothing —
you are reading this file after the meeting, not during it.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path

from .config import settings

# Session ids become filenames, so they are whitelisted rather than escaped.
# Anything outside this set is rejected — a session id arrives over HTTP from a
# page we do not control, and "../../.ssh/authorized_keys" is a valid string.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class TranscriptError(Exception):
    pass


class _Session:
    """What we need to remember between flushes of one meeting."""

    def __init__(self, path: Path):
        self.path = path
        self.written: set[str] = set()   # segment ids, so a retried flush cannot duplicate
        self.last_speaker: str | None = None
        self.count = 0


_lock = threading.Lock()
_sessions: dict[str, _Session] = {}


def directory() -> Path:
    """Where transcripts go. Created on demand, not at import time."""
    return Path(settings.transcript_dir).expanduser().resolve()


def _open_session(session_id: str, started_at: str, target_lang: str) -> _Session:
    if not _SAFE_ID.match(session_id):
        raise TranscriptError("bad session id")

    root = directory()
    path = (root / f"{session_id}.md").resolve()
    # Belt and braces: even with the whitelist above, never write outside the root.
    if root not in path.parents:
        raise TranscriptError("path escapes transcript directory")

    root.mkdir(parents=True, exist_ok=True)

    fresh = not path.exists()
    sess = _Session(path)
    if fresh:
        header = (
            f"# Meeting transcript\n\n"
            f"- **Started:** {started_at or datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"- **Source:** Microsoft Teams live captions\n"
            f"- **Translated into:** {target_lang}\n\n"
            f"---\n\n"
        )
        path.write_text(header, encoding="utf-8")
    return sess


def _format(seg: dict, sess: _Session) -> str:
    """
    One segment as Markdown.

    The speaker name is only written when it changes. A meeting is mostly one
    person talking for several lines in a row, and repeating the name above every
    line turns a readable transcript into a wall of headings.
    """
    out = []
    speaker = (seg.get("speaker") or "").strip() or "Unknown speaker"
    if speaker != sess.last_speaker:
        out.append(f"\n**{speaker}**\n")
        sess.last_speaker = speaker

    ts = (seg.get("t") or "")[11:19]          # HH:MM:SS out of an ISO timestamp
    text = " ".join((seg.get("text") or "").split())
    out.append(f"\n`{ts}` {text}\n")

    tr = " ".join((seg.get("translation") or "").split())
    if tr:
        # Blockquote, so the translation is visually subordinate to the original
        # and stays readable when the target language is right-to-left.
        out.append(f"> {tr}\n")
    return "".join(out)


def append(session_id: str, started_at: str, segments: list[dict]) -> dict:
    """
    Append sealed segments to this session's file. Returns where it went.

    Safe to call with segments already written: ids seen before are skipped, so a
    flush that failed halfway and is retried does not duplicate anything.
    """
    if not settings.transcript_enabled:
        raise TranscriptError("transcript saving is disabled")

    with _lock:
        sess = _sessions.get(session_id)
        if sess is None:
            sess = _open_session(session_id, started_at, settings.target_lang_name)
            _sessions[session_id] = sess

        chunks, added = [], 0
        for seg in segments:
            sid = str(seg.get("id") or "")
            if not sid or sid in sess.written:
                continue
            chunks.append(_format(seg, sess))
            sess.written.add(sid)
            added += 1

        if chunks:
            with sess.path.open("a", encoding="utf-8") as fh:
                fh.write("".join(chunks))
            sess.count += added

        return {
            "path": str(sess.path),
            "directory": str(sess.path.parent),
            "written": added,
            "total": sess.count,
            "bytes": sess.path.stat().st_size,
        }
