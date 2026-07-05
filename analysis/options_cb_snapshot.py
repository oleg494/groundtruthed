"""Read-only option-chain snapshot for the CBR-vol research pipeline.

The module deliberately separates pure math helpers from live API calls so the
parity/oracle layer is testable offline. CLI makes only read-only REST calls:

    python -m analysis.options_cb_snapshot --out analysis/options_cb_snapshot.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent

DEFAULT_UNDERLYING = "Si"
DEFAULT_FUT_UID = "574d37d8-9de4-423a-9e33-b936002d8bda"  # Si-9.26
DEFAULT_EXPIRY = "2026-09-18"


def to_f(v):
    if not v:
        return None
    if isinstance(v, dict):
        if v.get("value") not in (None, ""):
            return float(v["value"])
        return int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9
    return None


def load_token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no token")


def call(method: str, payload: dict, token: str, retries: int = 5) -> dict:
    url = f"{BASE}/tinkoff.public.invest.api.contract.v1.{method}"
    data = json.dumps(payload).encode()
    delay = 2.0
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(float(e.headers.get("Retry-After", delay)))
                delay = min(delay * 2, 20)
                continue
            raise
    raise RuntimeError("retries exhausted")


def chain_pairs(instruments: list[dict]) -> dict[float, dict[str, str]]:
    """Return strike -> {'C': call_uid, 'P': put_uid} from option instruments."""
    out: dict[float, dict[str, str]] = {}
    for ins in instruments:
        name = ins.get("name", "")
        m = re.search(r"\b(CALL|PUT)\s+([0-9.]+)", name)
        if not m:
            continue
        strike = float(m.group(2))
        side = "C" if m.group(1) == "CALL" else "P"
        out.setdefault(strike, {})[side] = ins["uid"]
    return dict(sorted(out.items()))


def _date_token(name: str) -> str | None:
    m = re.search(r"(\d{2}\.\d{2})", name)
    return m.group(1) if m else None


def _complete_pair_count(instruments: list[dict]) -> int:
    return sum(1 for pair in chain_pairs(instruments).values() if "C" in pair and "P" in pair)


def _expiry_tokens(expiry: str, tolerance_days: int) -> set[str]:
    d = datetime.fromisoformat(expiry).date()
    return {
        (d + timedelta(days=offset)).strftime("%d.%m")
        for offset in range(-tolerance_days, tolerance_days + 1)
    }


def filter_chain(instruments: list[dict], ticker_prefix: str, expiry: str,
                 tolerance_days: int = 1) -> list[dict]:
    """Filter broad Options() output to one underlying prefix and expiry window."""
    tokens = _expiry_tokens(expiry, tolerance_days)
    out = []
    for ins in instruments:
        ticker = ins.get("ticker", "")
        name = ins.get("name", "")
        if not ticker.startswith(ticker_prefix):
            continue
        if not any(token in name for token in tokens):
            continue
        out.append(ins)
    return out


def select_expiry_chain(instruments: list[dict], expiry: str) -> list[dict]:
    """Choose one option date from a tolerance-filtered broad option list."""
    exact = datetime.fromisoformat(expiry).date().strftime("%d.%m")
    by_token: dict[str, list[dict]] = {}
    for ins in instruments:
        token = _date_token(ins.get("name", ""))
        if token:
            by_token.setdefault(token, []).append(ins)
    if not by_token:
        return instruments

    exact_chain = by_token.get(exact, [])
    if _complete_pair_count(exact_chain) >= 1:
        return exact_chain

    best_token = max(
        by_token,
        key=lambda token: (_complete_pair_count(by_token[token]), len(by_token[token]), token),
    )
    return by_token[best_token]


def parity_fit(rows: list[dict]) -> dict:
    """Fit premium-style parity: K+C-P = D*F + (1-D)*K."""
    pts = [(r["K"], r["K"] + r["C"] - r["P"]) for r in rows]
    if len(pts) < 2:
        raise ValueError("need at least two call/put pairs")
    n = len(pts)
    sx = sum(k for k, _ in pts)
    sy = sum(v for _, v in pts)
    sxx = sum(k * k for k, _ in pts)
    sxy = sum(k * v for k, v in pts)
    denom = n * sxx - sx * sx
    if denom == 0:
        raise ValueError("strikes must vary")
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    ybar = sy / n
    sst = sum((v - ybar) ** 2 for _, v in pts)
    sse = sum((v - (intercept + slope * k)) ** 2 for k, v in pts)
    r2 = 1.0 if sst == 0 else 1 - sse / sst
    discount = 1 - slope
    forward = intercept / discount if discount else float("nan")
    return {"intercept": intercept, "slope": slope, "discount": discount,
            "forward": forward, "r2": r2, "resid_sd": math.sqrt(sse / n)}


def _parity_residual(row: dict, fit: dict) -> float:
    y = row["K"] + row["C"] - row["P"]
    return y - (fit["intercept"] + fit["slope"] * row["K"])


def robust_parity_fit(rows: list[dict], mad_multiplier: float = 8.0,
                      min_abs_residual: float = 0.05) -> dict:
    """Return ordinary parity fit plus a MAD-filtered fit and outlier list."""
    fit = parity_fit(rows)
    if len(rows) < 4:
        return {"fit": fit, "robust_fit": fit, "outliers": []}

    residuals = [_parity_residual(row, fit) for row in rows]
    center = statistics.median(residuals)
    mad = statistics.median(abs(r - center) for r in residuals)
    threshold = max(min_abs_residual, mad_multiplier * 1.4826 * mad)

    kept = []
    outliers = []
    for row, residual in zip(rows, residuals):
        if abs(residual - center) > threshold:
            outliers.append({"K": row["K"], "residual": residual})
        else:
            kept.append(row)

    robust_fit = parity_fit(kept) if outliers and len(kept) >= 2 else fit
    return {"fit": fit, "robust_fit": robust_fit, "outliers": outliers}


def market_values(uids: list[str], values: list[str], token: str) -> dict:
    out = {}
    for i in range(0, len(uids), 60):
        chunk = uids[i:i + 60]
        r = call("MarketDataService/GetMarketValues",
                 {"instrumentId": chunk, "values": values}, token)
        for ins in r.get("instruments", []):
            out[ins["instrumentUid"]] = {
                v["type"]: {"value": to_f(v.get("value")), "time": v.get("time", "")}
                for v in ins.get("values", [])
            }
    return out


def load_chain(fut_uid: str, token: str) -> list[dict]:
    fut = call("InstrumentsService/FutureBy",
               {"idType": "INSTRUMENT_ID_TYPE_UID", "id": fut_uid}, token)["instrument"]
    try:
        r = call("InstrumentsService/OptionsBy",
                 {"basicAssetPositionUid": fut["basicAssetPositionUid"]}, token)
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
        r = call("InstrumentsService/Options", {}, token)
    return r.get("instruments", [])


def build_snapshot(instruments: list[dict], values: dict, underlying: str,
                   expiry: str) -> dict:
    pairs = chain_pairs(instruments)
    rows = []
    for strike, pair in pairs.items():
        cu, pu = pair.get("C"), pair.get("P")
        c = values.get(cu, {}).get("INSTRUMENT_VALUE_THEOR_PRICE", {}).get("value") if cu else None
        p = values.get(pu, {}).get("INSTRUMENT_VALUE_THEOR_PRICE", {}).get("value") if pu else None
        if c is None or p is None:
            continue
        rows.append({"K": strike, "C": c, "P": p, "call_uid": cu, "put_uid": pu})
    robust = robust_parity_fit(rows) if len(rows) >= 2 else None
    chain_dates = sorted({token for token in (_date_token(ins.get("name", "")) for ins in instruments)
                          if token})
    snapshot = {"generated": datetime.now(timezone.utc).isoformat(), "underlying": underlying,
                "expiry": expiry, "pairs": len(rows), "rows": rows,
                "chain_dates": chain_dates,
                "fit": robust["fit"] if robust else None,
                "robust_fit": robust["robust_fit"] if robust else None,
                "outliers": robust["outliers"] if robust else [],
                "read_only": True}
    snapshot["quality"] = snapshot_quality(snapshot)
    return snapshot


def snapshot_quality(snapshot: dict, min_pairs: int = 20,
                     min_robust_r2: float = 0.999) -> dict[str, str]:
    pairs = int(snapshot.get("pairs") or 0)
    if pairs < min_pairs:
        return {"status": "FAIL", "reason": f"too few pairs: {pairs} < {min_pairs}"}

    robust_fit = snapshot.get("robust_fit") or {}
    robust_r2 = robust_fit.get("r2")
    if robust_r2 is None:
        return {"status": "FAIL", "reason": "missing robust parity fit"}
    if robust_r2 < min_robust_r2:
        return {"status": "FAIL", "reason": f"robust parity r2 too low: {robust_r2:.6f}"}

    outliers = snapshot.get("outliers") or []
    max_outliers = max(1, int(pairs * 0.05))
    if len(outliers) > max_outliers:
        return {"status": "FAIL",
                "reason": f"too many outliers: {len(outliers)} > {max_outliers}"}

    return {"status": "PASS", "reason": "robust parity ok"}


def render_summary(snapshot: dict) -> str:
    fit = snapshot.get("fit") or {}
    lines = [
        "# Options CB Snapshot",
        "",
        "READ-ONLY snapshot: no orders, no signals.",
        "",
        f"underlying: {snapshot.get('underlying')}",
        f"expiry: {snapshot.get('expiry')}",
        f"chain_dates: {', '.join(snapshot.get('chain_dates') or []) or 'unknown'}",
        f"pairs: {snapshot.get('pairs', 0)}",
    ]
    quality = snapshot.get("quality") or {}
    if quality:
        lines.append(f"quality: {quality.get('status')} ({quality.get('reason')})")
    if fit:
        lines.extend([
            f"discount: {fit['discount']:.6f}",
            f"forward: {fit['forward']:.4f}",
            f"parity_r2: {fit['r2']:.6f}",
            f"parity_resid_sd: {fit['resid_sd']:.6f}",
        ])
    robust_fit = snapshot.get("robust_fit") or {}
    outliers = snapshot.get("outliers") or []
    if robust_fit and robust_fit != fit:
        lines.extend([
            f"robust_discount: {robust_fit['discount']:.6f}",
            f"robust_forward: {robust_fit['forward']:.4f}",
            f"robust_parity_r2: {robust_fit['r2']:.6f}",
            f"outliers: {len(outliers)}",
        ])
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="read-only option chain snapshot")
    p.add_argument("--fut-uid", default=DEFAULT_FUT_UID)
    p.add_argument("--underlying", default=DEFAULT_UNDERLYING)
    p.add_argument("--expiry", default=DEFAULT_EXPIRY)
    p.add_argument("--out", default="analysis/options_cb_snapshot.json")
    p.add_argument("--md", default=None)
    args = p.parse_args(argv)

    token = load_token()
    instruments = select_expiry_chain(
        filter_chain(load_chain(args.fut_uid, token), args.underlying, args.expiry),
        args.expiry,
    )
    pairs = chain_pairs(instruments)
    uids = [uid for pair in pairs.values() for uid in pair.values()]
    vals = market_values(uids, ["INSTRUMENT_VALUE_THEOR_PRICE"], token)
    snap = build_snapshot(instruments, vals, args.underlying, args.expiry)
    Path(args.out).write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md:
        Path(args.md).write_text(render_summary(snap), encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
