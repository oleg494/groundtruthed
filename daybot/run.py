"""Daybot: интрадей ложные пробои утреннего диапазона на фьючерсах (ПЕСОЧНИЦА, лонг и шорт).

10:00-10:30 МСК строится диапазон по минуткам; ширина фильтруется по дневному ATR
(ATR_MULT_MIN/MAX, иначе пропуск дня). Вход по рынку на пробое контртрендом
(REVERSAL=True: вверх - ШОРТ, вниз - ЛОНГ), стоп за пробойной свечой (риск 0.15-0.5%),
тейк 2R. Ночных позиций нет: в 23:40 всё закрывается и процесс выходит.
Остановка: файл daybot.stop (если существует при старте - бот не запускается).

    python -m daybot.run        (батники: start.bat / stop.bat / setup_task.bat)
"""
import ctypes
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lab.api import call, to_f
from lab.instruments import INSTRUMENTS, rub_value
from lab.journal import Journal
from lab.strategy import Ctx

ROOT = Path(__file__).resolve().parent
STATE, LOCK, STOPF = ROOT / "daybot_state.json", ROOT / "daybot.pid", ROOT / "daybot.stop"
DB, LOGF = ROOT / "daybot.db", ROOT / "daybot.log"

TICKERS = ["BMQ6", "NGN6", "GLDRUBF"]   # Brent мини, газ, золото
VIRTUAL_CASH = 100_000
MSK = timezone(timedelta(hours=3))
TICK = 10                # сек между проверками
RANGE_MIN = 10           # минимум минуток для валидного диапазона
MAX_ENTRIES = 3          # входов в день на инструмент
ALLOC = 0.30             # доля equity на один вход
RISK_CAP = 0.005         # стоп не дальше 0.5% от входа
MIN_RISK = 0.0015        # ...и не ближе 0.15%: иначе тейк 2R не покрывает комиссию
TAKE_R = 2.0             # тейк = вход + 2*риска
STALE_LIMIT = 300        # сек без цен при открытой позиции -> аварийное закрытие
REVERSAL = True              # контртрендовый режим (ложные пробои)
ATR_N = 100                  # период для расчета ATR
ATR_MULT_MIN = 0.6           # минимальная ширина диапазона в единицах ATR
ATR_MULT_MAX = 2.0           # максимальная ширина диапазона в единицах ATR



def now_msk() -> datetime:
    return datetime.now(MSK)


def log(msg: str) -> None:
    line = now_msk().strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line, flush=True)
    with LOGF.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def pid_alive(pid: int) -> bool:
    if os.name == "nt":  # os.kill(pid, 0) на Windows всегда падает WinError 87
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, 0, pid)
        if h:
            k.CloseHandle(h)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock() -> None:
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().strip())
            if pid_alive(pid):
                raise SystemExit("daybot уже запущен (PID %d)" % pid)
        except ValueError:
            pass
    LOCK.write_text(str(os.getpid()))


