"""MCP server process configuration. Imports nothing from the backend package."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class ToolSettingsError(Exception):
    """Configuration error in the MCP server process.

    Deliberately NOT `statlab.errors.ConfigError`: importing the backend package here would
    couple two processes that must stay independently deployable (D13).
    """

    def __init__(
        self,
        message: str,
        *,
        missing: tuple[str, ...] = (),
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.missing = missing
        self.cause = cause

    def __str__(self) -> str:
        if self.cause is None:
            return self.message
        return f"{self.message} (caused by {type(self.cause).__name__}: {self.cause})"


class ToolSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    mcp_token: SecretStr = Field(alias="STATLAB_MCP_TOKEN")
    data_dir: Path = Field(default=Path("projects"), alias="STATLAB_DATA_DIR")
    host: str = Field(default="127.0.0.1", alias="STATLAB_MCP_HOST")
    port: int = Field(default=8765, ge=1024, le=65535, alias="STATLAB_MCP_PORT")
    allow_nonlocal_bind: bool = Field(default=False, alias="STATLAB_ALLOW_NONLOCAL_BIND")
    fit_permits: int = Field(default=0, ge=0, alias="STATLAB_FIT_PERMITS")

    @field_validator("mcp_token")
    @classmethod
    def _token_not_blank(cls, value: SecretStr) -> SecretStr:
        """Mirrors `statlab.config.Settings._token_not_blank`; see there."""
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("data_dir")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @model_validator(mode="after")
    def _guard_bind_address(self) -> ToolSettings:
        # ponytail: duplicated from backend/statlab/config.py on purpose — see the note there.
        if self.host not in _LOOPBACK_HOSTS and not self.allow_nonlocal_bind:
            raise ValueError(
                f"host {self.host!r} is not loopback. This server writes files and starts "
                f"model fits on request; do not expose it. Set STATLAB_ALLOW_NONLOCAL_BIND=true "
                f"only if you understand that."
            )
        return self

    @property
    def effective_fit_permits(self) -> int:
        """Semaphore size. `fit_permits=0` means 'one per core'.

        Kept as a property rather than resolved in a validator so tests can inject a fixed
        permit count (D17, §14.2) instead of depending on the machine's core count.
        """
        if self.fit_permits > 0:
            return self.fit_permits
        return os.cpu_count() or 1


def _missing_env_vars(exc: ValidationError) -> tuple[str, ...]:
    """Same alias-extraction contract as `statlab.config._missing_env_vars`; see there,
    including the blank-token-counts-as-missing special case."""
    missing: list[str] = []
    for error in exc.errors():
        if not error["loc"]:
            continue
        if error["type"] == "missing":
            missing.append(str(error["loc"][0]))
        elif error["type"] == "value_error" and error["loc"] == ("STATLAB_MCP_TOKEN",):
            missing.append("STATLAB_MCP_TOKEN")
    return tuple(missing)


def load_tool_settings(env_file: Path | None = None) -> ToolSettings:
    """Same contract as `load_settings`, raising `ToolSettingsError`."""
    try:
        settings = ToolSettings(_env_file=env_file) if env_file is not None else ToolSettings()
    except ValidationError as exc:
        missing = _missing_env_vars(exc)
        if missing:
            message = (
                f"Invalid configuration. Missing: {', '.join(missing)}. "
                f"Copy .env.example to .env and fill it in."
            )
        else:
            message = f"Invalid configuration. {exc}"
        raise ToolSettingsError(message, missing=missing, cause=exc) from exc

    if settings.data_dir.exists() and not settings.data_dir.is_dir():
        raise ToolSettingsError(f"{settings.data_dir} exists and is not a directory.")
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolSettingsError(f"Cannot create directory {settings.data_dir}.", cause=exc) from exc
    probe = settings.data_dir / ".statlab_write_probe"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ToolSettingsError(
            f"Directory {settings.data_dir} is not writable.", cause=exc
        ) from exc

    return settings


@lru_cache(maxsize=1)
def get_tool_settings() -> ToolSettings:
    return load_tool_settings()
