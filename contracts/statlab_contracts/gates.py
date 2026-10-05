"""Seven explicit gate actions and declared-node rewind intent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

type GateAction = Literal[
    "approve",
    "approve_with_edits",
    "instruct",
    "approve_all_remaining",
    "skip_node",
    "rewind",
    "abort",
]


class GateAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    gate_id: str = Field(min_length=1)
    revision: int = Field(default=0, ge=0)
    action: GateAction
    edits: dict[str, JsonValue] = Field(default_factory=dict)
    instruction: str | None = None
    reason: str | None = None
    target: str | None = None

    @field_validator("gate_id", "instruction", "reason", "target")
    @classmethod
    def _nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("text must not be blank")
        return value

    @model_validator(mode="after")
    def _fields(self) -> GateAnswer:
        if (self.action == "instruct") != (self.instruction is not None):
            raise ValueError("instruction is required exactly for instruct")
        if (self.action == "skip_node") != (self.reason is not None):
            raise ValueError("reason is required exactly for skip_node")
        if (self.action == "rewind") != (self.target is not None):
            raise ValueError("target is required exactly for rewind")
        if self.action != "approve_with_edits" and self.edits:
            raise ValueError("edits are allowed only for approve_with_edits")
        return self


class RewindIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    target: str = Field(min_length=1)

    @field_validator("target")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("target must not be blank")
        return value
