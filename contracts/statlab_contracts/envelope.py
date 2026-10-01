"""The tool result envelope. Produced by the MCP server, parsed by the backend."""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from statlab_contracts.artifacts import ArtifactMeta

ErrorClass = Literal["transient", "tool_param", "data", "bug"]
"""The only dimension the recovery handler (chunk 31) dispatches on.

transient   — network, timeout, 429, transient IO. Retry with backoff.
tool_param  — schema-valid but semantically wrong arguments. Repairable by the agent.
data        — the data cannot support the request. NOT repairable by retrying; escalate
              to the user even in autonomous mode.
bug         — an unexpected exception. Escalate with a traceback (server log only).
"""

RETRY_BUDGET: Final[dict[ErrorClass, int]] = {
    "transient": 3,
    "tool_param": 2,
    "data": 0,
    "bug": 0,
}
"""Maximum attempts per class, from PLAN.md §6.1.1. Chunk 31 consumes this; it lives here so
the numbers cannot diverge between the taxonomy and the handler that applies it."""


def retry_budget(error_class: ErrorClass) -> int:
    """Attempts permitted for this class. The single source of truth for retryability."""
    return RETRY_BUDGET[error_class]


def is_retryable(error_class: ErrorClass) -> bool:
    """Whether retrying could possibly help. Derived, never carried on the wire.

    `Failure` deliberately has no `retryable` field: it would be fully determined by
    `error_class`, so it could only ever agree with `RETRY_BUDGET` or be a bug — duplicated
    state that adds validation burden and zero information. A tool also cannot know the answer
    itself, because "may I retry" depends on how many attempts have already happened, which is
    the recovery handler's state (chunk 31), not the tool's. Do not re-add this field; derive
    it here instead.
    """
    return retry_budget(error_class) > 0


class Failure(BaseModel):
    """The payload of a failed operation.

    Shared by tool failures and node failures (NodeResult.error) so there is exactly one
    failure shape in the system.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    error_class: ErrorClass
    message: str = Field(min_length=1)
    field_errors: dict[str, str] | None = None

    @model_validator(mode="after")
    def _field_errors_iff_tool_param(self) -> Failure:
        """`field_errors` is mandatory for tool_param and forbidden otherwise.

        This is what makes chunk 21's single repair retry mechanical: the agent is handed
        per-field reasons, not a prose blob to re-interpret.
        """
        if self.error_class == "tool_param":
            if not self.field_errors:
                raise ValueError(
                    "field_errors is mandatory when error_class == 'tool_param' "
                    "(and must be non-empty)"
                )
        elif self.field_errors is not None:
            raise ValueError(f"field_errors is forbidden when error_class == {self.error_class!r}")
        return self


class ToolSuccess(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: Literal[True] = True
    summary: dict[str, JsonValue] = Field(default_factory=dict)
    artifacts: tuple[ArtifactMeta, ...] = ()
    warnings: tuple[str, ...] = ()


class ToolFailure(Failure):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: Literal[False] = False


ToolResult = Annotated[ToolSuccess | ToolFailure, Field(discriminator="ok")]
"""Discriminated on `ok`, so parsing is unambiguous and errors point at the right branch."""

TOOL_RESULT_ADAPTER: Final[TypeAdapter[ToolSuccess | ToolFailure]] = TypeAdapter(ToolResult)
"""The single parse/serialise entry point. Chunk 21 must use this rather than hand-branching
on `ok`, so a malformed envelope fails at one place with one message."""
