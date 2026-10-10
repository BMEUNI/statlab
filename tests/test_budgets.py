"""Spend, limits and the start/run/person block messages (chunk 71)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from statlab.budgets import (
    AdminSettings,
    Meter,
    RunBudget,
    month_start,
    person_block,
    project_limits,
    read_settings,
    recent_runs,
    run_budget,
    run_limit,
    set_project_limits,
    set_run_limit,
    spend_since,
    start_block,
    write_settings,
)
from statlab.persistence.accounts import create_user, get_user, update_user
from statlab.persistence.repository import (
    create_contract,
    create_node_execution,
    create_project,
    create_run,
    record_llm_call,
)
from statlab_contracts.contract import DataContract, TargetSpec


def test_settings_round_trip(migrated_db: sqlite3.Connection) -> None:
    assert read_settings(migrated_db) == AdminSettings()
    write_settings(
        migrated_db,
        AdminSettings(default_monthly_limit_usd=5.0, signup_open=False),
    )
    assert read_settings(migrated_db) == AdminSettings(
        default_monthly_limit_usd=5.0, signup_open=False
    )
    write_settings(migrated_db, AdminSettings())
    assert read_settings(migrated_db) == AdminSettings()


def test_run_budget_limits_and_messages(migrated_db: sqlite3.Connection) -> None:
    now = datetime(2026, 10, 10, tzinfo=UTC)
    user = create_user(migrated_db, email="a@example.test", name="A", password_hash="h")  # noqa: S106 -- test value, not a real credential
    assert user is not None
    project = create_project(migrated_db, slug="p", name="P", owner_id=user.id)
    contract = create_contract(
        migrated_db,
        project_id=project.id,
        path=Path("/c.json"),
        contract=DataContract(target=TargetSpec(column="y")),
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract.id,
        mode="autonomous",
        config_json="{}",
        started_by=user.id,
    )
    execution = create_node_execution(migrated_db, run_id=run.id, node_name="data_profiling", seq=1)
    record_llm_call(
        migrated_db,
        run_id=run.id,
        node_execution_id=execution.id,
        signature="s",
        model="m",
        prompt_tokens=0,
        completion_tokens=0,
        cached_tokens=0,
        cost_usd=0.5,
        duration_ms=1,
        ts="2026-10-05T00:00:00+00:00",
    )
    record_llm_call(
        migrated_db,
        run_id=run.id,
        node_execution_id=execution.id,
        signature="s",
        model="m",
        prompt_tokens=0,
        completion_tokens=0,
        cached_tokens=0,
        cost_usd=0.25,
        duration_ms=1,
        ts="2026-09-30T23:00:00+00:00",
    )
    assert month_start(now) == "2026-10-01"
    assert spend_since(migrated_db, "2026-10-01") == 0.5
    assert spend_since(migrated_db, "") == 0.75
    assert run_budget(migrated_db, run.id, now) == RunBudget(
        run=Meter(spent_usd=0.75, limit_usd=None),
        project=Meter(spent_usd=0.75, limit_usd=None),
        person=Meter(spent_usd=0.5, limit_usd=None),
        started_by="A",
        blocked=None,
    )
    write_settings(migrated_db, AdminSettings(default_run_limit_usd=0.75))
    assert run_budget(migrated_db, run.id, now).blocked == (
        "This run has used its budget ($0.75 of $0.75)."
    )
    set_project_limits(migrated_db, project.id, limit_usd=None, run_limit_usd=1.0)
    budget = run_budget(migrated_db, run.id, now)
    assert budget.blocked is None
    assert budget.run.limit_usd == 1.0
    set_run_limit(migrated_db, run.id, 0.5)
    assert run_budget(migrated_db, run.id, now).blocked == (
        "This run has used its budget ($0.75 of $0.50)."
    )
    assert run_limit(migrated_db, run.id) == 0.5
    set_run_limit(migrated_db, run.id, None)
    set_project_limits(migrated_db, project.id, limit_usd=0.75, run_limit_usd=None)
    write_settings(migrated_db, AdminSettings())
    assert run_budget(migrated_db, run.id, now).blocked == (
        "This project has used its budget ($0.75 of $0.75)."
    )
    assert project_limits(migrated_db, project.id) == (0.75, None)
    set_project_limits(migrated_db, project.id, limit_usd=None, run_limit_usd=None)
    update_user(migrated_db, user.id, is_admin=True, disabled=False, monthly_limit_usd=0.5)
    starter = get_user(migrated_db, user.id)
    assert starter is not None
    assert run_budget(migrated_db, run.id, now).blocked == (
        "The person who started this run has used this month's budget ($0.50 of $0.50)."
    )
    expected = "You have used this month's budget ($0.50 of $0.50)."
    assert person_block(migrated_db, starter, now) == expected
    assert start_block(migrated_db, project.id, starter, now) == expected
    update_user(migrated_db, user.id, is_admin=True, disabled=False, monthly_limit_usd=None)
    starter = get_user(migrated_db, user.id)
    assert starter is not None
    assert start_block(migrated_db, project.id, starter, now) is None
    set_project_limits(migrated_db, project.id, limit_usd=None, run_limit_usd=0.0)
    assert start_block(migrated_db, project.id, starter, now) == (
        "New runs here have no budget ($0.00 of $0.00)."
    )
    set_project_limits(migrated_db, project.id, limit_usd=0.75, run_limit_usd=None)
    assert start_block(migrated_db, project.id, starter, now) == (
        "This project has used its budget ($0.75 of $0.75)."
    )
    migrated_db.execute(
        "INSERT INTO runs (id, project_id, contract_id, mode, status, started_at, config_json) "
        "VALUES ('r_raw', ?, ?, 'autonomous', 'completed', '2026-01-01T00:00:00+00:00', '{}')",
        (project.id, contract.id),
    )
    raw = run_budget(migrated_db, "r_raw", now)
    assert raw.started_by is None
    assert raw.person == Meter(spent_usd=0.0, limit_usd=None)
    assert run_limit(migrated_db, "r_raw") is None
    assert [r.id for r in recent_runs(migrated_db, 10)] == [run.id, "r_raw"]
    assert len(recent_runs(migrated_db, 1)) == 1
