"""Settings for the translation companion. Loaded from .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- what we translate into ---
    # BCP-47-ish tag plus the name we put in the prompt. Persian is the first target.
    target_lang: str = "fa"
    target_lang_name: str = "Persian (Farsi)"
    source_lang_name: str = "English"

    # --- provider: openai | ollama | anthropic ---
    provider: str = "openai"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"        # verify against the current model list

    # Local option. Nothing leaves the laptop.
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"         # decent multilingual for its size

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"

    # --- quality knobs ---
    # Previous segments sent as context. Without this, pronouns and continuations
    # lose their referent: "he said that" translates blind.
    context_segments: int = 3
    # Domain vocabulary that must survive translation intact.
    # Generic default. Put your own product terms in .env — they are
    # deployment-specific, and they do not belong in a shared config default.
    glossary: str = "API,SDK,merge request,pull request,pipeline,repository,deploy"

    # --- limits ---
    max_chars: int = 1200        # one caption line is short; anything longer is suspect
    timeout_seconds: float = 20.0
    cache_size: int = 2000

    # --- server ---
    host: str = "127.0.0.1"
    port: int = 8100


settings = Settings()
