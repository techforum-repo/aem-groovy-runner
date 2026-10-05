from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from .utils import harden_file_permissions

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    app_env: str = "development"

    # Mock mode fakes the Groovy Console round trip with generated sample
    # rows, so the whole select -> run -> format flow can be tried before any
    # AEM credential exists. Also switchable on the Settings page.
    mock_mode: bool = True

    # --- AEM author ----------------------------------------------------------
    # e.g. https://author-p12345-e67890.adobeaemcloud.com (no trailing slash)
    aem_author_url: str = ""

    groovy_console_endpoint: str = "/bin/groovyconsole/post.json"

    # A Groovy run can take minutes (e.g. ReferenceSearch per asset on a big
    # DAM folder) — this has to be generous, unlike a normal API timeout.
    http_timeout: float = 1800.0

    # Folder (relative to the project root unless absolute) runs write into:
    # <output_dir>/<session>/<script-id>/<run-stamp>/<slug>.json (+ formatted files).
    output_dir: str = "output"

    model_config = SettingsConfigDict(env_file=ENV_PATH, extra="ignore")

    @property
    def author_url(self) -> str:
        return self.aem_author_url.strip().rstrip("/")

    @property
    def output_path(self) -> Path:
        path = Path(self.output_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path


settings = Settings()


def harden_env_file() -> None:
    """Restrict .env to the owning user only.
    Called from app.py's startup, not at import time."""
    if ENV_PATH.exists():
        harden_file_permissions(ENV_PATH)
