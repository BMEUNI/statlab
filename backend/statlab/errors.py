"""Domain errors. Every foreign exception is wrapped into one of these at its boundary."""

from __future__ import annotations

import traceback

from statlab_contracts.envelope import ErrorClass, Failure
from statlab_contracts.envelope import retry_budget as _retry_budget


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


class ToolCallError(StatlabError):
    """A tool returned a ToolFailure, or its response could not be parsed.

    Carries the wire failure so the recovery handler (chunk 31) dispatches on `error_class`
    rather than on string matching.
    """

    def __init__(self, tool: str, failure: Failure, *, cause: BaseException | None = None) -> None:
        super().__init__(f"tool {tool!r} failed: {failure.message}", cause=cause)
        self.tool = tool
        self.failure = failure

    def __str__(self) -> str:
        return self.message

    @property
    def error_class(self) -> ErrorClass:
        return self.failure.error_class

    @property
    def retry_budget(self) -> int:
        """Attempts allowed for this failure's class. Delegates to the single source of
        truth in `statlab_contracts.envelope` rather than indexing `RETRY_BUDGET` itself."""
        return _retry_budget(self.failure.error_class)


class LlmCallError(StatlabError):
    """A provider request failed; provider exception text stays internal."""

    def __init__(self, failure: Failure, *, cause: BaseException | None = None) -> None:
        super().__init__(f"LLM call failed: {failure.message}", cause=cause)
        self.failure = failure

    def __str__(self) -> str:
        return self.message

    @property
    def error_class(self) -> ErrorClass:
        return self.failure.error_class

    @property
    def retry_budget(self) -> int:
        return _retry_budget(self.failure.error_class)


class LlmOutputError(LlmCallError):
    """A response remained invalid after the single repair request."""


class ToolNotAllowedError(StatlabError):
    """A node attempted a tool outside its skill-file allow-list (D12).

    Raised by chunk 21 before dispatch. Not a ToolCallError: the call never happened, and this
    is a programming or skill-file defect rather than a runtime condition.
    """

    def __init__(self, tool: str, node: str, allowed: frozenset[str]) -> None:
        allowed_list = ", ".join(sorted(allowed))
        super().__init__(f"tool {tool!r} is not allowed in node {node!r} (allowed: {allowed_list})")
        self.tool = tool
        self.node = node
        self.allowed = allowed


class ArtifactError(StatlabError):
    """An artifact is missing, unreadable, or of an unexpected kind."""

    def __init__(self, ref: str, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(f"artifact {ref!r}: {message}", cause=cause)
        self.ref = ref


class ArtifactLockedError(ArtifactError):
    """A locked artifact was passed as a tool parameter (D26, PLAN.md §4.2 rule 2).

    The test-set lock. Raised by chunk 21 pre-dispatch. Deliberately its own type so the
    guarantee is greppable and a test can assert this exact class.
    """

    def __init__(self, ref: str, current_node: str) -> None:
        super().__init__(
            ref,
            f"is locked (the test set) and cannot be used as input in node {current_node!r}",
        )
        self.current_node = current_node


class EngineError(StatlabError):
    """An invalid state transition in the run loop. Always a bug in our code."""


class PersistenceError(StatlabError):
    """Database operation failed. Wraps sqlite3 errors at the persistence boundary.

    A plain subclass with no structured payload, per chunk 02's precedent (`ArtifactError`):
    the message alone is what a caller needs, and `cause` already carries the original
    `sqlite3.Error` for anyone who needs to inspect it.
    """


class MigrationError(PersistenceError):
    """A migration could not be applied. Carries `version` and `name` because a caller (and
    `make db-reset`) needs to say which migration failed."""

    def __init__(
        self,
        message: str,
        *,
        version: int,
        name: str,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, cause=cause)
        self.version = version
        self.name = name


def traceback_frames(exc: BaseException) -> str:
    """Frame locations only; never source lines or the exception message.

    The message may carry secrets or data values.
    """
    frames = traceback.extract_tb(exc.__traceback__)
    return "\n".join(f"{frame.filename}:{frame.lineno} in {frame.name}" for frame in frames)
