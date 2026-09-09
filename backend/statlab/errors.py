"""Domain errors. Every foreign exception is wrapped into one of these at its boundary."""

from __future__ import annotations


class StatlabError(Exception):
    """Base class for every error this application raises deliberately.

    Carries the original exception in `cause` so a boundary can wrap without losing the
    traceback, while callers pattern-match on the domain type rather than on library types.
    """

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause

    def __str__(self) -> str:
        if self.cause is None:
            return self.message
        return f"{self.message} (caused by {type(self.cause).__name__}: {self.cause})"


class ConfigError(StatlabError):
    """Configuration is invalid or incomplete.

    Raised only at startup, never mid-run: a run that has begun must not fail on config.
    `missing` names the environment variables that were absent, so the message can tell the
    user exactly what to add to `.env`.
    """

    def __init__(
        self,
        message: str,
        *,
        missing: tuple[str, ...] = (),
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, cause=cause)
        self.missing = missing
