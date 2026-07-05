"""Strategy Lab: forward-test слой поверх журнала (дизайн — deep/forward_test_layer_sandbox.md).

Три задачи, всё READ-ONLY (никаких заявок и корректировок):
  - reconcile: сверка журнала lab.db с фактом sandbox-счёта (позиции/заявки), dry-run отчёт;
  - метрики live-vs-backtest: частота сделок, equity-статистика, tracking, слиппедж
    по trades.fill_price, order fill-rate и partial-rate;
  - daily_report: текст дневного отчёта с алертами (просадка, тишина, серия ошибок).

    python -m lab.forward report            # офлайн, только lab.db
    python -m lab.forward reconcile         # нужен TINVEST_SANDBOX_KEY и сеть
"""
import argparse
import json
import sqlite3
import statistics
from datetime import datetime, timedelta
from pathlib import Path

from .instruments import INSTRUMENTS, futures_roll_warnings
from .journal import DB, conn

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "lab_state.json"      # runner кладёт сюда accounts {name: accountId}

# Пороги алертов (тюнить здесь; тик фермы = 60с в сессию, см. runner.TICK_OPEN)
DD_LIMIT_PCT = 5.0        # текущая просадка от пика
SILENCE_TICKS = 1000      # equity-записей без единой сделки (для buyhold это норма — алерт информационный)
ERR_STREAK = 5            # подряд идущих error/fail событий в хвосте журнала

_ERR_SQL = "(kind LIKE '%error%' OR kind LIKE '%fail%')"


def _conn(db=None) -> sqlite3.Connection:
    """Принимает готовый Connection (тесты), путь или None (боевой lab.db)."""
    if isinstance(db, sqlite3.Connection):
        return db
    return conn(db)


def _strategies(c) -> list:
    return [r[0] for r in c.execute(
        "SELECT DISTINCT strategy FROM equity UNION SELECT DISTINCT strategy FROM trades ORDER BY 1")]


# ── позиции по журналу ─────────────────────────────────────────────────────────

def journal_positions(c, strategy: str) -> dict:
    """ticker -> нетто-лоты по trades (buy +, sell −). side пишется как 'buy:SBER'.

    Best-effort: журнал — не ground truth (сделка пишется при отправке market /
    обнаружении fill лимитки; часть fills могла не попасть при падении процесса).
    Ровно поэтому и нужен reconcile против счёта."""
    out = {}
    for side, lots in c.execute("SELECT side, lots FROM trades WHERE strategy=?", (strategy,)):
        if ":" not in (side or ""):
            continue  # старый/чужой формат — не гадаем
        action, ticker = side.split(":", 1)
        sign = 1 if action == "buy" else -1
        out[ticker] = out.get(ticker, 0) + sign * int(lots)
    return {t: v for t, v in out.items() if v != 0}


# ── reconcile: журнал vs счёт ──────────────────────────────────────────────────

def _account_positions(account_id: str) -> dict:
    """ticker -> лоты на sandbox-счёте (шорт < 0). Зеркалит Ctx.positions(), но без журнала."""
    from .api import call, to_f  # lazy: офлайн-команды не должны требовать токен
    p = call("SandboxService/GetSandboxPortfolio", {"accountId": account_id})
    by_uid = {m["uid"]: t for t, m in INSTRUMENTS.items()}
    out = {}
    for pos in p.get("positions", []):
        t = by_uid.get(pos.get("instrumentUid"))
        if not t:
            continue
        lots = to_f(pos.get("quantityLots"))
        if not lots:
            lots = to_f(pos.get("quantity")) / INSTRUMENTS[t]["lot"]
        lots = int(round(lots))
        if lots:
            out[t] = lots
    return out


def _account_orders(account_id: str) -> list:
    from .api import call
    r = call("SandboxService/GetSandboxOrders", {"accountId": account_id})
    return r.get("orders", [])


def load_accounts() -> dict:
    """name -> accountId из lab_state.json (пишет runner)."""
    if not STATE.exists():
        return {}
    return json.loads(STATE.read_text(encoding="utf-8")).get("accounts", {})


