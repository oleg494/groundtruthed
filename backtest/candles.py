"""Слой данных: синтетические свечи (детерминированные) + реальный фетч с кэшем.

Синтетика нужна, чтобы движок и тесты работали полностью офлайн и воспроизводимо:
один и тот же seed → одни и те же бары. Реальная история тянется ТОЛЬКО read-only
методом MarketDataService/GetCandles через sandbox-домен (sandbox-токен), результат
кэшируется на диск (backtest/.cache), повторный запрос сети не делает.
"""
from __future__ import annotations

import json
import math
import random
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .core import Bar

DAY = 86400
_BASE_T = 1_577_836_800            # 2020-01-01 UTC — стабильная отправная точка
ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"


# ───────────────────── синтетика ─────────────────────
def _ohlc(closes: list[float], rng: random.Random, start_t: int = _BASE_T,
          dt: int = DAY, wick: float = 0.006) -> list[Bar]:
    """Свернуть путь цен закрытия в OHLC-бары с детерминированными тенями."""
    bars = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev if i > 0 else c * (1 - rng.uniform(0, wick))
        hi = max(o, c) * (1 + rng.uniform(0, wick))
        lo = min(o, c) * (1 - rng.uniform(0, wick))
        vol = round(rng.uniform(1e4, 1e6))
        bars.append(Bar(t=start_t + i * dt, o=round(o, 6), h=round(hi, 6),
                        l=round(lo, 6), c=round(c, 6), v=vol))
        prev = c
    return bars


def gbm(ticker: str = "SYN", bars: int = 500, seed: int = 0, s0: float = 100.0,
        mu: float = 0.08, sigma: float = 0.25) -> dict[str, list[Bar]]:
    """Геометрическое броуновское движение (дневные бары). mu/sigma — годовые."""
    rng = random.Random(seed)
    dt = 1 / 252
    drift = (mu - 0.5 * sigma ** 2) * dt
    vol = sigma * math.sqrt(dt)
    closes, p = [], s0
    for _ in range(bars):
        p *= math.exp(drift + vol * rng.gauss(0, 1))
        closes.append(p)
    return {ticker: _ohlc(closes, rng)}


def trend(ticker: str = "SYN", bars: int = 500, seed: int = 0, s0: float = 100.0,
          slope: float = 0.001, noise: float = 0.01) -> dict[str, list[Bar]]:
    """Дрейф с шумом: близко к линейному росту (slope за бар) + гауссов шум."""
    rng = random.Random(seed)
    closes, p = [], s0
    for i in range(bars):
        p = s0 * (1 + slope) ** i * (1 + rng.gauss(0, noise))
        closes.append(max(p, 0.01))
    return {ticker: _ohlc(closes, rng)}


def mean_revert(ticker: str = "SYN", bars: int = 500, seed: int = 0,
                mean: float = 100.0, theta: float = 0.05,
                sigma: float = 2.0) -> dict[str, list[Bar]]:
    """Процесс Орнштейна–Уленбека: тянется к mean со скоростью theta."""
    rng = random.Random(seed)
    closes, p = [], mean
    for _ in range(bars):
        p += theta * (mean - p) + sigma * rng.gauss(0, 1)
        closes.append(max(p, 0.01))
    return {ticker: _ohlc(closes, rng)}


def sine(ticker: str = "SYN", bars: int = 500, period: int = 40,
         amp: float = 0.2, s0: float = 100.0, seed: int = 0) -> dict[str, list[Bar]]:
    """Чистый цикл: предсказуемая синусоида (для проверки контртренда)."""
    rng = random.Random(seed)
    closes = [s0 * (1 + amp * math.sin(2 * math.pi * i / period)) for i in range(bars)]
    return {ticker: _ohlc(closes, rng, wick=0.001)}


def resample(bars: list[Bar], factor: int) -> list[Bar]:
    """Склеить каждые `factor` баров в один (день→неделя при factor=5).

    open = первый open группы, high/low = экстремумы, close = последний close,
    volume = сумма, t = метка первого бара группы. Неполный хвост отбрасывается."""
    if factor <= 1:
        return list(bars)
    out = []
    for i in range(0, len(bars) - factor + 1, factor):
        grp = bars[i:i + factor]
        out.append(Bar(t=grp[0].t, o=grp[0].o,
                       h=max(b.h for b in grp), l=min(b.l for b in grp),
                       c=grp[-1].c, v=sum(b.v for b in grp)))
    return out


def resample_data(data: dict[str, list[Bar]], factor: int) -> dict[str, list[Bar]]:
    """resample для всего словаря тикер→бары."""
    return {t: resample(bars, factor) for t, bars in data.items()}


def basket(tickers: list[str], bars: int = 500, seed: int = 0,
           **kw) -> dict[str, list[Bar]]:
    """Несколько независимых GBM-лент с общей временной осью."""
    out: dict[str, list[Bar]] = {}
    for k, t in enumerate(tickers):
        out.update(gbm(t, bars=bars, seed=seed + k * 101, **kw))
    return out


