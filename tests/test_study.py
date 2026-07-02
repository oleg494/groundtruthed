"""Тесты капстоун-пайплайна study (волна 3)."""
from backtest import candles
from backtest.study import run_study, text_study
from backtest.report import study_html


def _study():
    data = candles.gbm("X", bars=1000, seed=3)
    return run_study("sma_cross", data, {"fast": [10, 20, 30], "slow": [50, 80, 120]},
                     metric="sharpe", n_splits=3, mc_n=300)


def test_run_study_assembles_all_parts():
    s = _study()
    assert s.best_params["fast"] < s.best_params["slow"]
    assert len(s.grid_points) >= 1
    assert len(s.walkforward.windows) == 3
    assert 0.0 <= s.robustness.deflated_sharpe <= 1.0
    assert s.montecarlo.ret_p95 >= s.montecarlo.ret_p5
    assert "ratio" in s.oos


def test_verdict_is_string():
    s = _study()
    v = s.verdict()
    assert isinstance(v, str) and len(v) > 0


def test_text_study_has_sections():
    txt = text_study(_study())
    assert "walk-forward" in txt
    assert "Monte-Carlo" in txt
    assert "ВЕРДИКТ" in txt


def test_study_html_smoke():
    html = study_html(_study())
    assert html.lower().startswith("<!doctype")
    assert "<svg" in html
    assert "study" in html
