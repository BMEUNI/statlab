"""Backend process configuration. Provider-free by design (see chunk 01 §1)."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from statlab.errors import ConfigError

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class Settings(BaseSettings):
    """Backend settings, read from environment then `.env`.

    `extra="ignore"` is deliberate: the process environment legitimately contains hundreds of
    unrelated variables, so forbidding extras would make the app refuse to start because the
    user has `EDITOR` set. Unknown *declared* fields are still impossible.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    mcp_token: SecretStr = Field(alias="STATLAB_MCP_TOKEN")
    mcp_url: str = Field(default="http://127.0.0.1:8765/mcp", alias="STATLAB_MCP_URL")

    data_dir: Path = Field(default=Path("projects"), alias="STATLAB_DATA_DIR")
    db_path: Path = Field(default=Path("statlab.db"), alias="STATLAB_DB_PATH")

    api_host: str = Field(default="127.0.0.1", alias="STATLAB_API_HOST")
    api_port: int = Field(default=8000, ge=1024, le=65535, alias="STATLAB_API_PORT")
    allow_nonlocal_bind: bool = Field(default=False, alias="STATLAB_ALLOW_NONLOCAL_BIND")

    max_upload_mb: int = Field(default=200, ge=1, le=2048, alias="STATLAB_MAX_UPLOAD_MB")
    keep_intermediates: bool = Field(default=False, alias="STATLAB_KEEP_INTERMEDIATES")
    max_concurrent_runs: int = Field(default=0, ge=0, alias="STATLAB_MAX_CONCURRENT_RUNS")
    max_concurrent_llm: int = Field(default=4, ge=1, alias="STATLAB_MAX_CONCURRENT_LLM")

    @field_validator("mcp_token")
    @classmethod
    def _token_not_blank(cls, value: SecretStr) -> SecretStr:
        """An env var set to an empty string is "present" to pydantic, not "missing" —
        but `STATLAB_MCP_TOKEN=` in a real `.env` is the same operator mistake as leaving
        it unset, and D20's fail-fast intent covers both. `_missing_env_vars` recognises
        this validator's `type == "value_error"` on this field and reports it as missing too.
        """
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("data_dir", "db_path")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        """Resolve relative paths against CWD so two processes agree on one location."""
        return value.expanduser().resolve()

    @model_validator(mode="after")
    def _guard_bind_address(self) -> Settings:
        # ponytail: duplicated in mcp_server/statlab_tools/settings.py. Six lines across a
        # process boundary is cheaper than a third shared package. Upgrade path: extract to
        # a `statlab_shared` package if a third process ever needs it.
        if self.api_host not in _LOOPBACK_HOSTS and not self.allow_nonlocal_bind:
            raise ValueError(
                f"api_host {self.api_host!r} is not loopback. This application has no "
                f"authentication (D14); binding it publicly exposes an unauthenticated API. "
                f"Set STATLAB_ALLOW_NONLOCAL_BIND=true only if you understand that."
            )
        return self


def _missing_env_vars(exc: ValidationError) -> tuple[str, ...]:
    """Extract the environment-variable aliases reported as missing by `exc`.

    pydantic-settings resolves each field's alias against the environment *before*
    validation runs, so a `"missing"` error's `loc` is already the alias
    (e.g. `("STATLAB_MCP_TOKEN",)`), not the Python attribute name (`mcp_token`). That is
    exactly what a fail-fast message needs to say, since the user sets env vars, not
    Python identifiers.

    A blank `STATLAB_MCP_TOKEN=` is "present" to pydantic (an empty string, not an absent
    key), so `_token_not_blank` raises a `"value_error"` instead of a `"missing"` one for
    that specific case. Both mean the same thing to the user who ran `make check-env`, so
    both are reported the same way.
    """
    missing: list[str] = []
    for error in exc.errors():
        if not error["loc"]:
            continue
        if error["type"] == "missing":
            missing.append(str(error["loc"][0]))
        elif error["type"] == "value_error" and error["loc"] == ("STATLAB_MCP_TOKEN",):
            missing.append("STATLAB_MCP_TOKEN")
    return tuple(missing)


def load_settings(env_file: Path | None = None) -> Settings:
    """Build Settings, wrapping any pydantic ValidationError in ConfigError.

    Raises
    ------
    ConfigError
        With `missing` listing every environment variable reported as absent, and a message
        that names them so `make check-env` output is actionable.
    """
    try:
        settings = Settings(_env_file=env_file) if env_file is not None else Settings()
    except ValidationError as exc:
        missing = _missing_env_vars(exc)
        if missing:
            message = (
                f"Invalid configuration. Missing: {', '.join(missing)}. "
                f"Copy .env.example to .env and fill it in."
            )
        else:
            message = f"Invalid configuration. {exc}"
        raise ConfigError(message, missing=missing, cause=exc) from exc

    ensure_writable(settings.data_dir)
    ensure_writable(settings.db_path.parent)
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton. Tests must call `get_settings.cache_clear()`."""
    return load_settings()


def ensure_writable(path: Path) -> None:
    """Create `path` (and parents) if absent and verify it is writable.

    Raises
    ------
    ConfigError
        If creation fails or the directory is not writable, wrapping the OSError as `cause`.
    """
    if path.exists() and not path.is_dir():
        raise ConfigError(f"{path} exists and is not a directory.")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"Cannot create directory {path}.", cause=exc) from exc
    # A unique anonymous file, not a fixed name: two loaders probing at once (parallel
    # workers, or the server and backend sharing a data_dir) must not race on one path.
    try:
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError as exc:
        raise ConfigError(f"Directory {path} is not writable.", cause=exc) from exc
