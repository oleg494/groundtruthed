"""Тесты backtest/dashboard.py."""
from pathlib import Path

from backtest import candles, run, strategies
from backtest.dashboard import build_dashboard, load_results, save_dashboard
from backtest.export import to_json


def _make_json(tmp: Path, strategy: str = "buyhold", seed: int = 1) -> Path:
    """Прогон + to_json в tmp, возвращает путь."""
    data = candles.gbm("SYN", bars=200, seed=seed)
    strat = strategies.build(strategy)
    res = run(strat, data, cash=100_000)
    p = tmp / f"{strategy}_{seed}.json"
    to_json(res, str(p))
    return p


def test_load_results_finds_json(tmp_path):
    _make_json(tmp_path, "buyhold", 1)
    _make_json(tmp_path, "sma_cross", 2)
    results = load_results(str(tmp_path))
    assert len(results) == 2
    for r in results:
        assert "metrics" in r
        assert "strategy" in r
        assert "_file" in r


def test_load_results_empty_dir(tmp_path):
    results = load_results(str(tmp_path))
    assert results == []


def test_load_results_ignores_non_backtest_json(tmp_path):
    # JSON без ключа metrics — должен быть проигнорирован
    (tmp_path / "other.json").write_text('{"foo": 1}', encoding="utf-8")
    results = load_results(str(tmp_path))
    assert results == []


def test_load_results_recurse_subdir(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    _make_json(sub, "buyhold", 5)
    results = load_results(str(tmp_path))
    assert len(results) == 1


def test_build_dashboard_empty():
    html = build_dashboard([])
    assert "Нет данных" in html


def test_build_dashboard_returns_html(tmp_path):
    _make_json(tmp_path, "buyhold", 1)
    _make_json(tmp_path, "donchian", 2)
    results = load_results(str(tmp_path))
    html = build_dashboard(results, title="Test Dashboard")
    assert "Test Dashboard" in html
    assert "<table" in html
    assert "buyhold" in html
    assert "donchian" in html


def test_build_dashboard_has_sparkline(tmp_path):
    _make_json(tmp_path, "sma_cross", 3)
    results = load_results(str(tmp_path))
    html = build_dashboard(results)
    assert "<svg" in html
    assert "<polyline" in html


def test_build_dashboard_metrics_present(tmp_path):
    _make_json(tmp_path, "macd", 7)
    results = load_results(str(tmp_path))
    html = build_dashboard(results)
    # все заголовки колонок должны быть в таблице
    for header in ("CAGR", "Sharpe", "MaxDD", "WinRate", "Trades"):
        assert header in html


def test_save_dashboard_writes_file(tmp_path):
    _make_json(tmp_path, "buyhold", 1)
    out = tmp_path / "dash.html"
    n = save_dashboard(str(tmp_path), str(out))
    assert n == 1
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "buyhold" in content


def test_save_dashboard_zero_on_empty(tmp_path):
    out = tmp_path / "dash.html"
    n = save_dashboard(str(tmp_path), str(out))
    assert n == 0
    assert "Нет данных" in out.read_text(encoding="utf-8")


def test_build_dashboard_heat_colors(tmp_path):
    # прогоны с разными метриками → должны быть разные цвета тепловой карты
    for seed in range(1, 5):
        _make_json(tmp_path, "buyhold", seed)
    results = load_results(str(tmp_path))
    html = build_dashboard(results)
    # хотя бы два разных rgba должны присутствовать
    rgba_count = html.count("background:rgba")
    assert rgba_count >= 2
