"""Deterministic synthetic CSV generators with planted truth."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import random
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

from integration.model import DatasetKey

REAL_SOURCES: dict[str, tuple[str, str, str]] = {
    "adult": (
        "https://archive.ics.uci.edu/static/public/2/adult.zip",
        "7537312dd56c2b98035880805ce99e68183a30ee468aa5329d6df0fbb3cc21bb",
        "adult.data",
    ),
    "autompg": (
        "https://archive.ics.uci.edu/static/public/9/auto+mpg.zip",
        "5fc1507522b73b8050d3aa45df51eefe1b666ae5f428a5f1347b6483339f1003",
        "auto-mpg.data",
    ),
}
CACHE_DIR = Path("integration/.cache")

Table = tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]


def _bernoulli(rng: random.Random, logit: float) -> int:
    p = 1 / (1 + math.exp(-logit))
    return 1 if rng.random() < p else 0


def _churn_row(rng: random.Random) -> tuple[str, str, int, float, int, int]:
    plan = rng.choices(["basic", "standard", "premium"], weights=[40, 35, 25])[0]
    region = rng.choice(["north", "south", "east", "west"])
    tenure = rng.randint(1, 72)
    charges = round(min(150.0, max(10.0, rng.gauss(60, 15))), 2)
    calls = rng.choices(range(10), weights=[25, 20, 15, 12, 10, 7, 5, 3, 2, 1])[0]
    logit = (
        -0.5 + 1.4 * (plan == "basic") + 0.5 * (plan == "standard") - 0.04 * tenure + 0.3 * calls
    )
    churned = _bernoulli(rng, logit)
    return plan, region, tenure, charges, calls, churned


def _churn() -> Table:
    rng = random.Random(101)  # noqa: S311 - synthetic data, not cryptographic.
    rows: list[tuple[str, ...]] = []
    for i in range(1, 501):
        plan, region, tenure, charges, calls, churned = _churn_row(rng)
        rows.append(
            (f"S{i:05d}", plan, region, str(tenure), str(charges), str(calls), str(churned))
        )
    header = (
        "subscriber_id",
        "plan",
        "region",
        "tenure_months",
        "monthly_charges",
        "support_calls",
        "churned",
    )
    return header, tuple(rows)


def _churn_dirty() -> Table:
    rng = random.Random(103)  # noqa: S311 - synthetic data, not cryptographic.
    rows: list[tuple[str, ...]] = []
    for i in range(1, 401):
        plan, region, tenure, charges, calls, churned = _churn_row(rng)
        notes_draw = rng.random()
        notes_pick = rng.choice(["vip", "late payer", "moved"])
        city = f"city_{rng.randint(1, 60):02d}"
        na_draw = rng.random()
        na_token = rng.choice(["NA", "N/A", ""])
        notes = notes_pick if notes_draw >= 0.85 else ""
        charges_text = na_token if na_draw < 0.05 else str(charges)
        if i % 25 == 0:
            plan = "?"
        if i % 20 == 0:
            region = region.upper()
        rows.append(
            (
                f"S{i:05d}",
                plan,
                region,
                str(tenure),
                charges_text,
                str(calls),
                "HU",
                f"C{i:06d}x",
                notes,
                city,
                str(churned),
            )
        )
    rows.extend(rows[:20])
    header = (
        "subscriber_id",
        "plan",
        "region",
        "tenure_months",
        "monthly_charges",
        "support_calls",
        "country",
        "customer_code",
        "notes",
        "city",
        "churned",
    )
    return header, tuple(rows)


def _houses() -> Table:
    rng = random.Random(102)  # noqa: S311 - synthetic data, not cryptographic.
    offsets = {"a": 50000, "b": 0, "c": -30000}
    rows: list[tuple[str, ...]] = []
    for _ in range(400):
        area = round(rng.uniform(30, 200), 1)
        rooms = rng.randint(1, 6)
        district = rng.choice(["a", "b", "c"])
        age = rng.randint(0, 80)
        garden = rng.choice(["yes", "no"])
        price = round(
            1500 * area + 20000 * rooms + offsets[district] - 300 * age + rng.gauss(0, 20000)
        )
        area_missing = rng.random() < 0.05
        age_missing = rng.random() < 0.03
        rows.append(
            (
                "" if area_missing else str(area),
                str(rooms),
                district,
                "" if age_missing else str(age),
                garden,
                str(price),
            )
        )
    header = ("area_m2", "rooms", "district", "age_years", "has_garden", "price")
    return header, tuple(rows)


def _species() -> Table:
    rng = random.Random(104)  # noqa: S311 - synthetic data, not cryptographic.
    classes = (
        ("setosa_like", 5.0, 1.5),
        ("versicolor_like", 5.9, 4.3),
        ("virginica_like", 6.6, 5.6),
    )
    rows: list[tuple[str, ...]] = []
    for name, x1_mean, x3_mean in classes:
        for _ in range(150):
            x1 = round(rng.gauss(x1_mean, 0.35), 2)
            x2 = round(rng.gauss(3.0, 0.4), 2)
            x3 = round(rng.gauss(x3_mean, 0.4), 2)
            x4 = round(rng.gauss(1.0, 0.5), 2)
            rows.append((str(x1), str(x2), str(x3), str(x4), name))
    rng.shuffle(rows)
    return ("x1", "x2", "x3", "x4", "species"), tuple(rows)


def _sales() -> Table:
    rng = random.Random(105)  # noqa: S311 - synthetic data, not cryptographic.
    weekday_effect = (0, 2, 4, 6, 10, 25, 15)
    start = date(2025, 1, 1)
    rows: list[tuple[str, ...]] = []
    for i in range(365):
        day = start + timedelta(days=i)
        promo = 1 if rng.random() < 0.25 else 0
        temp = round(
            10 + 12 * math.sin(2 * math.pi * (i - 100) / 365) + rng.gauss(0, 3),
            1,
        )
        units = round(
            100
            + 20 * promo
            + weekday_effect[day.weekday()]
            + 0.1 * i
            + 0.5 * temp
            + rng.gauss(0, 5)
        )
        rows.append((day.isoformat(), str(promo), str(temp), str(units)))
    return ("date", "promo", "temperature", "units"), tuple(rows)


def _visits() -> Table:
    rng = random.Random(106)  # noqa: S311 - synthetic data, not cryptographic.
    rows: list[tuple[str, ...]] = []
    for patient in range(1, 81):
        age = rng.randint(30, 80)
        base = rng.gauss(0, 1)
        for _ in range(6):
            dose = rng.choice([0, 10, 20, 40])
            biomarker = round(base + rng.gauss(0, 0.5), 3)
            logit = -2 + 0.06 * dose + 1.2 * biomarker
            responded = _bernoulli(rng, logit)
            rows.append((f"P{patient:03d}", str(age), str(dose), str(biomarker), str(responded)))
    return ("patient_id", "age", "dose", "biomarker", "responded"), tuple(rows)


def fetch_zip(key: str) -> bytes:
    url, digest, _ = REAL_SOURCES[key]
    path = CACHE_DIR / f"{key}.zip"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https UCI URL
            path.write_bytes(response.read())
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        path.unlink()
        raise RuntimeError(f"{key}: downloaded archive sha256 mismatch")
    return data


def _member_text(key: str) -> str:
    member = REAL_SOURCES[key][2]
    return zipfile.ZipFile(io.BytesIO(fetch_zip(key))).read(member).decode("utf-8")


def _adult() -> Table:
    header = (
        "age",
        "workclass",
        "fnlwgt",
        "education",
        "education_num",
        "marital_status",
        "occupation",
        "relationship",
        "race",
        "sex",
        "capital_gain",
        "capital_loss",
        "hours_per_week",
        "native_country",
        "income",
    )
    rows: list[tuple[str, ...]] = []
    reader = csv.reader(_member_text("adult").splitlines(), skipinitialspace=True)
    for row in reader:
        if not any(value.strip() for value in row):
            continue
        if len(row) != 15:
            raise RuntimeError(f"adult: malformed row {reader.line_num}")
        rows.append(tuple(value.strip() for value in row))
    return header, tuple(rows)


def _autompg() -> Table:
    header = (
        "mpg",
        "cylinders",
        "displacement",
        "horsepower",
        "weight",
        "acceleration",
        "model_year",
        "origin",
        "car_name",
    )
    rows: list[tuple[str, ...]] = []
    for number, line in enumerate(_member_text("autompg").splitlines(), start=1):
        if not line.strip():
            continue
        left, _, name = line.partition("\t")
        fields = left.split()
        if len(fields) != 8:
            raise RuntimeError(f"autompg: malformed row {number}")
        rows.append((*fields, name.strip().strip('"')))
    return header, tuple(rows)


def generate(key: DatasetKey) -> Table:
    if key == "churn":
        return _churn()
    if key == "churn_dirty":
        return _churn_dirty()
    if key == "houses":
        return _houses()
    if key == "species":
        return _species()
    if key == "sales":
        return _sales()
    if key == "visits":
        return _visits()
    if key == "adult":
        return _adult()
    return _autompg()


def write_csv(key: DatasetKey, path: Path) -> Path:
    header, rows = generate(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    return path
