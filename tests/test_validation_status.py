import os
from pathlib import Path

from analysis.validation_status import collect_status, has_blocking_status, render_markdown


# --- outcomes-файл как primary source outcome (от run_all_validations) ---------


def test_outcomes_file_overrides_parsed_outcome(tmp_path: Path):
    """Если в outcomes-файле есть запись для скрипта — она primary, парсинг result-файла не нужен."""
    now = 2_000_000.0
    (tmp_path / "x_validate.py").write_text("# validate\n", encoding="utf-8")
    # result-файл без явного маркера → парсер дал бы UNKNOWN
    (tmp_path / "x_result.md").write_text("совпадение в пределах 1 б.п.\n", encoding="utf-8")
    os.utime(tmp_path / "x_result.md", (now, now))
    # outcomes-файл с явным PASS от раннера
    outcomes = tmp_path / "validation_outcomes.jsonl"
    outcomes.write_text(
        '{"script":"x_validate.py","outcome":"PASS","run_at":"2026-07-05T14:00:00"}\n',
        encoding="utf-8",
    )

    rows = collect_status(tmp_path, now=now, outcomes_path=outcomes)

    assert rows[0].outcome == "PASS"
    assert rows[0].state == "FRESH"  # mtime result-файла свежий


def test_outcomes_file_fallback_to_parse_when_no_entry(tmp_path: Path):
    """Нет записи в outcomes для скрипта → fallback на парсинг result-файла."""
    now = 2_000_000.0
    (tmp_path / "x_validate.py").write_text("# validate\n", encoding="utf-8")
    (tmp_path / "x_result.md").write_text("PASS\n", encoding="utf-8")
    os.utime(tmp_path / "x_result.md", (now, now))
    outcomes = tmp_path / "validation_outcomes.jsonl"
    outcomes.write_text(
        '{"script":"other_validate.py","outcome":"PASS","run_at":"2026-07-05T14:00:00"}\n',
        encoding="utf-8",
    )

    rows = collect_status(tmp_path, now=now, outcomes_path=outcomes)

    assert rows[0].outcome == "PASS"  # из парсинга result-файла


def test_outcomes_file_missing_falls_back_to_parse(tmp_path: Path):
    """Outcomes-файла нет → fallback на парсинг result-файла (текущее поведение)."""
    now = 2_000_000.0
    (tmp_path / "x_validate.py").write_text("# validate\n", encoding="utf-8")
    (tmp_path / "x_result.md").write_text("WARN\n", encoding="utf-8")
    os.utime(tmp_path / "x_result.md", (now, now))

    rows = collect_status(tmp_path, now=now, outcomes_path=tmp_path / "nope.jsonl")

    assert rows[0].outcome == "WARN"


def test_outcomes_fail_is_blocking(tmp_path: Path):
    """FAIL из outcomes-файла — блокирующий статус, как и из парсинга."""
    now = 2_000_000.0
    (tmp_path / "x_validate.py").write_text("# validate\n", encoding="utf-8")
    (tmp_path / "x_result.md").write_text("без маркера\n", encoding="utf-8")
    os.utime(tmp_path / "x_result.md", (now, now))
    outcomes = tmp_path / "validation_outcomes.jsonl"
    outcomes.write_text(
        '{"script":"x_validate.py","outcome":"FAIL","run_at":"2026-07-05T14:00:00"}\n',
        encoding="utf-8",
    )

    rows = collect_status(tmp_path, now=now, outcomes_path=outcomes)

    assert rows[0].outcome == "FAIL"
    assert has_blocking_status(rows)


def test_outcomes_last_entry_wins_for_duplicate_script(tmp_path: Path):
    """Если в outcomes несколько записей для одного скрипта — последняя по run_at wins."""
    now = 2_000_000.0
    (tmp_path / "x_validate.py").write_text("# validate\n", encoding="utf-8")
    (tmp_path / "x_result.md").write_text("без маркера\n", encoding="utf-8")
    os.utime(tmp_path / "x_result.md", (now, now))
    outcomes = tmp_path / "validation_outcomes.jsonl"
    outcomes.write_text(
        '{"script":"x_validate.py","outcome":"FAIL","run_at":"2026-07-04T10:00:00"}\n'
        '{"script":"x_validate.py","outcome":"PASS","run_at":"2026-07-05T14:00:00"}\n',
        encoding="utf-8",
    )

    rows = collect_status(tmp_path, now=now, outcomes_path=outcomes)

    assert rows[0].outcome == "PASS"  # последний по run_at


# --- run_all_validations: запись outcomes-файла после прогона -----------------