def parse_synthetic(spec: str) -> dict[str, list[Bar]]:
    """Распарсить CLI-спеку синтетики.

    'gbm:750:1'        → kind=gbm, bars=750, seed=1 (одиночный тикер SYN);
    'trend:500:2', 'mean_revert:..', 'sine:..' — аналогично;
    'basket:5:800:2'   → корзина из 5 тикеров, 800 баров, seed=2.
    """
    parts = spec.split(":")
    kind = parts[0]
    if kind == "basket":
        count = int(parts[1]) if len(parts) > 1 else 4
        bars = int(parts[2]) if len(parts) > 2 else 500
        seed = int(parts[3]) if len(parts) > 3 else 0
        names = [chr(ord("A") + k) for k in range(count)]
        return basket(names, bars=bars, seed=seed)
    bars = int(parts[1]) if len(parts) > 1 else 500
    seed = int(parts[2]) if len(parts) > 2 else 0
    gen = {"gbm": gbm, "trend": trend, "mean_revert": mean_revert,
           "sine": sine}.get(kind)
    if gen is None:
        raise ValueError(f"неизвестный генератор {kind!r}")
    return gen("SYN", bars=bars, seed=seed)


# ───────────────────── реальная история (read-only, кэш) ─────────────────────
_SANDBOX = "https://sandbox-invest-public-api.tinkoff.ru/rest"


def _token() -> str:
    for p in (ROOT.parent / ".env", ROOT / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith("TINVEST_SANDBOX_KEY="):
                    tok = line.split("=", 1)[1].strip()
                    if tok:
                        return tok
    raise SystemExit("нет TINVEST_SANDBOX_KEY в .env — фетч недоступен (синтетика работает без него)")


def _to_f(v) -> float:
    if isinstance(v, dict):
        return int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9
    return float(v or 0)


def from_tinvest(uid: str, ticker: str, days: int = 365,
                 interval: str = "CANDLE_INTERVAL_DAY",
                 use_cache: bool = True) -> dict[str, list[Bar]]:
    """Скачать дневные/внутридневные свечи по uid через sandbox-домен (read-only) с диск-кэшем.

    Чтобы НЕ дёргать сеть в тестах/демо, кэшируем по ключу uid|interval|days|дата.
    Это единственное место в пакете, где есть сеть, и оно опциональное.
    """
    CACHE.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    key = CACHE / f"{ticker}_{interval}_{days}_{today}.json"
    if use_cache and key.exists():
        raw = json.loads(key.read_text(encoding="utf-8"))
        return {ticker: [Bar(**b) for b in raw]}

    now = datetime.now(timezone.utc)
    start_dt = now - timedelta(days=int(days * 1.5))
    end_dt = now

    # Определяем шаг запроса в днях согласно лимитам T-Invest API
    step_days = 365
    if "MIN" in interval:
        if any(x in interval for x in ["1_MIN", "2_MIN", "3_MIN", "5_MIN", "10_MIN"]):
            step_days = 7
        else:  # 15_MIN, 30_MIN
            step_days = 20
    elif "HOUR" in interval:
        step_days = 80
    elif "SEC" in interval:
        step_days = 1

    candles_raw = []
    curr_start = start_dt
    url = f"{_SANDBOX}/tinkoff.public.invest.api.contract.v1.MarketDataService/GetCandles"

    while curr_start < end_dt:
        curr_end = min(curr_start + timedelta(days=step_days), end_dt)
        body = {
            "instrumentId": uid, "interval": interval,
            "from": curr_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": curr_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
        req.add_header("Authorization", f"Bearer {_token()}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            candles_raw.extend(data.get("candles", []))
        
        curr_start = curr_end
        time.sleep(0.3)  # Снижаем нагрузку/лимиты

    bars = []
    for c in candles_raw:
        if not c.get("isComplete", True):
            continue
        t = int(datetime.strptime(c["time"][:19], "%Y-%m-%dT%H:%M:%S")
                .replace(tzinfo=timezone.utc).timestamp())
        bars.append(Bar(t=t, o=_to_f(c["open"]), h=_to_f(c["high"]),
                        l=_to_f(c["low"]), c=_to_f(c["close"]), v=_to_f(c.get("volume", 0))))
    bars.sort(key=lambda b: b.t)
    # Удаляем дубликаты на стыках чанков, если они возникли
    seen_ts = set()
    unique_bars = []
    for b in bars:
        if b.t not in seen_ts:
            seen_ts.add(b.t)
            unique_bars.append(b)
    
    if use_cache:
        key.write_text(json.dumps([b.__dict__ for b in unique_bars]), encoding="utf-8")
    return {ticker: unique_bars}