def reconcile(accounts: dict | None = None, db=None) -> dict:
    """Сверка позиций журнала с фактом счёта. Только чтение, никаких корректировок.

    Возвращает {"strategies": {name: {"discrepancies": [...], "active_orders": int,
    "journal": {...}, "account": {...}}}, "ok": bool}. Расхождение — dict с полями
    strategy/ticker/journal_lots/account_lots/issue."""
    c = _conn(db)
    accounts = accounts if accounts is not None else load_accounts()
    if not accounts:
        return {"ok": False, "error": "нет счетов: lab_state.json пуст или отсутствует",
                "strategies": {}}
    result, ok = {}, True
    for name, acc in sorted(accounts.items()):
        jpos = journal_positions(c, name)
        apos = _account_positions(acc)
        orders = _account_orders(acc)
        disc = []
        for t in sorted(set(jpos) | set(apos)):
            jl, al = jpos.get(t, 0), apos.get(t, 0)
            if jl == al:
                continue
            if al == 0:
                issue = "в журнале есть, на счёте нет"
            elif jl == 0:
                issue = "на счёте есть, в журнале нет"
            else:
                issue = "размер не сходится"
            disc.append({"strategy": name, "ticker": t,
                         "journal_lots": jl, "account_lots": al, "issue": issue})
        if disc:
            ok = False
        result[name] = {"discrepancies": disc, "active_orders": len(orders),
                        "journal": jpos, "account": apos}
    return {"ok": ok, "strategies": result}


def format_reconcile(rec: dict) -> str:
    lines = ["=== reconcile: журнал lab.db vs sandbox-счета (dry-run, без корректировок) ==="]
    if rec.get("error"):
        lines.append(f"ОШИБКА: {rec['error']}")
        return "\n".join(lines)
    for name, r in rec["strategies"].items():
        lines.append(f"\n[{name}] журнал={r['journal'] or '{}'} счёт={r['account'] or '{}'} "
                     f"активных заявок={r['active_orders']}")
        if not r["discrepancies"]:
            lines.append("  OK — расхождений нет")
        for d in r["discrepancies"]:
            lines.append(f"  РАСХОЖДЕНИЕ {d['ticker']}: журнал {d['journal_lots']} лот(ов), "
                         f"счёт {d['account_lots']} — {d['issue']}")
    lines.append("\nитог: " + ("OK" if rec["ok"] else "ЕСТЬ РАСХОЖДЕНИЯ (исправлять вручную, "
                 "слой ничего не корректирует)"))
    return "\n".join(lines)


def record_reconcile(c, rec: dict, ts: float | None = None) -> None:
    """Сохранить краткую историю reconcile по стратегиям в таблицу reconciles."""
    ts = ts or datetime.now().timestamp()
    if rec.get("error"):
        c.execute("INSERT INTO reconciles(ts,strategy,n_disc,detail) VALUES(?,?,?,?)",
                  (ts, "", -1, rec["error"]))
        c.commit()
        return
    for strategy, row in rec.get("strategies", {}).items():
        disc = row.get("discrepancies", [])
        c.execute("INSERT INTO reconciles(ts,strategy,n_disc,detail) VALUES(?,?,?,?)",
                  (ts, strategy, len(disc), json.dumps(disc, ensure_ascii=False)[:1000]))
    c.commit()


# ── метрики live-vs-backtest ───────────────────────────────────────────────────

def slippage(c, strategy: str) -> dict:
    """Фактический слиппедж: цена исполнения vs last на момент решения.

    trades.price — decision price, fill_price — средняя цена исполнения. Возвращаем
    adverse slippage: для buy это fill-price − decision-price, для sell наоборот.
    Старые записи без fill_price честно дают unavailable."""
    n = c.execute("SELECT COUNT(*) FROM trades WHERE strategy=?", (strategy,)).fetchone()[0]
    rows = c.execute("SELECT side,price,fill_price,lots FROM trades "
                     "WHERE strategy=? AND fill_price IS NOT NULL AND price IS NOT NULL",
                     (strategy,)).fetchall()
    if not rows:
        return {"available": False, "trades": n,
                "reason": "в журнале нет цены исполнения (нужно поле trades.fill_price)"}
    weighted_price, weighted_bps, weight = 0.0, 0.0, 0
    for side, price, fill_price, lots in rows:
        action = (side or "").split(":", 1)[0]
        if action == "buy":
            adverse = fill_price - price
        elif action == "sell":
            adverse = price - fill_price
        else:
            adverse = abs(fill_price - price)
        w = max(int(lots or 0), 1)
        weighted_price += adverse * w
        weighted_bps += (adverse / price * 10_000 if price else 0.0) * w
        weight += w
    return {"available": True, "trades": len(rows), "total_trades": n,
            "avg_adverse_price": weighted_price / weight,
            "avg_adverse_bps": weighted_bps / weight}