def load_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(st: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def ensure_account(st: dict, j: Journal) -> str:
    if st.get("account"):
        return st["account"]
    acc = call("SandboxService/OpenSandboxAccount", {"name": "daybot"})["accountId"]
    call("SandboxService/SandboxPayIn", {
        "accountId": acc, "amount": {"units": str(VIRTUAL_CASH), "nano": 0, "currency": "rub"}})
    st["account"] = acc
    j.event("daybot", "account_open", acc)
    return acc


def last_prices() -> dict:
    uids = {INSTRUMENTS[t]["uid"]: t for t in TICKERS}
    r = call("MarketDataService/GetLastPrices", {"instrumentId": list(uids)})
    return {uids[x["instrumentUid"]]: to_f(x["price"])
            for x in r.get("lastPrices", []) if x.get("instrumentUid") in uids}


def minute_candles(ticker: str, frm: str, to: str) -> list:
    r = call("MarketDataService/GetCandles", {
        "instrumentId": INSTRUMENTS[ticker]["uid"],
        "interval": "CANDLE_INTERVAL_1_MIN", "from": frm, "to": to})
    return [c for c in r.get("candles", []) if c.get("isComplete", True)]


def build_range(ticker: str, day: str) -> dict | None:
    """Opening range 10:00-10:30 МСК (07:00-07:30 UTC, МСК фиксированно UTC+3)."""
    cs = minute_candles(ticker, day + "T07:00:00Z", day + "T07:30:00Z")
    if len(cs) < RANGE_MIN:
        return None
    return {"high": max(to_f(c["high"]) for c in cs),
            "low": min(to_f(c["low"]) for c in cs)}


def breakout_candle(ticker: str) -> dict:
    now = datetime.now(timezone.utc)
    cs = minute_candles(ticker, (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if not cs:
        return {"low": 0.0, "high": 0.0}
    return {"low": to_f(cs[-1]["low"]), "high": to_f(cs[-1]["high"])}


def get_atr(ticker: str, n: int = 100) -> float | None:
    """Расчет классического дневного ATR(N) для фильтрации волатильности."""
    now = datetime.now(timezone.utc)
    frm = (now - timedelta(days=int(n * 2))).strftime("%Y-%m-%dT%H:%M:%SZ")
    to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        r = call("MarketDataService/GetCandles", {
            "instrumentId": INSTRUMENTS[ticker]["uid"],
            "interval": "CANDLE_INTERVAL_DAY", "from": frm, "to": to
        })
        candles = [c for c in r.get("candles", []) if c.get("isComplete", True)]
        if len(candles) < n:
            return None
        trs = []
        for i in range(len(candles)):
            h = to_f(candles[i]["high"])
            l = to_f(candles[i]["low"])
            if i == 0:
                tr = h - l
            else:
                prev_c = to_f(candles[i-1]["close"])
                tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
        return sum(trs[-n:]) / n
    except Exception as e:
        log("ошибка при получении ATR для %s: %s" % (ticker, e))
        return None



def market_exit(ctx: Ctx, t: str, side: str, lots: int) -> None:
    """Закрытие позиции с обходом бага песочницы: выкуп шорта может упасть с
    30034 "Not enough balance" (двойная блокировка ГО) даже при достатке средств.
    Тогда доливаем кэш и повторяем; доливку учитываем в equity (extra_cash)."""
    try:
        ctx.market(t, side, lots)
    except RuntimeError as e:
        if "30034" not in str(e):
            raise
        call("SandboxService/SandboxPayIn", {
            "accountId": ctx.account_id,
            "amount": {"units": str(VIRTUAL_CASH), "nano": 0, "currency": "rub"}})
        ctx.state["extra_cash"] = ctx.state.get("extra_cash", 0) + VIRTUAL_CASH
        ctx.j.event("daybot", "payin_bugfix", "%s: +%d на обход 30034" % (t, VIRTUAL_CASH))
        log("%s: баг 30034 при закрытии - долил %d, повторяю" % (t, VIRTUAL_CASH))
        ctx.market(t, side, lots)


def honest_equity(ctx: Ctx) -> dict:
    eq = ctx.equity()
    eq["total"] -= ctx.state.get("extra_cash", 0)
    return eq


def close_all(ctx: Ctx, reason: str) -> None:
    pos = ctx.positions()
    for t, lots in pos.items():
        if lots == 0:
            continue
        try:
            market_exit(ctx, t, "sell" if lots > 0 else "buy", abs(lots))
            log("закрыл %s x%+d (%s)" % (t, lots, reason))
        except RuntimeError as e:
            ctx.j.event("daybot", "close_fail", "%s: %s" % (t, e))
    ctx.state.get("open", {}).clear()


def main():
    acquire_lock()
    if STOPF.exists():
        # стоп-флаг снимает только оператор: в файле может лежать вердикт (почему убит)
        print("daybot.stop существует, не стартую:\n" + STOPF.read_text(encoding="utf-8"))
        LOCK.unlink(missing_ok=True)
        return
    j = Journal(str(DB))
    st = load_state()
    acc = ensure_account(st, j)
    ctx = Ctx(acc, j, "daybot")
    ctx.state = st
    today = now_msk().strftime("%Y-%m-%d")

    if st.get("day") != today:
        # новый день: осиротевшие позиции закрываем - их диапазон утрачен
        if any(ctx.positions().values()):
            log("осиротевшие позиции с прошлого дня - закрываю")
            ctx.prices = last_prices()
            close_all(ctx, "orphan")
        st.update(day=today, ranges={}, entries={}, open={})
    save_state(st)

    if now_msk().weekday() >= 5:
        j.event("daybot", "weekend", today)
        log("выходной - выходим")
        LOCK.unlink(missing_ok=True)
        return

    log("daybot старт: %s, счёт %s" % (TICKERS, acc))
    stale_since = None
    last_eq_log = 0.0

    while True:
        if STOPF.exists():
            close_all(ctx, "stop.bat")
            log("остановлен по stop-флагу")
            break
        now = now_msk()
        if (now.hour == 23 and now.minute >= 40) or now.hour < 7:
            close_all(ctx, "конец дня")
            eq = honest_equity(ctx)
            j.event("daybot", "day_close", json.dumps({"total": eq["total"]}))
            log("день закрыт, equity %.0f" % eq["total"])
            break
        if now.hour < 10:
            time.sleep(30)
            continue

        try:
            ctx.prices = last_prices()
            stale_since = None
        except RuntimeError as e:
            j.event("daybot", "price_fail", str(e))
            stale_since = stale_since or time.time()
            if time.time() - stale_since > STALE_LIMIT and any(
                    ctx.positions().values()):
                log("нет цен >5 мин при открытой позиции - аварийное закрытие")
                close_all(ctx, "stale")
                break
            time.sleep(TICK)
            continue

        in_session = now.hour > 10 or now.minute >= 30
        for t in TICKERS:
            px = ctx.prices.get(t, 0.0)
            if not px:
                continue
            rng = st["ranges"].get(t)
            if rng is None and in_session:
                rng = build_range(t, today)
                if rng:
                    atr_val = get_atr(t, ATR_N)
                    if atr_val is not None:
                        w = rng["high"] - rng["low"]
                        if ATR_MULT_MIN > 0 and w < ATR_MULT_MIN * atr_val:
                            log("%s: диапазон слишком узкий (%.4f < %.4f ATR), пропуск дня" % (t, w, ATR_MULT_MIN * atr_val))
                            rng = "skip"
                        elif ATR_MULT_MAX > 0 and w > ATR_MULT_MAX * atr_val:
                            log("%s: диапазон слишком широкий (%.4f > %.4f ATR), пропуск дня" % (t, w, ATR_MULT_MAX * atr_val))
                            rng = "skip"
                st["ranges"][t] = rng if rng else "skip"
                log("%s: диапазон %s" % (t, rng if rng else "мало минуток, пропуск дня"))
            if not isinstance(rng, dict):
                continue

            opened = st["open"].get(t)
            if opened:  # ведём стоп/тейк
                short = opened.get("side") == "short"
                hit_stop = px >= opened["stop"] if short else px <= opened["stop"]
                hit_take = px <= opened["take"] if short else px >= opened["take"]
                if hit_stop or hit_take:
                    why = "стоп" if hit_stop else "тейк"
                    try:
                        market_exit(ctx, t, "buy" if short else "sell", opened["lots"])
                        log("%s: выход по %s @%s (%s от %s)"
                            % (t, why, px, opened.get("side", "long"), opened["px"]))
                    except RuntimeError as e:
                        j.event("daybot", "exit_fail", "%s: %s" % (t, e))
                        continue
                    del st["open"][t]
            elif in_session and st["entries"].get(t, 0) < MAX_ENTRIES and (
                    px > rng["high"] or px < rng["low"]):
                if px > rng["high"]:
                    side = "short" if REVERSAL else "long"
                else:
                    side = "long" if REVERSAL else "short"
                eq = honest_equity(ctx)
                lots = int(eq["total"] * ALLOC / rub_value(t, px, 1))
                if lots < 1:
                    continue
                bc = breakout_candle(t)
                if side == "long":
                    stop = max(bc["low"], px * (1 - RISK_CAP))
                    stop = min(stop, px * (1 - MIN_RISK))  # риск >= 0.15%, иначе комиссия съедает
                else:
                    stop = min(bc["high"] or px * 2, px * (1 + RISK_CAP))
                    stop = max(stop, px * (1 + MIN_RISK))
                try:
                    ctx.market(t, "buy" if side == "long" else "sell", lots)
                except RuntimeError as e:
                    j.event("daybot", "entry_fail", "%s: %s" % (t, e))
                    continue
                take = px + TAKE_R * (px - stop)  # для шорта px-stop < 0 - тейк ниже входа
                st["open"][t] = {"px": px, "lots": lots, "side": side,
                                 "stop": round(stop, 6), "take": round(take, 6)}
                st["entries"][t] = st["entries"].get(t, 0) + 1
                log("%s: %s #%d x%d @%s, стоп %.4f, тейк %.4f"
                    % (t, side, st["entries"][t], lots, px, stop, take))

        if time.time() - last_eq_log > 60:
            eq = honest_equity(ctx)
            j.equity("daybot", eq["total"], eq["cash"], eq["pos_lots"])
            last_eq_log = time.time()
        save_state(st)
        time.sleep(TICK)

    save_state(st)
    LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