def test_write_outcomes_persists_script_outcome_run_at(tmp_path: Path):
    """Раннер пишет JSONL: одна строка на скрипт с outcome и run_at."""
    import json
    from analysis.run_all_validations import write_outcomes

    results = [
        ("a_validate.py", "PASS", 1.2),
        ("b_validate.py", "WARN", 0.5),
        ("c_validate.py", "FAIL", 3.0),
    ]
    out = tmp_path / "validation_outcomes.jsonl"
    run_at = "2026-07-05T14:00:00"

    write_outcomes(results, out, run_at=run_at)

    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    recs = [json.loads(ln) for ln in lines]
    assert recs[0] == {"script": "a_validate.py", "outcome": "PASS", "run_at": run_at}
    assert recs[1] == {"script": "b_validate.py", "outcome": "WARN", "run_at": run_at}
    assert recs[2] == {"script": "c_validate.py", "outcome": "FAIL", "run_at": run_at}


def test_write_outcomes_skips_non_terminal_tags(tmp_path: Path):
    """TIME/ERR/MISS (процессные состояния) не пишем в outcomes — это не outcome оракула."""
    import json
    from analysis.run_all_validations import write_outcomes

    results = [
        ("ok_validate.py", "PASS", 1.0),
        ("timeout_validate.py", "TIME", 300.0),
        ("err_validate.py", "ERR", 0.1),
        ("miss_validate.py", "MISS", 0.0),
    ]
    out = tmp_path / "validation_outcomes.jsonl"

    write_outcomes(results, out, run_at="2026-07-05T14:00:00")

    recs = [json.loads(ln) for ln in out.read_text(encoding="utf-8").strip().splitlines()]
    assert [r["script"] for r in recs] == ["ok_validate.py"]  # только PASS/WARN/FAIL


def test_main_help_does_not_run_validators(tmp_path: Path, capsys, monkeypatch):
    """``--help`` печатает справку и НЕ запускает валидаторы.

    Регресс: раннер парсил sys.argv вручную, --help молча игнорировался как
    неизвестный флаг, и шёл полный live-прогон всех 27 оракулов (сеть, минуты).
    Тест направляет outcomes-выход во временный файл, чтобы не мутировать рабочий кэш.
    """
    import analysis.run_all_validations as rav
    ran = {"called": False}

    def _boom(*a, **kw):
        ran["called"] = True
        raise AssertionError("subprocess.run не должен вызываться при --help")

    monkeypatch.setattr(rav.subprocess, "run", _boom)
    monkeypatch.setattr(rav.sys, "argv",
                        ["run_all_validations.py", "--help",
                         "--outcomes-out", str(tmp_path / "noop.jsonl")])

    try:
        rav.main()
    except SystemExit as e:
        # --help должен выйти с кодом 0, не запускать прогон
        assert e.code == 0
    captured = capsys.readouterr()
    assert "usage" in captured.out.lower() or "использование" in captured.out.lower()
    assert not ran["called"], "subprocess.run вызывался — --help не должен гнать валидаторы"


def test_write_outcomes_does_not_clobber_existing_with_empty(tmp_path: Path):
    """Guard: не перезаписывать существующий непустой outcomes-файл пустым контентом.

    Регресс: если все results не-терминальные (TIME/ERR/MISS — mock-прогон, сбой,
    отсутствие файла), write_outcomes писал пустой файл и убивал 32 записи
    предыдущего live-прогона. Guard: сохраняет существующий, пишет в .tmp.
    """
    import json
    from analysis.run_all_validations import write_outcomes

    out = tmp_path / "validation_outcomes.jsonl"
    out.write_text(
        '{"script":"a_validate.py","outcome":"PASS","run_at":"2026-07-05T10:00:00"}\n',
        encoding="utf-8",
    )
    # все не-терминальные → раньше затёр бы файл пустотой
    write_outcomes([("b_validate.py", "TIME", 1.0), ("c_validate.py", "ERR", 0.1)],
                   out, run_at="2026-07-05T17:00:00")

    recs = [json.loads(ln) for ln in out.read_text(encoding="utf-8").strip().splitlines() if ln.strip()]
    assert len(recs) == 1, "существующий файл затёрт — guard не сработал"
    assert recs[0]["script"] == "a_validate.py"


def test_write_outcomes_with_terminal_results_replaces_existing(tmp_path: Path):
    """При наличии терминальных outcomes файл перезаписывается (последний прогон = актуальный)."""
    import json
    from analysis.run_all_validations import write_outcomes

    out = tmp_path / "validation_outcomes.jsonl"
    out.write_text(
        '{"script":"old_validate.py","outcome":"FAIL","run_at":"2026-07-01T10:00:00"}\n',
        encoding="utf-8",
    )
    write_outcomes([("new_validate.py", "PASS", 1.0)],
                   out, run_at="2026-07-05T17:00:00")

    recs = [json.loads(ln) for ln in out.read_text(encoding="utf-8").strip().splitlines() if ln.strip()]
    assert [r["script"] for r in recs] == ["new_validate.py"]


