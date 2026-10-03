"""Runtime settings. Everything comes from environment variables so the same
code runs on a laptop, on Render, or in a container without edits."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (no extra dependency). Real env vars always win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


ROOT = Path(__file__).resolve().parent.parent
_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("CONTAGION_MODEL", "claude-sonnet-4-5"))
    anthropic_base_url: str = field(default_factory=lambda: _env("ANTHROPIC_API_URL", "https://api.anthropic.com"))

    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-4o"))
    perplexity_api_key: str = field(default_factory=lambda: _env("PERPLEXITY_API_KEY"))
    perplexity_model: str = field(default_factory=lambda: _env("PERPLEXITY_MODEL", "sonar"))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-flash-latest"))

    profound_api_key: str = field(default_factory=lambda: _env("PROFOUND_API_KEY"))
    profound_category_id: str = field(default_factory=lambda: _env("PROFOUND_CATEGORY_ID"))
    profound_base_url: str = field(default_factory=lambda: _env("PROFOUND_API_URL", "https://api.tryprofound.com"))

    youtube_api_key: str = field(default_factory=lambda: _env("YOUTUBE_API_KEY"))

    data_dir: Path = field(default_factory=lambda: Path(_env("CONTAGION_DATA_DIR", str(ROOT / "data"))))
    user_agent: str = field(default_factory=lambda: _env("CONTAGION_USER_AGENT", "contagion-rumor-trace/0.1 (hackathon research tool)"))
    max_items_per_source: int = field(default_factory=lambda: int(_env("CONTAGION_MAX_ITEMS", "40")))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
