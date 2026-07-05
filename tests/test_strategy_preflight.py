from pathlib import Path

from analysis.strategy_preflight import load_entries, match_idea, missing_evidence, render_report


def test_load_entries_from_killed_strategy_table(tmp_path: Path):
    md = tmp_path / "killed.md"
    md.write_text(
        "| strategy | class | instruments / horizon | verdict | primary kill reason | evidence |\n"
        "|---|---|---|---|---|---|\n"
        "| ORB_REVERSAL | opening-range false breakout reversal | futures | KILL | no edge | report |\n",
        encoding="utf-8",
    )

    entries = load_entries(md)

    assert len(entries) == 1
    assert entries[0].strategy == "ORB_REVERSAL"
    assert entries[0].klass == "opening-range false breakout reversal"


def test_match_idea_finds_closest_killed_class():
    entries = load_entries(Path("analysis/KILLED_STRATEGIES.md"))

    matches = match_idea("Opening range breakout on Brent futures with ATR filter", entries)

    assert matches[0].strategy == "ORB_REVERSAL"
    assert matches[0].score > 0


def test_render_report_includes_preflight_questions():
    entries = load_entries(Path("analysis/KILLED_STRATEGIES.md"))
    report = render_report("CB meeting directional drift in SBER", entries)

    assert "CALENDAR_CB" in report
    assert "new market fact" in report
    assert "objective oracle" in report


def test_missing_evidence_detects_bad_paths(tmp_path: Path):
    root = tmp_path
    (root / "ok.md").write_text("ok\n", encoding="utf-8")
    md = root / "killed.md"
    md.write_text(
        "| strategy | class | instruments / horizon | verdict | primary kill reason | evidence |\n"
        "|---|---|---|---|---|---|\n"
        "| X | class | inst | KILL | reason | `ok.md`, `missing.md` |\n",
        encoding="utf-8",
    )

    missing = missing_evidence(load_entries(md), root=root)

    assert missing == [("X", "missing.md")]


def test_real_killed_strategy_evidence_paths_exist():
    entries = load_entries(Path("analysis/KILLED_STRATEGIES.md"))

    assert missing_evidence(entries, root=Path(".")) == []