def trade_stats(c, strategy: str) -> dict:
    """Фактическая частота сделок по журналу."""
    rows = c.execute("SELECT ts FROM trades WHERE strategy=? ORDER BY ts", (strategy,)).fetchall()
    if not rows:
        return {"trades": 0, "per_day": 0.0, "last_trade_age_ticks": None}
    t0, t1 = rows[0][0], rows[-1][0]
    span_days = max((t1 - t0) / 86400, 1 / 24)  # минимум час, чтобы не делить на 0
    # возраст последней сделки в тиках = equity-записей после неё (тик фермы пишет equity)
    age = c.execute("SELECT COUNT(*) FROM equity WHERE strategy=? AND ts>?",
                    (strategy, t1)).fetchone()[0]
    return {"trades": len(rows), "per_day": len(rows) / span_days, "last_trade_age_ticks": age}


def order_stats(c, strategy: str) -> dict:
    """Fill-rate по последнему lifecycle-статусу каждого order_id."""
    try:
        rows = c.execute("SELECT order_id,status FROM orders WHERE strategy=? ORDER BY ts",
                         (strategy,)).fetchall()
    except sqlite3.OperationalError:
        return {"orders": 0}
    latest = {}
    for order_id, status in rows:
        if order_id:
            latest[order_id] = status or ""
    if not latest:
        return {"orders": 0}
    counts = {}
    for status in latest.values():
        counts[status] = counts.get(status, 0) + 1
    filled = counts.get("filled", 0)
    partial = sum(n for status, n in counts.items() if "partial" in status.lower())
    return {"orders": len(latest), "filled": filled,
            "fill_rate": filled / len(latest), "partial": partial,
            "partial_rate": partial / len(latest), "by_status": counts}


def equity_stats(c, strategy: str) -> dict:
    """Статистика по equity-кривой: доходность, maxDD, дневная волатильность."""
    rows = c.execute("SELECT ts,total FROM equity WHERE strategy=? ORDER BY ts", (strategy,)).fetchall()
    if len(rows) < 2:
        return {"points": len(rows)}
    eq = [r[1] for r in rows]
    peak, maxdd, cur_dd = eq[0], 0.0, 0.0
    for v in eq:
        peak = max(peak, v)
        cur_dd = (v / peak - 1) * 100
        maxdd = min(maxdd, cur_dd)
    # дневные доходности: последний equity каждого дня
    daily = {}
    for ts, total in rows:
        daily[datetime.fromtimestamp(ts).date()] = total
    closes = [daily[d] for d in sorted(daily)]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    vol = statistics.stdev(rets) * 100 if len(rets) >= 2 else None
    return {"points": len(rows), "start": eq[0], "last": eq[-1],
            "return_pct": (eq[-1] / eq[0] - 1) * 100, "maxdd_pct": maxdd,
            "current_dd_pct": cur_dd, "daily_vol_pct": vol, "days": len(closes)}


def tracking_vs_backtest(c, strategy: str, expected_daily_ret: float | None = None) -> dict:
    """Tracking live-кривой против ожидания из бэктеста.

    Если expected_daily_ret передан вручную (доля в день, напр. 0.0004), используем
    его для всех дней. Иначе читаем таблицу expectations(strategy,date,exp_ret), где
    date — ISO-дата дневного close, exp_ret — ожидаемая дневная доходность."""
    rows = c.execute("SELECT ts,total FROM equity WHERE strategy=? ORDER BY ts",
                     (strategy,)).fetchall()
    st = equity_stats(c, strategy)
    if expected_daily_ret is None:
        if len(rows) < 2:
            return {"available": False, "reason": "мало данных equity"}
        daily = {}
        for ts, total in rows:
            daily[datetime.fromtimestamp(ts).date()] = total
        dates = sorted(daily)
        try:
            exp = {r[0]: float(r[1]) for r in c.execute(
                "SELECT date,exp_ret FROM expectations WHERE strategy=?", (strategy,))}
        except sqlite3.OperationalError:
            exp = {}
        missing = [d.isoformat() for d in dates[1:] if d.isoformat() not in exp]
        if missing:
            return {"available": False,
                    "reason": "нет ожиданий для дат: " + ", ".join(missing[:5])}
        exp_factor = 1.0
        for d in dates[1:]:
            exp_factor *= 1 + exp[d.isoformat()]
        exp_total = (exp_factor - 1) * 100
        return {"available": True, "live_pct": st["return_pct"], "expected_pct": exp_total,
                "gap_pp": st["return_pct"] - exp_total}
    if st.get("days", 0) < 2:
        return {"available": False, "reason": "мало данных equity"}
    exp_total = ((1 + expected_daily_ret) ** (st["days"] - 1) - 1) * 100
    return {"available": True, "live_pct": st["return_pct"], "expected_pct": exp_total,
            "gap_pp": st["return_pct"] - exp_total}


