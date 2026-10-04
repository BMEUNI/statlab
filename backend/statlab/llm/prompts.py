"""Invariant-first prompt assembly for a single node's initialized context."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import JsonValue
from statlab_contracts.contract import DataContract

from statlab.errors import EngineError
from statlab.llm.skills import NodeSkill
from statlab.persistence.models import MemoryRow


def _canonical(value: JsonValue) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except ValueError:
        raise EngineError("prompt JSON could not be serialized") from None


@dataclass(frozen=True, slots=True)
class PromptContext:
    preamble: str
    skill: NodeSkill
    contract: DataContract
    memory: tuple[MemoryRow, ...] = ()
    _prefix: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        contract = _canonical(self.contract.model_dump(mode="json"))
        facts: list[JsonValue] = [
            {"key": row.key, "value": row.value(), "source_run_id": row.source_run_id}
            for row in self.memory
        ]
        object.__setattr__(
            self,
            "_prefix",
            "\n\n".join((self.preamble, self.skill.section, contract, _canonical(facts))),
        )

    def messages(
        self, *, state_summary: JsonValue, recent_tool_results: tuple[JsonValue, ...], question: str
    ) -> tuple[dict[str, str], ...]:
        return (
            {"role": "system", "content": self._prefix},
            {
                "role": "user",
                "content": "\n\n".join(
                    (_canonical(state_summary), _canonical(list(recent_tool_results)), question)
                ),
            },
        )
