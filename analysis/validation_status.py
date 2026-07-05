"""Offline status board for oracle validation result files.

This does not call T-Invest API. It scans ``analysis/*_validate.py`` and the
matching ``*_result.md`` files to show missing or stale validation artifacts.

Outcome priority (per script): entry in ``validation_outcomes.jsonl`` (written by
``run_all_validations`` after a live run) → parsed marker in ``*_result.md`` → UNKNOWN.
The outcomes file separates "when we last ran the validator" (run_at) from
"when we wrote the result discussion" (result file mtime).
"""
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DAY = 86400
DEFAULT_OUTCOMES = HERE / "validation_outcomes.jsonl"


@dataclass(frozen=True)
class ValidationRow:
    script: str
    result: str
    state: str
    outcome: str
    age_days: int | None
    mtime: str


def _legacy_result_name(script: Path) -> str:
    stem = script.stem
    if stem.endswith("_validate"):
        stem = stem[:-len("_validate")]
    return f"{stem}_result.md"


def _result_path(script: Path) -> Path:
    direct = script.with_name(f"{script.stem}_result.md")
    if direct.exists():
        return direct
    legacy = script.with_name(_legacy_result_name(script))
    if legacy.exists():
        return legacy
    matches = sorted(script.parent.glob(f"{script.stem}*_result.md"))
    return matches[0] if matches else legacy


def _outcome(result: Path) -> str:
    text = result.read_text(encoding="utf-8", errors="replace").upper()
    matches = list(re.finditer(r"\b(PASS|WARN|FAIL)\b", text))
    if matches:
        return matches[-1].group(1)
    if "✓" in text:
        return "PASS"
    return "UNKNOWN"


def _load_outcomes(path: Path | None) -> dict[str, str]:
    """Прочитать JSONL outcomes-файл, вернуть map script → outcome.

    Последняя запись по run_at wins (хронологически последний прогон).
    Плохие строки пропускаются молча — это кэш, не источник правды.
    """
    if path is None or not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        script = rec.get("script")
        outcome = rec.get("outcome")
        if not script or outcome not in ("PASS", "WARN", "FAIL"):
            continue
        # last-by-run_at: вставляем, и при дубле обновляем — JSONL-order + run_at
        # согласованы, если раннер пишет в append-режиме хронологически.
        out[script] = outcome
    return out


def collect_status(root: Path = HERE, now: float | None = None,
                   stale_days: int = 7,
                   outcomes_path: Path | None = DEFAULT_OUTCOMES) -> list[ValidationRow]:
    now = time.time() if now is None else now
    outcomes = _load_outcomes(outcomes_path)
    rows: list[ValidationRow] = []
    for script in sorted(root.glob("*_validate.py")):
        result = _result_path(script)
        if not result.exists():
            rows.append(ValidationRow(script.name, result.name, "MISSING", "", None, ""))
            continue
        age = max(0, int((now - result.stat().st_mtime) // DAY))
        state = "STALE" if age > stale_days else "FRESH"
        mtime = datetime.fromtimestamp(result.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        outcome = outcomes.get(script.name) or _outcome(result)
        rows.append(ValidationRow(script.name, result.name, state, outcome, age, mtime))
    return rows


def render_markdown(rows: list[ValidationRow], generated_at: str | None = None) -> str:
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    counts = {"FRESH": 0, "STALE": 0, "MISSING": 0}
    outcomes = {"PASS": 0, "WARN": 0, "FAIL": 0, "UNKNOWN": 0}
    for row in rows:
        counts[row.state] = counts.get(row.state, 0) + 1
        if row.outcome:
            outcomes[row.outcome] = outcomes.get(row.outcome, 0) + 1
    lines = [
        "# Validation Status",
        "",
        f"Generated: {generated_at}",
        "",
        f"Summary: FRESH: {counts['FRESH']} / STALE: {counts['STALE']} / "
        f"MISSING: {counts['MISSING']}",
        f"Outcomes: PASS: {outcomes['PASS']} / WARN: {outcomes['WARN']} / "
        f"FAIL: {outcomes['FAIL']} / UNKNOWN: {outcomes['UNKNOWN']}",
        "",
        "| script | result | state | outcome | age_days | mtime |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in rows:
        age = "" if row.age_days is None else str(row.age_days)
        lines.append(f"| {row.script} | {row.result} | {row.state} | {row.outcome} | "
                     f"{age} | {row.mtime} |")
    lines.append("")
    return "\n".join(lines)


def has_blocking_status(rows: list[ValidationRow]) -> bool:
    """True when status board should return a failing exit code."""
    return any(row.state == "MISSING" or row.outcome == "FAIL" for row in rows)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="offline freshness board for analysis/*_validate.py")
    p.add_argument("--root", default=str(HERE), help="analysis directory")
    p.add_argument("--stale-days", type=int, default=7)
    p.add_argument("--write", default=None,
                   help="write markdown to path (default: print to stdout)")
    args = p.parse_args(argv)

    rows = collect_status(Path(args.root), stale_days=args.stale_days)
    md = render_markdown(rows)
    if args.write:
        Path(args.write).write_text(md, encoding="utf-8")
    else:
        print(md)
    return 1 if has_blocking_status(rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
