from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = BACKEND_DIR / "data" / "jobrecall.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    jobrecall_host: str = "127.0.0.1"
    jobrecall_port: int = 8765
    jobrecall_api_token: str = "jobrecall-local-dev-token-2026"
    jobrecall_db_url: str = f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"

    llm_provider: Literal["openai", "anthropic", "google", "ollama", "mock"] = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 15.0
    analysis_schema_version: str = "v1.0"

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""
    ollama_base_url: str = "http://host.docker.internal:11434"

    @property
    def resolved_db_url(self) -> str:
        url = (self.jobrecall_db_url or "").strip()
        if url.startswith("sqlite:///") and ":memory:" not in url:
            raw = url.removeprefix("sqlite:///")
            if raw.startswith("./") or raw.startswith(".\\"):
                raw = raw[2:]
            p = Path(raw)
            if not p.is_absolute():
                p = (BACKEND_DIR / p).resolve()
            return f"sqlite:///{p.as_posix()}"
        return url

    @property
    def is_ai_configured(self) -> bool:
        if self.llm_provider == "openai":
            return bool(self.openai_api_key.strip())
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key.strip())
        if self.llm_provider == "google":
            return bool(self.google_api_key.strip())
        if self.llm_provider in ("ollama", "mock"):
            return True
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