# ── алерты ─────────────────────────────────────────────────────────────────────

def check_alerts(c, strategy: str, dd_limit=DD_LIMIT_PCT,
                 silence_ticks=SILENCE_TICKS, err_streak=ERR_STREAK) -> list:
    """Список строк-алертов: просадка > лимита, тишина > N тиков, серия ошибок в хвосте."""
    alerts = []
    st = equity_stats(c, strategy)
    if st.get("current_dd_pct", 0) < -dd_limit:
        alerts.append(f"ПРОСАДКА {st['current_dd_pct']:+.2f}% (лимит {dd_limit}%)")
    ts_ = trade_stats(c, strategy)
    if ts_["trades"] == 0:
        ticks = st.get("points", 0)
        if ticks > silence_ticks:
            alerts.append(f"ТИШИНА: 0 сделок за {ticks} тиков (порог {silence_ticks})")
    elif ts_["last_trade_age_ticks"] is not None and ts_["last_trade_age_ticks"] > silence_ticks:
        alerts.append(f"ТИШИНА: последняя сделка {ts_['last_trade_age_ticks']} тиков назад "
                      f"(порог {silence_ticks})")
    # серия ошибок: сколько подряд error/fail в хвосте событий
    streak = 0
    for (kind,) in c.execute(
            "SELECT kind FROM events WHERE strategy=? ORDER BY ts DESC LIMIT 50", (strategy,)):
        if "error" in kind or "fail" in kind:
            streak += 1
        else:
            break
    if streak >= err_streak:
        alerts.append(f"СЕРИЯ ОШИБОК: {streak} error/fail событий подряд (порог {err_streak})")
    return alerts


# ── дневной отчёт ──────────────────────────────────────────────────────────────

