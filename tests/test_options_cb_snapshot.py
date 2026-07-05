import subprocess
import sys
import urllib.error

import analysis.options_cb_snapshot as snap_mod
from analysis.options_cb_snapshot import (
    build_snapshot,
    chain_pairs,
    filter_chain,
    load_chain,
    parity_fit,
    robust_parity_fit,
    render_summary,
    select_expiry_chain,
    snapshot_quality,
)


def test_chain_pairs_groups_call_put_by_strike():
    instruments = [
        {"uid": "c75", "name": "Si CALL 75.000 17.09.2026"},
        {"uid": "p75", "name": "Si PUT 75.000 17.09.2026"},
        {"uid": "c76", "name": "Si CALL 76 17.09.2026"},
        {"uid": "junk", "name": "not an option"},
    ]

    pairs = chain_pairs(instruments)

    assert pairs == {
        75.0: {"C": "c75", "P": "p75"},
        76.0: {"C": "c76"},
    }


def test_parity_fit_recovers_discount_and_forward():
    discount = 0.96
    forward = 80.0
    rows = []
    for strike in (70.0, 75.0, 80.0, 85.0):
        f_impl = discount * forward + (1 - discount) * strike
        rows.append({"K": strike, "C": max(forward - strike, 0) * discount + 1.0,
                     "P": strike + max(forward - strike, 0) * discount + 1.0 - f_impl})

    fit = parity_fit(rows)

    assert round(fit["discount"], 6) == discount
    assert round(fit["forward"], 6) == forward
    assert fit["r2"] > 0.999999


def test_robust_parity_fit_flags_single_bad_strike():
    rows = []
    for strike in range(70, 91):
        strike = float(strike)
        rows.append({"K": strike, "C": 80.0 - 0.03 * strike, "P": 0.0})
    rows[-1] = {**rows[-1], "C": rows[-1]["C"] + 0.75}

    result = robust_parity_fit(rows)

    assert [x["K"] for x in result["outliers"]] == [90.0]
    assert result["robust_fit"]["resid_sd"] < result["fit"]["resid_sd"] / 100
    assert result["robust_fit"]["r2"] > 0.999999


def test_render_summary_mentions_read_only_and_counts():
    summary = render_summary({
        "underlying": "Si",
        "expiry": "2026-09-18",
        "pairs": 4,
        "fit": {"discount": 0.96, "forward": 80.0, "r2": 1.0, "resid_sd": 0.0},
    })

    assert "READ-ONLY" in summary
    assert "pairs: 4" in summary
    assert "forward: 80.0000" in summary


def test_snapshot_quality_passes_good_robust_fit_with_one_outlier():
    snapshot = {
        "pairs": 44,
        "fit": {"r2": 0.8},
        "robust_fit": {"r2": 0.99999},
        "outliers": [{"K": 87.5, "residual": 0.66}],
    }

    quality = snapshot_quality(snapshot)

    assert quality == {"status": "PASS", "reason": "robust parity ok"}


def test_snapshot_quality_fails_low_pairs_or_many_outliers():
    assert snapshot_quality({"pairs": 19, "robust_fit": {"r2": 1.0}, "outliers": []}) == {
        "status": "FAIL",
        "reason": "too few pairs: 19 < 20",
    }

    quality = snapshot_quality({
        "pairs": 40,
        "robust_fit": {"r2": 0.9999},
        "outliers": [{"K": k, "residual": 1.0} for k in range(4)],
    })

    assert quality == {"status": "FAIL", "reason": "too many outliers: 4 > 2"}


def test_build_snapshot_skips_incomplete_pairs_and_missing_theor_price():
    instruments = [
        {"uid": "c75", "name": "Si CALL 75 17.09.2026"},
        {"uid": "p75", "name": "Si PUT 75 17.09.2026"},
        {"uid": "c76", "name": "Si CALL 76 17.09.2026"},
        {"uid": "p76", "name": "Si PUT 76 17.09.2026"},
        {"uid": "c77", "name": "Si CALL 77 17.09.2026"},
    ]
    values = {
        "c75": {"INSTRUMENT_VALUE_THEOR_PRICE": {"value": 5.0}},
        "p75": {"INSTRUMENT_VALUE_THEOR_PRICE": {"value": 1.0}},
        "c76": {"INSTRUMENT_VALUE_THEOR_PRICE": {"value": None}},
        "p76": {"INSTRUMENT_VALUE_THEOR_PRICE": {"value": 2.0}},
    }

    snap = build_snapshot(instruments, values, "Si", "2026-09-18")

    assert snap["pairs"] == 1
    assert snap["rows"][0]["K"] == 75.0
    assert snap["fit"] is None