def test_main_unknown_flag_errors_and_does_not_run(tmp_path: Path, capsys, monkeypatch):
    """Неизвестный флаг → exit code 2 со справкой, без запуска валидаторов.

    Регресс: раннер парсил sys.argv вручную, опечатка в флаге (--fas вместо --fast)
    молча игнорировалась → шёл полный live-прогон с НЕ-теми параметрами.
    """
    import analysis.run_all_validations as rav
    ran = {"called": False}

    def _boom(*a, **kw):
        ran["called"] = True
        raise AssertionError("subprocess.run не должен вызываться при неизвестном флаге")

    monkeypatch.setattr(rav.subprocess, "run", _boom)
    monkeypatch.setattr(rav.sys, "argv",
                        ["run_all_validations.py", "--fas",  # опечатка
                         "--outcomes-out", str(tmp_path / "noop.jsonl")])

    rc = 0
    try:
        rav.main()
    except SystemExit as e:
        rc = e.code
    captured = capsys.readouterr()
    assert rc == 2, f"неизвестный флаг должен выходить с code 2, got {rc}"
    assert "неизвестный" in captured.err.lower() or "неизвестный" in captured.out.lower()
    assert not ran["called"], "subprocess.run вызывался — неизвестный флаг не должен гнать валидаторы"


def test_collect_status_marks_fresh_stale_and_missing(tmp_path: Path):
    now = 2_000_000.0
    fresh = tmp_path / "fresh_validate.py"
    stale = tmp_path / "stale_validate.py"
    missing = tmp_path / "missing_validate.py"
    for p in (fresh, stale, missing):
        p.write_text("# validate\n", encoding="utf-8")

    fresh_result = tmp_path / "fresh_result.md"
    stale_result = tmp_path / "stale_result.md"
    fresh_result.write_text("PASS\n", encoding="utf-8")
    stale_result.write_text("WARN\n", encoding="utf-8")
    os.utime(fresh_result, (now - 2 * 86400, now - 2 * 86400))
    os.utime(stale_result, (now - 10 * 86400, now - 10 * 86400))

    rows = collect_status(tmp_path, now=now, stale_days=7)
    by_script = {r.script: r for r in rows}

    assert by_script["fresh_validate.py"].state == "FRESH"
    assert by_script["fresh_validate.py"].outcome == "PASS"
    assert by_script["fresh_validate.py"].age_days == 2
    assert by_script["stale_validate.py"].state == "STALE"
    assert by_script["stale_validate.py"].outcome == "WARN"
    assert by_script["missing_validate.py"].state == "MISSING"
    assert by_script["missing_validate.py"].outcome == ""


def test_render_markdown_includes_summary_counts(tmp_path: Path):
    now = 2_000_000.0
    (tmp_path / "x_validate.py").write_text("# validate\n", encoding="utf-8")
    (tmp_path / "x_result.md").write_text("PASS\n", encoding="utf-8")
    os.utime(tmp_path / "x_result.md", (now, now))

    md = render_markdown(collect_status(tmp_path, now=now), generated_at="2026-07-05")

    assert "# Validation Status" in md
    assert "FRESH: 1" in md
    assert "PASS: 1" in md
    assert "| x_validate.py | x_result.md | FRESH | PASS | 0 |" in md


def test_collect_status_uses_last_outcome_marker(tmp_path: Path):
    now = 2_000_000.0
    (tmp_path / "x_validate.py").write_text("# validate\n", encoding="utf-8")
    result = tmp_path / "x_result.md"
    result.write_text("old table: FAIL\n\nfinal verdict: PASS\n", encoding="utf-8")
    os.utime(result, (now, now))

    rows = collect_status(tmp_path, now=now)

    assert rows[0].outcome == "PASS"


def test_run_all_can_write_validation_status(tmp_path: Path):
    from analysis.run_all_validations import write_validation_status

    (tmp_path / "x_validate.py").write_text("# validate\n", encoding="utf-8")
    (tmp_path / "x_validate_result.md").write_text("PASS\n", encoding="utf-8")
    out = tmp_path / "status.md"

    rc = write_validation_status(root=tmp_path, out=out, stale_days=7)

    assert rc == 0
    assert out.exists()
    assert "MISSING: 0" in out.read_text(encoding="utf-8")


def test_run_all_status_fails_on_fail_outcome(tmp_path: Path):
    from analysis.run_all_validations import write_validation_status

    (tmp_path / "x_validate.py").write_text("# validate\n", encoding="utf-8")
    (tmp_path / "x_result.md").write_text("final verdict: FAIL\n", encoding="utf-8")

    rc = write_validation_status(root=tmp_path, out=tmp_path / "status.md", stale_days=7)

    assert rc == 1


def test_has_blocking_status_flags_missing_and_fail(tmp_path: Path):
    now = 2_000_000.0
    (tmp_path / "fail_validate.py").write_text("# validate\n", encoding="utf-8")
    (tmp_path / "fail_result.md").write_text("FAIL\n", encoding="utf-8")
    (tmp_path / "missing_validate.py").write_text("# validate\n", encoding="utf-8")

    rows = collect_status(tmp_path, now=now)

    assert has_blocking_status(rows)
