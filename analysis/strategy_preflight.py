"""Offline preflight check for new strategy ideas against killed classes.

Usage:
    python -m analysis.strategy_preflight "opening range breakout on Brent"
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_INDEX = HERE / "KILLED_STRATEGIES.md"

STOPWORDS = {
    "and", "the", "with", "from", "that", "this", "into", "класс", "через",
    "strategy", "strategies", "futures", "stocks", "daily", "hour",
}


@dataclass(frozen=True)
class Entry:
    strategy: str
    klass: str
    instruments: str
    verdict: str
    reason: str
    evidence: str
    score: int = 0


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def load_entries(path: Path = DEFAULT_INDEX) -> list[Entry]:
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or "---" in line:
            continue
        cells = _cells(line)
        if not cells or cells[0].lower() == "strategy" or len(cells) < 6:
            continue
        entries.append(Entry(*cells[:6]))
    return entries


def _tokens(text: str) -> set[str]:
    raw = re.findall(r"[A-Za-zА-Яа-я0-9_]+", text.lower())
    out = set()
    for token in raw:
        if token in STOPWORDS or len(token) < 4:
            continue
        out.add(token)
        if "_" in token:
            out.update(part for part in token.split("_") if len(part) >= 4)
    return out


def match_idea(idea: str, entries: list[Entry] | None = None, limit: int = 5) -> list[Entry]:
    entries = entries or load_entries()
    idea_tokens = _tokens(idea)
    scored = []
    for entry in entries:
        haystack = " ".join([entry.strategy, entry.klass, entry.instruments, entry.reason])
        score = len(idea_tokens & _tokens(haystack))
        if score:
            scored.append(Entry(entry.strategy, entry.klass, entry.instruments,
                                entry.verdict, entry.reason, entry.evidence, score))
    return sorted(scored, key=lambda e: (-e.score, e.strategy))[:limit]


def missing_evidence(entries: list[Entry], root: Path = Path(".")) -> list[tuple[str, str]]:
    missing = []
    for entry in entries:
        for ref in re.findall(r"`([^`]+)`", entry.evidence):
            path = root / ref
            if not path.exists():
                missing.append((entry.strategy, ref))
    return missing


def render_report(idea: str, entries: list[Entry] | None = None) -> str:
    matches = match_idea(idea, entries)
    lines = [
        "# Strategy Preflight",
        "",
        f"idea: {idea}",
        "",
    ]
    if not matches:
        lines.append("No close killed-class match by simple token overlap.")
    else:
        lines.extend([
            "| score | killed class | strategy | kill reason | evidence |",
            "|---:|---|---|---|---|",
        ])
        for match in matches:
            lines.append(f"| {match.score} | {match.klass} | {match.strategy} | "
                         f"{match.reason} | {match.evidence} |")
    lines.extend([
        "",
        "Pre-flight questions:",
        "1. Which killed class is this closest to?",
        "2. What new market fact invalidates the old kill reason?",
        "3. Does the gross edge clear 10-20 bp per round trip and current key-rate carry?",
        "4. What is the objective oracle: OOS return, DSR, buyhold/random, min trades?",
        "",
    ])
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="offline preflight against killed strategy classes")
    p.add_argument("idea", nargs="*", help="strategy idea text")
    p.add_argument("--index", default=str(DEFAULT_INDEX))
    p.add_argument("--check-evidence", action="store_true",
                   help="validate backtick evidence paths in the killed-strategy index")
    args = p.parse_args(argv)
    entries = load_entries(Path(args.index))
    if args.check_evidence:
        missing = missing_evidence(entries, Path("."))
        if missing:
            for strategy, ref in missing:
                print(f"MISSING {strategy}: {ref}")
            return 1
        print("evidence OK")
        return 0
    if not args.idea:
        p.error("idea is required unless --check-evidence is used")
    print(render_report(" ".join(args.idea), entries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