def test_cli_help_does_not_require_token_or_network():
    out = subprocess.run([sys.executable, "-m", "analysis.options_cb_snapshot", "--help"],
                         capture_output=True, text=True, timeout=10)

    assert out.returncode == 0
    assert "read-only option chain snapshot" in out.stdout


def test_filter_chain_keeps_underlying_and_expiry_window():
    instruments = [
        {"ticker": "Si75CU6", "name": "Доллар CALL 75₽ 17.09"},
        {"ticker": "Si75PU6", "name": "Доллар PUT 75₽ 18.09"},
        {"ticker": "Si75CZ6", "name": "Доллар CALL 75₽ 20.12"},
        {"ticker": "PS2600CL8", "name": "Positive Technologies CALL 2600₽ 20.12"},
    ]

    filtered = filter_chain(instruments, "Si", "2026-09-18", tolerance_days=1)

    assert [x["ticker"] for x in filtered] == ["Si75CU6", "Si75PU6"]


def test_select_expiry_chain_prefers_exact_date_when_available():
    instruments = [
        {"ticker": "Si75C-near", "uid": "c17", "name": "Доллар CALL 75₽ 17.09"},
        {"ticker": "Si75P-near", "uid": "p17", "name": "Доллар PUT 75₽ 17.09"},
        {"ticker": "Si75C-exact", "uid": "c18", "name": "Доллар CALL 75₽ 18.09"},
        {"ticker": "Si75P-exact", "uid": "p18", "name": "Доллар PUT 75₽ 18.09"},
    ]

    selected = select_expiry_chain(instruments, "2026-09-18")

    assert [x["ticker"] for x in selected] == ["Si75C-exact", "Si75P-exact"]


def test_select_expiry_chain_falls_back_to_best_complete_neighbor():
    instruments = [
        {"ticker": "Si75C-near", "uid": "c17", "name": "Доллар CALL 75₽ 17.09"},
        {"ticker": "Si75P-near", "uid": "p17", "name": "Доллар PUT 75₽ 17.09"},
        {"ticker": "Si80C-near", "uid": "c17b", "name": "Доллар CALL 80₽ 17.09"},
        {"ticker": "Si80P-near", "uid": "p17b", "name": "Доллар PUT 80₽ 17.09"},
        {"ticker": "Si75C-exact", "uid": "c18", "name": "Доллар CALL 75₽ 18.09"},
    ]

    selected = select_expiry_chain(instruments, "2026-09-18")

    assert [x["ticker"] for x in selected] == [
        "Si75C-near",
        "Si75P-near",
        "Si80C-near",
        "Si80P-near",
    ]


def test_load_chain_falls_back_to_options_when_optionsby_rejects(monkeypatch):
    calls = []

    def fake_call(method, payload, token, retries=5):
        calls.append((method, payload))
        if method.endswith("FutureBy"):
            return {"instrument": {"basicAssetPositionUid": "pos"}}
        if method.endswith("OptionsBy"):
            raise urllib.error.HTTPError("url", 400, "bad", {}, None)
        if method.endswith("Options"):
            return {"instruments": [{"ticker": "Si75CU6", "name": "Доллар CALL 75₽ 17.09"}]}
        raise AssertionError(method)

    monkeypatch.setattr(snap_mod, "call", fake_call)

    chain = load_chain("future_uid", "token")

    assert chain == [{"ticker": "Si75CU6", "name": "Доллар CALL 75₽ 17.09"}]
    assert [c[0] for c in calls] == [
        "InstrumentsService/FutureBy",
        "InstrumentsService/OptionsBy",
        "InstrumentsService/Options",
    ]
