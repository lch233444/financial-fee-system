from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
APP_VERSION = "0.2.25"


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller onedir stores bundled data below ``_internal`` and
        # exposes that resource directory through ``sys._MEIPASS``.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


def installation_root() -> Path:
    """Directory containing the user-facing executable and bundled OCR."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return application_root()


def default_ai_codex_home() -> Path:
    """Return the private Codex home used only by statement extraction.

    The normal desktop/CLI Codex home may contain global AGENTS.md, plugins,
    hooks, and other user customizations.  Reusing it would make a financial
    extraction turn inherit instructions outside this application's control.
    """

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data and local_app_data.strip():
        return Path(local_app_data) / "FinancialFeeSystem" / "LunaCodexHome"
    try:
        return Path.home() / ".financial-fee-system" / "luna-codex-home"
    except (OSError, RuntimeError):
        return application_root() / ".luna-codex-home"


class Settings(BaseSettings):
    app_name: str = "金融计划收费计算系统"
    host: str = "127.0.0.1"
    port: int = 8000
    data_root: Path = Path(os.getenv("FINANCIAL_DATA_ROOT", application_root() / "data"))
    template_path: Path = Path(
        os.getenv("FINANCIAL_EXCEL_TEMPLATE", application_root() / "新收费计划计算纯净版模板.xlsx")
    )
    frontend_dist: Path = application_root() / "frontend" / "dist"
    tesseract_cmd: str | None = os.getenv("TESSERACT_CMD")
    # Codex is intentionally authenticated through the user's managed
    # ChatGPT session.  This application never accepts or stores an API key.
    codex_cmd: str | None = os.getenv("FINANCIAL_CODEX_CMD")
    # This must not default to the user's normal ~/.codex directory. The AI
    # process has its own login so global AGENTS.md and desktop extensions can
    # never enter a statement extraction prompt.
    codex_home: Path = Field(default_factory=default_ai_codex_home)
    codex_status_timeout_seconds: float = 10.0
    codex_recognition_timeout_seconds: float = 120.0
    testing: bool = False
    model_config = SettingsConfigDict(env_prefix="FINANCIAL_", env_file=".env", extra="ignore")

    @field_validator("host")
    @classmethod
    def require_loopback_host(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in LOOPBACK_HOSTS:
            raise ValueError("FINANCIAL_HOST只允许127.0.0.1、localhost或::1")
        return normalized

    @field_validator("codex_home")
    @classmethod
    def require_dedicated_codex_home(cls, value: Path) -> Path:
        candidate = value.expanduser().resolve()
        default_homes: list[Path] = []
        for name in ("USERPROFILE", "HOME"):
            profile = os.getenv(name)
            if profile and profile.strip():
                default_homes.append((Path(profile) / ".codex").resolve())
        try:
            default_homes.append((Path.home() / ".codex").resolve())
        except (OSError, RuntimeError):
            pass
        if any(os.path.normcase(str(candidate)) == os.path.normcase(str(home)) for home in default_homes):
            raise ValueError("FINANCIAL_CODEX_HOME必须使用独立目录，不能指向桌面Codex的~/.codex")
        return candidate

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK_HOSTS

    @property
    def database_path(self) -> Path:
        return self.data_root / "database" / "financial_system.sqlite3"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def codex_recognition_workspace(self) -> Path:
        """Project-isolated working directory for statement extraction turns."""

        return self.codex_home / "recognition-workspace"

    def ensure_directories(self) -> None:
        for path in (
            self.data_root / "database",
            self.data_root / "statement_imports",
            self.data_root / "attachments",
            self.data_root / "output" / "excel",
            self.data_root / "output" / "pdf",
            self.data_root / "backups",
            self.data_root / "tmp",
            self.data_root / "tmp" / "ai-codex",
            self.codex_home,
            self.codex_recognition_workspace,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
