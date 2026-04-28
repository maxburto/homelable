import json
import logging
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

def _read_version() -> str:
    for candidate in [
        Path(__file__).parent.parent.parent.parent / "VERSION",  # repo root (dev)
        Path("/app/VERSION"),                                      # Docker image
    ]:
        if candidate.exists():
            return candidate.read_text().strip()
    return "unknown"

APP_VERSION = _read_version()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    secret_key: str  # Required — set SECRET_KEY in .env
    sqlite_path: str = "./data/homelab.db"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # JWT
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440  # 24h

    # Auth — set AUTH_USERNAME and AUTH_PASSWORD_HASH in .env
    auth_username: str = "admin"
    auth_password_hash: str = ""
    # Optional multi-user auth map for operator and automation accounts.
    # Supported shapes:
    #   {"admin": "$2b$12$...", "codex-playwright": "$2b$12$..."}
    #   {"admin": {"password_hash": "$2b$12$..."}}
    auth_users_json: str = ""

    @model_validator(mode="after")
    def check_password_hash(self) -> "Settings":
        h = self.auth_password_hash
        if h and not h.startswith("$2"):
            logger.error(
                "AUTH_PASSWORD_HASH looks invalid (does not start with '$2b$'). "
                "bcrypt hashes contain '$' signs — wrap the value in single quotes "
                "in your .env file: AUTH_PASSWORD_HASH='$2b$12$...'"
            )
        return self

    def auth_users(self) -> dict[str, str]:
        users: dict[str, str] = {}
        if self.auth_username and self.auth_password_hash:
            users[self.auth_username] = self.auth_password_hash
        if not self.auth_users_json.strip():
            return users

        try:
            raw_users = json.loads(self.auth_users_json)
        except json.JSONDecodeError:
            logger.error("AUTH_USERS_JSON is not valid JSON; falling back to AUTH_USERNAME/AUTH_PASSWORD_HASH")
            return users

        if not isinstance(raw_users, dict):
            logger.error("AUTH_USERS_JSON must be a JSON object; falling back to AUTH_USERNAME/AUTH_PASSWORD_HASH")
            return users

        for username, value in raw_users.items():
            if not isinstance(username, str) or not username.strip():
                continue
            password_hash = ""
            if isinstance(value, str):
                password_hash = value
            elif isinstance(value, dict) and isinstance(value.get("password_hash"), str):
                password_hash = str(value["password_hash"])
            if password_hash:
                users[username] = password_hash
        return users

    # Scanner
    scanner_ranges: list[str] = ["192.168.1.0/24"]

    # Status checker
    status_checker_interval: int = 60
    proxmox_api_profiles: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # MCP service key — set MCP_SERVICE_KEY in .env
    # Used by the MCP server to authenticate against the backend without a user password.
    # Leave empty to disable MCP service key auth.
    mcp_service_key: str = ""

    # Live view — optional read-only public canvas endpoint.
    # Set to a random secret string to enable /api/v1/liveview?key=<value>.
    # Leave unset (or empty) to keep the feature disabled (default).
    liveview_key: str | None = None

    def _override_path(self) -> Path:
        return Path(self.sqlite_path).parent / "scan_config.json"

    def load_overrides(self) -> None:
        """Load runtime scan config overrides written by the API."""
        try:
            data = json.loads(self._override_path().read_text())
            if "scanner_ranges" in data:
                self.scanner_ranges = data["scanner_ranges"]
            if "status_checker_interval" in data:
                self.status_checker_interval = int(data["status_checker_interval"])
        except Exception:
            pass

    def save_overrides(self) -> None:
        """Persist scan config so it survives container restarts."""
        self._override_path().parent.mkdir(parents=True, exist_ok=True)
        self._override_path().write_text(json.dumps({
            "scanner_ranges": self.scanner_ranges,
            "status_checker_interval": self.status_checker_interval,
        }))


settings = Settings()  # type: ignore[call-arg]