def daily_report(db=None, day: str | None = None, reconcile_result: dict | None = None) -> str:
    """Текст дневного отчёта: P&L за день, сделки, ошибки, алерты, reconcile-статус.

    day — 'YYYY-MM-DD' (по умолчанию сегодня). Офлайн: только lab.db;
    reconcile_result — опционально готовый результат reconcile() (сеть)."""
    c = _conn(db)
    d = datetime.strptime(day, "%Y-%m-%d") if day else datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0)
    t0, t1 = d.timestamp(), (d + timedelta(days=1)).timestamp()
    names = _strategies(c)
    lines = [f"=== forward-test: дневной отчёт {d:%Y-%m-%d} ==="]
    if not names:
        lines.append("журнал пуст")
        return "\n".join(lines)
    for n in names:
        rows = c.execute("SELECT total FROM equity WHERE strategy=? AND ts>=? AND ts<? ORDER BY ts",
                         (n, t0, t1)).fetchall()
        trades_day = c.execute("SELECT COUNT(*) FROM trades WHERE strategy=? AND ts>=? AND ts<?",
                               (n, t0, t1)).fetchone()[0]
        errs_day = c.execute(f"SELECT COUNT(*) FROM events WHERE strategy=? AND ts>=? AND ts<? "
                             f"AND {_ERR_SQL}", (n, t0, t1)).fetchone()[0]
        lines.append(f"\n[{n}]")
        if rows:
            first, last = rows[0][0], rows[-1][0]
            pnl = last - first
            lines.append(f"  P&L дня: {pnl:+.0f} руб ({pnl / first * 100:+.2f}%), "
                         f"equity {first:.0f} → {last:.0f} ({len(rows)} тиков)")
        else:
            lines.append("  equity за день нет (ферма не тикала)")
        lines.append(f"  сделок за день: {trades_day}, ошибок за день: {errs_day}")
        st = equity_stats(c, n)
        if st.get("points", 0) >= 2:
            lines.append(f"  с начала: {st['return_pct']:+.2f}%, maxDD {st['maxdd_pct']:.2f}%, "
                         f"текущая просадка {st['current_dd_pct']:.2f}%")
        ts_ = trade_stats(c, n)
        lines.append(f"  частота: {ts_['trades']} сделок всего, {ts_['per_day']:.2f}/день")
        os_ = order_stats(c, n)
        if os_.get("orders", 0):
            status_bits = ", ".join(f"{k}={v}" for k, v in sorted(os_["by_status"].items()))
            lines.append(f"  fill-rate: {os_['filled']}/{os_['orders']} "
                         f"({os_['fill_rate']*100:.1f}%; {status_bits})")
            if os_.get("partial"):
                lines.append(f"  partial-rate: {os_['partial']}/{os_['orders']} "
                             f"({os_['partial_rate']*100:.1f}%)")
        tr = tracking_vs_backtest(c, n)
        if tr.get("available"):
            lines.append(f"  tracking: live {tr['live_pct']:+.2f}% vs expected "
                         f"{tr['expected_pct']:+.2f}%, gap {tr['gap_pp']:+.2f} п.п.")
        sl = slippage(c, n)
        if not sl["available"]:
            lines.append(f"  слиппедж: н/д — {sl['reason']}")
        else:
            lines.append(f"  слиппедж: {sl['avg_adverse_price']:+.4f} цены "
                         f"({sl['avg_adverse_bps']:+.2f} б.п., "
                         f"{sl['trades']}/{sl['total_trades']} сделок с fill_price)")
        for a in check_alerts(c, n):
            lines.append(f"  !! АЛЕРТ: {a}")
    roll = futures_roll_warnings()
    if roll:
        lines.append("\nrollover:")
        for r in roll:
            lines.append(f"  {r['status']} {r['ticker']}: last_trade {r['last_trade']}, "
                         f"exp {r['exp']}, roll_to {r['roll_to']}, "
                         f"days_to_last_trade={r['days_to_last_trade']}")
    # reconcile-статус
    if reconcile_result is None:
        lines.append("\nreconcile: не выполнялся (офлайн-режим; запустить: python -m lab.forward reconcile)")
    elif reconcile_result.get("error"):
        lines.append(f"\nreconcile: ОШИБКА — {reconcile_result['error']}")
    else:
        n_disc = sum(len(r["discrepancies"]) for r in reconcile_result["strategies"].values())
        lines.append(f"\nreconcile: {'OK' if reconcile_result['ok'] else f'{n_disc} расхождений(я)!'}")
    hist = c.execute("SELECT ts,strategy,n_disc FROM reconciles ORDER BY ts DESC LIMIT 5").fetchall()
    if hist:
        lines.append("\nистория reconcile:")
        for ts, s, n_disc in hist:
            if n_disc < 0:
                lines.append(f"  {datetime.fromtimestamp(ts):%Y-%m-%d %H:%M} ошибка: {s or 'global'}")
            else:
                lines.append(f"  {datetime.fromtimestamp(ts):%Y-%m-%d %H:%M} [{s}] {n_disc} расхождений")
    # хвост событий за день
    ev = c.execute("SELECT ts,strategy,kind,detail FROM events WHERE ts>=? AND ts<? "
                   "ORDER BY ts DESC LIMIT 8", (t0, t1)).fetchall()
    if ev:
        lines.append("\nпоследние события дня:")
        for ts, s, k, det in ev:
            lines.append(f"  {datetime.fromtimestamp(ts):%H:%M} [{s}] {k} {(det or '')[:60]}")
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(prog="lab.forward",
                                description="forward-test слой: отчёт и сверка (read-only)")
    p.add_argument("cmd", choices=["report", "reconcile"])
    p.add_argument("--db", default=None, help=f"путь к журналу (default: {DB})")
    p.add_argument("--date", default=None, help="день отчёта YYYY-MM-DD (default: сегодня)")
    args = p.parse_args(argv)

    if args.cmd == "report":
        print(daily_report(db=args.db, day=args.date))
        return 0

    # reconcile: нужен TINVEST_SANDBOX_KEY и сеть — гвардим сообщением, не трейсбеком
    try:
        rec = reconcile(db=args.db)
    except SystemExit as e:       # load_token: нет ключа в .env
        print(f"reconcile недоступен: {e}")
        return 1
    except RuntimeError as e:     # lab.api.call: сеть/HTTP после ретраев
        print(f"reconcile недоступен (сеть/API): {e}")
        return 1
    record_reconcile(_conn(args.db), rec)
    print(format_reconcile(rec))
    return 0 if rec["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
