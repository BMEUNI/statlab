"""HTTP setup, run loop, SSE recorder, gate policy and actions."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import JsonValue
from statlab_contracts.contract import DataContract

from integration.checks import CheckResult
from integration.datasets import write_csv
from integration.model import Action, GateRule, Scenario

TERMINAL = {"completed", "failed", "cancelled", "superseded"}
_ANY_STATUS = set(range(100, 600))


class HarnessError(Exception):
    """Unexpected API behaviour while driving a scenario."""


@dataclass(frozen=True)
class Workspace:
    root: Path
    data_dir: Path
    db_path: Path
    api_log: Path
    mcp_log: Path

    def scenario_dir(self, scenario_id: str) -> Path:
        path = self.root / scenario_id
        path.mkdir(parents=True, exist_ok=True)
        return path


@dataclass
class Budget:
    db_path: Path
    limit: float

    def spent(self) -> float:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls").fetchone()
        finally:
            conn.close()
        return float(row[0])


@dataclass(frozen=True)
class SetupResult:
    project_id: str
    dataset_id: str
    contract_id: str
    run_id: str
    validation: dict[str, JsonValue]
    proposal: dict[str, JsonValue] | None = None


@dataclass
class RunOutcome:
    run_id: str
    final_status: str = ""
    timed_out: bool = False
    budget_stopped: bool = False
    gate_answers: list[dict[str, JsonValue]] = field(default_factory=list)
    actions: list[dict[str, JsonValue]] = field(default_factory=list)
    messages: list[dict[str, JsonValue]] = field(default_factory=list)
    events: list[dict[str, JsonValue]] = field(default_factory=list)
    started_at: str = ""
    ended_at: str = ""
    fork_responses: list[dict[str, JsonValue]] = field(default_factory=list)
    child_run_id: str | None = None


def _obj(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise HarnessError(f"expected JSON object, got {type(value).__name__}")
    return {str(key): item for key, item in value.items()}


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise HarnessError(f"expected string, got {type(value).__name__}")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _cheap_constraints(contract: dict[str, JsonValue]) -> dict[str, JsonValue]:
    constraints = (
        dict(_obj(contract["constraints"])) if isinstance(contract.get("constraints"), dict) else {}
    )
    constraints.setdefault("max_search_waves", 1)
    constraints.setdefault("max_retune_loops", 0)
    return {**contract, "constraints": constraints}


class Driver:
    def __init__(
        self,
        *,
        base_url: str,
        workspace: Workspace,
        restart: Callable[[], Awaitable[None]] | None = None,
        cheap: bool = False,
    ) -> None:
        self._restart = restart
        self._cheap = cheap
        self.workspace = workspace
        self.client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(120, read=300))
        self.http_log: list[dict[str, JsonValue]] = []

    async def close(self) -> None:
        await self.client.aclose()

    async def _call(
        self,
        method: str,
        path: str,
        *,
        expected: set[int],
        json: JsonValue = None,
        files: dict[str, tuple[str, bytes | str, str]] | None = None,
    ) -> httpx.Response:
        started = time.monotonic()
        response = await self.client.request(method, path, json=json, files=files)
        self.http_log.append(
            {
                "method": method,
                "path": path,
                "status": response.status_code,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }
        )
        if response.status_code not in expected:
            raise HarnessError(f"{method} {path} -> {response.status_code}: {response.text[:500]}")
        return response

    async def _project_and_dataset(self, scenario: Scenario, scenario_dir: Path) -> tuple[str, str]:
        created = await self._call(
            "POST",
            "/api/projects",
            expected={201},
            json={"slug": scenario.id.lower(), "name": scenario.title},
        )
        project_id = _str(_obj(created.json())["id"])
        csv_path = write_csv(scenario.dataset, scenario_dir / "data.csv")
        uploaded = await self._call(
            "POST",
            f"/api/projects/{project_id}/datasets",
            expected={201},
            files={"file": ("data.csv", csv_path.read_bytes(), "text/csv")},
        )
        return project_id, _str(_obj(uploaded.json())["id"])

    async def create_run(
        self, project_id: str, dataset_id: str, contract_id: str, mode: str
    ) -> str:
        run = await self._call(
            "POST",
            f"/api/projects/{project_id}/runs",
            expected={201},
            json={"mode": mode, "dataset_ids": [dataset_id], "contract_id": contract_id},
        )
        return _str(_obj(run.json())["run_id"])

    async def setup(self, scenario: Scenario, scenario_dir: Path) -> SetupResult:
        project_id, dataset_id = await self._project_and_dataset(scenario, scenario_dir)
        contract = scenario.contract
        proposal: dict[str, JsonValue] | None = None
        if scenario.setup.propose:
            proposed = await self._call(
                "POST",
                f"/api/projects/{project_id}/contracts/propose",
                expected={200},
                json={"dataset_id": dataset_id, "contract": scenario.contract},
            )
            proposal = _obj(proposed.json())
            (scenario_dir / "proposal.json").write_text(
                json.dumps(proposal, indent=2), encoding="utf-8"
            )
            contract = _obj(proposal["contract"])
        if self._cheap:
            contract = _cheap_constraints(contract)
        DataContract.model_validate(contract)
        validated = await self._call(
            "POST",
            f"/api/projects/{project_id}/contracts/validate",
            expected={200},
            json={"dataset_id": dataset_id, "contract": contract},
        )
        validation = _obj(validated.json())
        if validation.get("valid") is False:
            raise HarnessError(f"setup validation failed: {validation.get('mismatches')}")
        stored = await self._call(
            "POST",
            f"/api/projects/{project_id}/contracts",
            expected={201},
            files={"file": ("contract.json", json.dumps(contract), "application/json")},
        )
        contract_id = _str(_obj(stored.json())["id"])
        run_id = await self.create_run(project_id, dataset_id, contract_id, scenario.mode)
        return SetupResult(project_id, dataset_id, contract_id, run_id, validation, proposal)

    # --- run loop -------------------------------------------------------------------------

    async def _record_events(
        self, run_id: str, scenario_dir: Path, events: list[dict[str, JsonValue]]
    ) -> None:
        last_seq = 0
        sink = scenario_dir / "sse_events.jsonl"
        while True:
            try:
                async with self.client.stream(
                    "GET",
                    f"/api/runs/{run_id}/events",
                    params={"since": last_seq},
                    timeout=httpx.Timeout(30, read=None),
                ) as response:
                    payload: dict[str, JsonValue] | None = None
                    async for line in response.aiter_lines():
                        if line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            payload = _obj(json.loads(line[5:].strip()))
                        elif line == "" and payload is not None:
                            seq = payload.get("seq")
                            if isinstance(seq, int) and seq > last_seq:
                                last_seq = seq
                                events.append({**payload})
                                with sink.open("a", encoding="utf-8") as handle:
                                    handle.write(json.dumps(payload) + "\n")
                                shown = json.dumps(payload.get("payload"), ensure_ascii=False)
                                print(
                                    f"  {str(payload.get('ts', ''))[11:19]} "
                                    f"{payload.get('type')} {shown[:160]}",
                                    flush=True,
                                )
                            payload = None
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)

    @staticmethod
    def _find_rule(
        rules: tuple[GateRule, ...], used: set[int], node: str, phase: str
    ) -> tuple[int, GateRule] | None:
        for index, rule in enumerate(rules):
            if rule.node != node or rule.phase not in (None, phase):
                continue
            if rule.once and index in used:
                continue
            return index, rule
        return None

    async def _answer_gates(
        self,
        scenario: Scenario,
        run_id: str,
        gates: list[JsonValue],
        answered: set[tuple[str, int]],
        used: set[int],
        outcome: RunOutcome,
    ) -> None:
        for raw in gates:
            gate = _obj(raw)
            if gate.get("status") != "pending":
                continue
            gate_id = _str(gate["id"])
            question = _obj(json.loads(_str(gate["question_json"])))
            revision = question["revision"]
            if not isinstance(revision, int) or (gate_id, revision) in answered:
                continue
            node = _str(question["node"])
            phase = _str(question["phase"])
            found = self._find_rule(scenario.gates, used, node, phase)
            body: dict[str, JsonValue] = {"revision": revision}
            if found is None:
                options = question["options"]
                if isinstance(options, list) and "approve" in options:
                    body["action"] = "approve"
                else:
                    body["action"] = question["default"]
            else:
                index, rule = found
                used.add(index)
                body["action"] = rule.action
                if rule.action == "approve_with_edits":
                    body["edits"] = rule.edits
                for key in ("instruction", "reason", "target"):
                    value = getattr(rule, key)
                    if value is not None:
                        body[key] = value
            response = await self._call(
                "POST", f"/api/runs/{run_id}/gates/{gate_id}", expected={200}, json=body
            )
            answered.add((gate_id, revision))
            outcome.gate_answers.append(
                {
                    "gate_id": gate_id,
                    "node": node,
                    "phase": phase,
                    "revision": revision,
                    "action": body["action"],
                    "response": _obj(response.json()),
                }
            )
            return

    @staticmethod
    def _trigger_matches(action: Action, status: str, events: list[dict[str, JsonValue]]) -> bool:
        trigger = action.when
        if trigger.event is not None:
            for event in events:
                if event.get("type") != trigger.event:
                    continue
                if trigger.node is None:
                    return True
                inner = event.get("payload")
                if isinstance(inner, dict) and inner.get("node") == trigger.node:
                    return True
            return False
        return status == trigger.status

    async def _fire(self, run_id: str, index: int, action: Action, outcome: RunOutcome) -> None:
        if action.do == "message":
            response = await self._call(
                "POST", f"/api/runs/{run_id}/messages", expected={200}, json={"text": action.text}
            )
        elif action.do == "rewind":
            rewind_body: JsonValue = {
                "action": "rewind",
                "target": action.target,
                "request_id": action.request_id,
            }
            response = await self._call(
                "POST", f"/api/runs/{run_id}/control", expected={200, 201}, json=rewind_body
            )
            again = await self._call(
                "POST", f"/api/runs/{run_id}/control", expected={200, 201}, json=rewind_body
            )
            first_body = _obj(response.json())
            outcome.fork_responses.append({**first_body, "status_code": response.status_code})
            outcome.fork_responses.append({**_obj(again.json()), "status_code": again.status_code})
            outcome.child_run_id = _str(first_body["new_run_id"])
        elif action.do == "restart_api":
            if self._restart is None:
                raise HarnessError("restart_api needs a restart hook")
            await self._restart()
            outcome.actions.append({"index": index, "do": action.do, "status_code": 0, "body": {}})
            return
        else:
            response = await self._call(
                "POST", f"/api/runs/{run_id}/control", expected={200}, json={"action": action.do}
            )
        body = _obj(response.json())
        outcome.actions.append(
            {"index": index, "do": action.do, "status_code": response.status_code, "body": body}
        )
        if action.do == "message":
            outcome.messages.append(body)

    async def _cancel_quietly(self, run_id: str) -> None:
        with contextlib.suppress(HarnessError, httpx.HTTPError):
            await self._call(
                "POST", f"/api/runs/{run_id}/control", expected={200}, json={"action": "cancel"}
            )

    async def run(
        self, scenario: Scenario, run_id: str, scenario_dir: Path, *, budget: Budget
    ) -> RunOutcome:
        outcome = RunOutcome(run_id=run_id, started_at=_now())
        recorder = asyncio.create_task(self._record_events(run_id, scenario_dir, outcome.events))
        answered: set[tuple[str, int]] = set()
        used: set[int] = set()
        fired: set[int] = set()
        started = time.monotonic()
        try:
            while True:
                response = await self._call("GET", f"/api/runs/{run_id}", expected={200})
                snapshot = _obj(response.json())
                (scenario_dir / "snapshot.json").write_text(
                    json.dumps(snapshot, indent=2), encoding="utf-8"
                )
                status = _str(_obj(snapshot["run"])["status"])
                outcome.final_status = status
                if snapshot.get("execution_failed") is True:
                    outcome.final_status = "execution_failed"
                    break
                gates = snapshot.get("gates")
                if status in ("awaiting_user", "paused"):
                    await self._answer_gates(
                        scenario,
                        run_id,
                        gates if isinstance(gates, list) else [],
                        answered,
                        used,
                        outcome,
                    )
                for index, action in enumerate(scenario.actions):
                    if index not in fired and self._trigger_matches(action, status, outcome.events):
                        fired.add(index)
                        await self._fire(run_id, index, action, outcome)
                        break
                pending_status = any(
                    index not in fired and action.when.status == status
                    for index, action in enumerate(scenario.actions)
                )
                if status in TERMINAL and not pending_status:
                    break
                if time.monotonic() - started > scenario.timeout_s:
                    await self._cancel_quietly(run_id)
                    outcome.timed_out = True
                    break
                if budget.spent() >= budget.limit:
                    await self._cancel_quietly(run_id)
                    outcome.budget_stopped = True
                    break
                await asyncio.sleep(1.0)
            await asyncio.sleep(2)
        finally:
            recorder.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await recorder
        outcome.ended_at = _now()
        return outcome

    # --- invalid setup ----------------------------------------------------------------------

    async def invalid_setup(self, scenario: Scenario, scenario_dir: Path) -> list[CheckResult]:
        results: list[CheckResult] = []
        project_id, dataset_id = await self._project_and_dataset(scenario, scenario_dir)

        validated = await self._call(
            "POST",
            f"/api/projects/{project_id}/contracts/validate",
            expected=_ANY_STATUS,
            json={"dataset_id": dataset_id, "contract": scenario.contract},
        )
        valid = _obj(validated.json()).get("valid")
        uploaded = await self._call(
            "POST",
            f"/api/projects/{project_id}/contracts",
            expected={201},
            files={"file": ("contract.json", json.dumps(scenario.contract), "application/json")},
        )
        run = await self._call(
            "POST",
            f"/api/projects/{project_id}/runs",
            expected=_ANY_STATUS,
            json={
                "mode": scenario.mode,
                "dataset_ids": [dataset_id],
                "contract_id": _str(_obj(uploaded.json())["id"]),
            },
        )
        results.append(
            CheckResult(
                "S17-a",
                "invalid target: validate says invalid, run creation 422",
                "must",
                valid is False and run.status_code == 422,
                f"valid={valid} run_status={run.status_code}",
            )
        )

        incomplete = await self._call(
            "POST",
            f"/api/projects/{project_id}/contracts",
            expected={201},
            files={
                "file": (
                    "incomplete.json",
                    json.dumps({"target": {"column": "churned"}}),
                    "application/json",
                )
            },
        )
        run = await self._call(
            "POST",
            f"/api/projects/{project_id}/runs",
            expected=_ANY_STATUS,
            json={
                "mode": scenario.mode,
                "dataset_ids": [dataset_id],
                "contract_id": _str(_obj(incomplete.json())["id"]),
            },
        )
        results.append(
            CheckResult(
                "S17-b",
                "incomplete contract: run creation 422 missing required setup fields",
                "must",
                run.status_code == 422 and "missing required setup fields" in run.text,
                f"status={run.status_code} body={run.text[:300]}",
            )
        )

        bad = await self._call(
            "POST",
            f"/api/projects/{project_id}/datasets",
            expected=_ANY_STATUS,
            files={"file": ("bad.csv", b"\x00\x01binary", "text/csv")},
        )
        results.append(
            CheckResult(
                "S17-c",
                "non-CSV upload rejected with 4xx",
                "must",
                400 <= bad.status_code < 500,
                f"status={bad.status_code}",
            )
        )

        detail = await self._call("GET", f"/api/projects/{project_id}", expected={200})
        runs = _obj(detail.json()).get("runs")
        results.append(
            CheckResult("S17-runs", "zero runs created", "must", runs == [], f"runs={runs}")
        )
        conn = sqlite3.connect(f"file:{self.workspace.db_path}?mode=ro", uri=True)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM llm_calls WHERE run_id IN "
                "(SELECT id FROM runs WHERE project_id = ?)",
                (project_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        results.append(
            CheckResult("S17-llm", "zero llm_calls", "must", count == 0, f"llm_calls={count}")
        )
        return results
