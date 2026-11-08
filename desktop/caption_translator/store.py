"""
Where the app keeps its settings, and how a change reaches the running translator.

The file is the same KEY=value format as server/.env, so anything documented in
.env.example works here too, and a developer can copy one into the other. It
lives in the user's profile -- an installed program cannot write beside itself,
and settings must survive replacing the .exe with a newer one.

Saving applies the change immediately: the settings object the server reads is
updated in place, so a new key or model is used by the very next caption. No
restart, and nothing in flight is dropped.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

APP_DIR_NAME = "CaptionTranslator"

# What the settings page may change, and how each is typed. Everything else in
# the file is kept as it is but not offered -- the page is for the handful of
# things a person actually sets.
FIELDS: dict[str, type] = {
    "provider": str,
    "openai_api_key": str,
    "openai_model": str,
    "anthropic_api_key": str,
    "anthropic_model": str,
    "ollama_model": str,
    "target_lang": str,
    "glossary": str,
    "transcript_enabled": bool,
    "transcript_dir": str,
}
SECRETS = {"openai_api_key", "anthropic_api_key"}
PROVIDERS = ("openai", "anthropic", "ollama")

# Offered in the model picker. Any other model id can still be typed.
OPENAI_MODELS = [
    ("gpt-6-luna", "gpt-6-luna — recommended: best measured quality per cent"),
    ("gpt-5.6-luna", "gpt-5.6-luna — same quality, about twice the price"),
    ("gpt-4o-mini", "gpt-4o-mini — older, noticeably worse at meeting jargon"),
]

_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def data_dir() -> pathlib.Path:
    """%APPDATA%\\CaptionTranslator on Windows; ~/.config/caption-translator elsewhere."""
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return pathlib.Path(os.environ["APPDATA"]) / APP_DIR_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or (pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "caption-translator"


def settings_path() -> pathlib.Path:
    return data_dir() / "settings.env"


def log_path() -> pathlib.Path:
    return data_dir() / "caption-translator.log"


def read(path: pathlib.Path) -> dict[str, str]:
    """KEY=value lines -> {key_lowercase: value}. Comments and blanks ignored."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _KEY_RE.match(line)
        if m:
            v = m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[m.group(1).lower()] = v
    return out


def _render(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value).replace("\r", " ").replace("\n", " ").strip()
    return s


def write(path: pathlib.Path, changes: dict[str, object]) -> None:
    """
    Update KEY=value lines in place; append keys that were not there.

    Comments, ordering and keys the page does not know about are all kept: the
    file may have been edited by hand, and a save must not throw that away.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else [
        "# Caption Translator settings. Same format as server/.env.example.",
        "# Edited by the app's settings page; hand edits are kept.",
    ]
    pending = {k.lower(): v for k, v in changes.items()}
    for i, line in enumerate(lines):
        m = _KEY_RE.match(line)
        if m and not line.lstrip().startswith("#") and m.group(1).lower() in pending:
            k = m.group(1).lower()
            lines[i] = f"{m.group(1)}={_render(pending.pop(k))}"
    for k, v in pending.items():
        lines.append(f"{k.upper()}={_render(v)}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)          # never a half-written settings file


def mask(secret: str) -> str:
    """Enough to recognise a key, never enough to use it."""
    secret = secret or ""
    if not secret:
        return ""
    return secret[:3] + "…" + secret[-4:] if len(secret) > 10 else "…"


def clean(raw: dict) -> dict[str, object]:
    """
    Validate what the settings page sent. Unknown keys are dropped; an empty
    secret means "keep the one I have" -- the page never sees the real key, so it
    cannot send it back.
    """
    out: dict[str, object] = {}
    for k, typ in FIELDS.items():
        if k not in raw:
            continue
        v = raw[k]
        if typ is bool:
            out[k] = v in (True, "true", "1", "on", 1)
            continue
        v = _render(v if v is not None else "")
        if k in SECRETS and not v:
            continue
        if k == "provider" and v not in PROVIDERS:
            raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")
        if k.endswith("_model") and v and not re.fullmatch(r"[A-Za-z0-9._:\-/]{1,80}", v):
            raise ValueError(f"'{v}' does not look like a model name")
        if k == "target_lang" and not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", v):
            raise ValueError(f"'{v}' is not a language code")
        out[k] = v
    return out


def apply(values: dict[str, object]) -> None:
    """Push saved values into the running translator, effective on the next caption."""
    from app import providers, translate
    from app.config import settings

    for k, v in values.items():
        setattr(settings, k, v)
    if "target_lang" in values:
        from app import languages
        settings.target_lang_name = languages.name_of(str(values["target_lang"]),
                                                      settings.target_lang_name)
    # A new key or model must not be answered from the old one's connection or
    # cache: the cache is keyed by provider, not by model.
    providers._client = None
    providers._no_temperature.clear()
    providers._no_effort.clear()
    translate._cache.clear()


def current() -> dict[str, object]:
    """What the settings page shows. Secrets are masked."""
    from app.config import settings
    out = {}
    for k in FIELDS:
        v = getattr(settings, k)
        out[k] = mask(v) if k in SECRETS else v
    out["has_openai_key"] = bool(settings.openai_api_key)
    out["has_anthropic_key"] = bool(settings.anthropic_api_key)
    return out


def needs_setup() -> bool:
    """Nothing to translate with yet: no key for a cloud provider."""
    from app.config import settings
    if settings.provider == "ollama":
        return False
    key = settings.anthropic_api_key if settings.provider == "anthropic" else settings.openai_api_key
    return not key
