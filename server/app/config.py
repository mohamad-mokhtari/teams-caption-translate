"""Settings for the translation companion. Loaded from .env."""
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- what we translate into ---
    # The DEFAULT only. Each reader chooses their own language in the panel and it
    # travels with every request, because one service now serves people who each
    # want a different one. This is what a brand-new install shows before anyone
    # has chosen and before the browser's own language has been consulted.
    #
    # There is deliberately no source language setting. Which language a meeting is
    # captioned in belongs to the meeting; it is detected (see /detect), never
    # configured. A stale source setting is worse than none -- the model reads
    # Spanish as English and produces confident nonsense.
    target_lang: str = "fa"
    target_lang_name: str = "Persian (Farsi)"

    # --- provider: openai | ollama | anthropic ---
    provider: str = "openai"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"        # verify against the current model list
    # gpt-5 and later only: how long the model thinks before answering. "none"
    # answers straight away (about 1 s per caption); anything higher adds
    # seconds per line. Older models that do not know "none" get it dropped.
    openai_reasoning_effort: str = "none"

    # Local option. Nothing leaves the laptop.
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"         # decent multilingual for its size

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"

    # --- quality knobs ---
    # Previous segments sent as context. Without this, pronouns and continuations
    # lose their referent: "he said that" translates blind.
    # 12 measured clearly better than 3 on real meetings: misheard terms are only
    # recoverable when the model can see what the conversation is about.
    context_segments: int = 12
    # Domain vocabulary that must survive translation intact.
    # Generic default. Put your own product terms in .env — they are
    # deployment-specific, and they do not belong in a shared config default.
    glossary: str = "API,SDK,merge request,pull request,pipeline,repository,deploy"

    # Same input, same wording. Applied to every provider. Leave it empty
    # (TEMPERATURE=) to send none at all -- a model that rejects it is also
    # detected and retried without it automatically.
    temperature: float | None = 0.2

    # --- limits ---
    max_chars: int = 1200        # one caption line is short; anything longer is suspect
    timeout_seconds: float = 20.0
    # Extra attempts after a rate limit, a 5xx, or a connection that never opened.
    # One is enough: a caption retried twice is no longer live.
    retries: int = 1
    cache_size: int = 2000

    # --- transcripts ---
    # Conversations are appended to a Markdown file so a meeting can be re-read
    # afterwards. The extension cannot write to an arbitrary path from inside the
    # browser, so this service does it — see transcript.py.
    #
    # A home-directory folder rather than one beside the code: people need to FIND
    # these files, and "next to the checkout" is not somewhere anyone looks.
    transcript_enabled: bool = True
    transcript_dir: str = "~/teams-captions"

    @field_validator("temperature", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        """`TEMPERATURE=` (or `none`) in .env means: do not send one."""
        if isinstance(v, str) and v.strip().lower() in ("", "none", "null"):
            return None
        return v

    # --- server ---
    host: str = "127.0.0.1"
    port: int = 8100


settings = Settings()
