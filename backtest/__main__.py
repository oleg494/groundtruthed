"""CLI пакета backtest.

    python -m backtest demo
    python -m backtest run --strategy sma_cross --params fast=20,slow=60 --synthetic gbm:750:1
    python -m backtest run --strategy donchian --uid <UID> --ticker SBER --days 400 --html out.html
    python -m backtest optimize --strategy sma_cross --grid "fast=10,20,30;slow=50,80,120"
    python -m backtest walkforward --strategy donchian --grid "n=10,20,40;exit_n=5,10" --splits 4
    python -m backtest montecarlo --strategy sma_cross --synthetic gbm:1000:3 --source trades

Источник данных: --synthetic gbm:<bars>:<seed> (без сети) ЛИБО --uid <UID> --ticker <T>
--days <N> (read-only фетч через sandbox-домен, кэш на диск).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import benchmark as bench_mod
from . import candles, strategies
from . import dashboard as dashboard_mod
from . import ensemble as ensemble_mod
from . import export as export_mod
from . import risk as risk_mod
from . import robust as robust_mod
from . import scenarios as scenarios_mod
from .engine import run
from .metrics import metrics
from .montecarlo import bootstrap_returns, bootstrap_trades
from .optimize import cost_sensitivity, grid_search, walk_forward
from .report import compare_report, heatmap_html, html_report, study_html, tearsheet_html, text_report, walkforward_html
from .study import run_study, text_study

# несколько UID ликвидных бумаг MOEX для удобства (значения скопированы, не импорт из lab/)
PRESETS = {
    "SBER": "e6123145-9665-43e0-8413-cd61b8aa9b13",
    "GAZP": "962e2a95-02a9-4171-abd7-aa198dbe643a",
    "LKOH": "02cfdf61-6298-4c0f-a9ca-9cabc82afaf3",
    "GLDRUBF": "b347fe28-0d2a-45bf-b3bd-cda8a6ac64e6",
}


def _coerce(v: str):
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return v


def _parse_params(s: str | None) -> dict:
    if not s:
        return {}
    out = {}
    for kv in s.split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k.strip()] = _coerce(v.strip())
    return out


def _parse_grid(s: str) -> dict[str, list]:
    grid: dict[str, list] = {}
    for part in s.split(";"):
        if "=" not in part:
            continue
        k, vals = part.split("=", 1)
        grid[k.strip()] = [_coerce(v.strip()) for v in vals.split(",") if v.strip()]
    return grid


def _load_data(args) -> dict:
    if getattr(args, "synthetic", None):
        return candles.parse_synthetic(args.synthetic)
    if getattr(args, "uid", None) or getattr(args, "ticker", None):
        ticker = args.ticker or "SEC"
        uid = args.uid or PRESETS.get(ticker.upper())
        if not uid:
            sys.exit(f"нет UID для {ticker!r}; задай --uid или используй пресет {list(PRESETS)}")
        interval = getattr(args, "interval", "CANDLE_INTERVAL_DAY")
        print(f"фетч {ticker} ({uid[:8]}…), {args.days} дн. с интервалом {interval} …", flush=True)
        return candles.from_tinvest(uid, ticker, days=args.days, interval=interval)
    # дефолт — синтетика, чтобы любая команда работала без сети
    return candles.gbm("SYN", bars=750, seed=1)


def _add_data_args(p):
    p.add_argument("--synthetic", help="gbm:<bars>:<seed> | trend:.. | mean_revert:.. | sine:..")
    p.add_argument("--uid", help="instrument UID для реального фетча")
    p.add_argument("--ticker", help="тикер (или пресет SBER/GAZP/LKOH/GLDRUBF)")
    p.add_argument("--days", type=int, default=400)
    p.add_argument("--interval", default="CANDLE_INTERVAL_DAY",
                   help="интервал свечей (напр. CANDLE_INTERVAL_DAY, CANDLE_INTERVAL_30_MIN, CANDLE_INTERVAL_HOUR)")
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--commission", type=float, default=0.0005)
    p.add_argument("--slippage", type=float, default=0.0)
    p.add_argument("--futures", action="store_true",
                   help="трактовать инструмент как фьючерс (маржинальная модель, цена в пунктах)")
    p.add_argument("--multiplier", type=float, default=1.0,
                   help="руб. за 1.0 пункта на лот (для --futures, напр. GLDRUBF=1.0)")


def _instruments(args, data):
    """Построить реестр инструментов из флагов CLI (None → дефолт cash-модель)."""
    if not getattr(args, "futures", False):
        return None
    from .core import Instrument
    mult = getattr(args, "multiplier", 1.0)
    return {t: Instrument(t, multiplier=mult, kind="futures") for t in data}


def cmd_demo(args) -> None:
    data = candles.gbm("SYN", bars=750, seed=args.seed)
    strat_list = [
        strategies.BuyHold(), strategies.RandomTrader(seed=args.seed),
        strategies.SMACross(20, 60), strategies.Donchian(20, 10),
        strategies.RSIReversion(), strategies.Bollinger(),
    ]
    results = [run(s, data, cash=args.cash, commission=0.0005, slippage=0.0005)
               for s in strat_list]
    print(f"\n=== DEMO: 6 стратегий на синтетике gbm:750:{args.seed} ===\n")
    print(compare_report(results))
    best = max(results, key=lambda r: metrics(r).sharpe)
    print("\nЛучшая по Sharpe — подробно:\n")
    print(text_report(best))
    if best.strategy == "random":
        print("\n  ⚠ на этом сиде выиграл RANDOM — наглядно, почему одному прогону\n"
              "    верить нельзя. Прогони `montecarlo` и `walkforward`, прежде чем верить сигналу.")
    if args.html:
        Path(args.html).write_text(html_report(best), encoding="utf-8")
        print(f"\nHTML-отчёт: {args.html}")


def cmd_run(args) -> None:
    data = _load_data(args)
    strat = strategies.build(args.strategy, **_parse_params(args.params))
    res = run(strat, data, cash=args.cash, commission=args.commission,
              slippage=args.slippage, instruments=_instruments(args, data))
    print(text_report(res))
    if args.html:
        Path(args.html).write_text(html_report(res), encoding="utf-8")
        print(f"\nHTML-отчёт: {args.html}")


def cmd_optimize(args) -> None:
    data = _load_data(args)
    cls = strategies.REGISTRY[args.strategy]
    grid = _parse_grid(args.grid)
    pts = grid_search(cls, data, grid, metric=args.metric, cash=args.cash,
                      commission=args.commission, slippage=args.slippage,
                      instruments=_instruments(args, data))
    print(f"\n=== optimize {args.strategy} по {args.metric}, "
          f"{len(pts)} комбинаций ===\n")
    print(f"{'#':>2}  {args.metric:>8}  {'ret%':>8}  {'maxDD%':>8}  параметры")
    for i, p in enumerate(pts[:15]):
        print(f"{i+1:>2}  {getattr(p.metrics, args.metric):>8.2f}  "
              f"{p.metrics.total_return*100:>+8.2f}  {p.metrics.max_drawdown*100:>8.2f}  "
              f"{p.params}")


def cmd_walkforward(args) -> None:
    data = _load_data(args)
    cls = strategies.REGISTRY[args.strategy]
    grid = _parse_grid(args.grid)
    wf = walk_forward(cls, data, grid, n_splits=args.splits, metric=args.metric,
                      cash=args.cash, commission=args.commission, slippage=args.slippage,
                      instruments=_instruments(args, data))
    print(f"\n=== walk-forward {args.strategy}, {args.splits} окон, оптим. по {args.metric} ===\n")
    print(f"{'окно':>4}  {'IS '+args.metric:>10}  {'OOS ret%':>9}  {'OOS Sharpe':>10}  лучшие параметры")
    for i, w in enumerate(wf.windows):
        print(f"{i+1:>4}  {w.is_metric:>10.2f}  {w.oos.total_return*100:>+9.2f}  "
              f"{w.oos.sharpe:>10.2f}  {w.best_params}")
    print(f"\nСквозная OOS-доходность (компаундинг): {wf.oos_return()*100:+.2f}%")
    if getattr(args, "html", None):
        Path(args.html).write_text(walkforward_html(wf), encoding="utf-8")
        print(f"HTML walk-forward: {args.html}")


def cmd_montecarlo(args) -> None:
    data = _load_data(args)
    strat = strategies.build(args.strategy, **_parse_params(args.params))
    res = run(strat, data, cash=args.cash, commission=args.commission,
              slippage=args.slippage)
    print(text_report(res))
    print()
    fn = bootstrap_returns if args.source == "returns" else bootstrap_trades
    mc = fn(res, n=args.n, seed=args.seed)
    print(mc.summary())


def cmd_robust(args) -> None:
    data = _load_data(args)
    cls = strategies.REGISTRY[args.strategy]
    if args.grid:
        pts = grid_search(cls, data, _parse_grid(args.grid), metric=args.metric,
                          cash=args.cash, commission=args.commission, slippage=args.slippage)
        if not pts:
            sys.exit("сетка не дала валидных комбинаций")
        best = pts[0]
        print(text_report(best.result))
        print()
        print(robust_mod.assess(best.result, pts, metric=args.metric).summary())
    else:
        strat = strategies.build(args.strategy, **_parse_params(args.params))
        res = run(strat, data, cash=args.cash, commission=args.commission,
                  slippage=args.slippage)
        print(text_report(res))
        print()
        print(robust_mod.assess(res).summary())


def cmd_bench(args) -> None:
    data = _load_data(args)
    strat = strategies.build(args.strategy, **_parse_params(args.params))
    res = run(strat, data, cash=args.cash, commission=args.commission,
              slippage=args.slippage)
    bh = run(strategies.BuyHold(), data, cash=args.cash,
             commission=args.commission, slippage=args.slippage)
    print(text_report(res))
    print()
    print(bench_mod.compare(res, bh).summary())


def cmd_tearsheet(args) -> None:
    data = _load_data(args)
    names = [n.strip() for n in args.strategies.split(",") if n.strip()]
    results = []
    for n in names:
        if n not in strategies.REGISTRY:
            sys.exit(f"неизвестная стратегия {n!r}; есть: {list(strategies.REGISTRY)}")
        results.append(run(strategies.build(n), data, cash=args.cash,
                           commission=args.commission, slippage=args.slippage))
    out = args.html or "tearsheet.html"
    Path(out).write_text(tearsheet_html(results, args.title), encoding="utf-8")
    print(f"tearsheet ({len(results)} стратегий): {out}")


def cmd_export(args) -> None:
    data = _load_data(args)
    strat = strategies.build(args.strategy, **_parse_params(args.params))
    res = run(strat, data, cash=args.cash, commission=args.commission,
              slippage=args.slippage)
    pre = args.prefix or f"export_{args.strategy}"
    export_mod.equity_csv(res, f"{pre}_equity.csv")
    export_mod.trades_csv(res, f"{pre}_trades.csv")
    export_mod.to_json(res, f"{pre}.json")
    print(text_report(res))
    print(f"\nэкспорт: {pre}_equity.csv, {pre}_trades.csv, {pre}.json")


def cmd_heatmap(args) -> None:
    data = _load_data(args)
    cls = strategies.REGISTRY[args.strategy]
    pts = grid_search(cls, data, _parse_grid(args.grid), metric=args.metric,
                      cash=args.cash, commission=args.commission, slippage=args.slippage)
    if not pts:
        sys.exit("сетка не дала валидных комбинаций")
    out = args.html or "heatmap.html"
    Path(out).write_text(heatmap_html(pts, args.x, args.y, args.metric), encoding="utf-8")
    print(f"heatmap {args.metric} по {args.x}×{args.y} ({len(pts)} ячеек): {out}")


def cmd_costs(args) -> None:
    data = _load_data(args)
    params = _parse_params(args.params)
    rows = cost_sensitivity(lambda: strategies.build(args.strategy, **params), data,
                            cash=args.cash)
    print(f"\n=== {args.strategy}: чувствительность к издержкам ===\n")
    print(f"{'comm':>7}{'slip':>7}{'ret%':>9}{'sharpe':>8}{'maxDD%':>9}{'trades':>8}")
    for r in rows:
        print(f"{r['commission']*100:>6.2f}%{r['slippage']*100:>6.2f}%"
              f"{r['total_return']*100:>+9.2f}{r['sharpe']:>8.2f}"
              f"{r['max_drawdown']*100:>9.2f}{r['num_trades']:>8}")


def cmd_ensemble(args) -> None:
    data = _load_data(args)
    names = [n.strip() for n in args.strategies.split(",") if n.strip()]
    runs = [run(strategies.build(n), data, cash=args.cash, commission=args.commission,
                slippage=args.slippage) for n in names]
    weights = ensemble_mod.risk_parity_weights(runs) if args.risk_parity else None
    combined = ensemble_mod.combine_equity(runs, weights, total_cash=args.cash,
                                           name="ensemble")
    print(compare_report(runs + [combined]))
    print()
    if len(runs) > 1:
        print(ensemble_mod.correlation_text(runs))
        print()
    print(text_report(combined))


def cmd_scenarios(args) -> None:
    data_params = _parse_params(args.params)
    factory = lambda: strategies.build(args.strategy, **data_params)  # noqa: E731
    if args.regimes:
        stats = scenarios_mod.across_regimes(factory, seeds=range(args.seeds),
                                             metric=args.metric, bars=args.bars,
                                             commission=args.commission, slippage=args.slippage)
        print(scenarios_mod.regimes_report(stats))
    else:
        st = scenarios_mod.across_seeds(factory, generator=args.generator,
                                        seeds=range(args.seeds), metric=args.metric,
                                        bars=args.bars, commission=args.commission,
                                        slippage=args.slippage)
        print(f"Сценарии {args.strategy} на {args.generator}:")
        print(st.line())


def cmd_risk(args) -> None:
    data = _load_data(args)
    strat = strategies.build(args.strategy, **_parse_params(args.params))
    res = run(strat, data, cash=args.cash, commission=args.commission,
              slippage=args.slippage)
    print(text_report(res))
    print()
    print(risk_mod.risk_report(res).summary())
    print()
    print(risk_mod.calendar_text(res))


def cmd_study(args) -> None:
    data = _load_data(args)
    study = run_study(args.strategy, data, _parse_grid(args.grid), metric=args.metric,
                      n_splits=args.splits, mc_n=args.mc_n, cash=args.cash,
                      commission=args.commission, slippage=args.slippage,
                      instruments=_instruments(args, data))
    print(text_study(study))
    if args.html:
        Path(args.html).write_text(study_html(study), encoding="utf-8")
        print(f"\nHTML-исследование: {args.html}")


def cmd_dashboard(args) -> None:
    out = args.html or "dashboard.html"
    n = dashboard_mod.save_dashboard(args.dir, out, title=args.title)
    if n == 0:
        print(f"директория {args.dir!r}: JSON-файлов с метриками не найдено")
    else:
        print(f"дашборд {n} прогонов → {out}")


def cmd_fetch(args) -> None:
    ticker = (args.ticker or "SEC").upper()
    uid = args.uid or PRESETS.get(ticker)
    if not uid:
        sys.exit(f"нет UID для {ticker}; задай --uid или пресет {list(PRESETS)}")
    data = candles.from_tinvest(uid, ticker, days=args.days)
    bars = data[ticker]
    print(f"{ticker}: {len(bars)} баров, "
          f"{bars[0].c:.2f} → {bars[-1].c:.2f}, кэш в backtest/.cache/")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="backtest", description="движок бэктестинга (stdlib-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("demo", help="прогон бенчмарков на синтетике")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--html", help="путь для HTML-отчёта лучшей стратегии")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("run", help="один прогон стратегии")
    p.add_argument("--strategy", required=True, choices=list(strategies.REGISTRY))
    p.add_argument("--params", help="k=v,k=v")
    p.add_argument("--html", help="путь для HTML-отчёта")
    _add_data_args(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("optimize", help="сеточный перебор параметров")
    p.add_argument("--strategy", required=True, choices=list(strategies.REGISTRY))
    p.add_argument("--grid", required=True, help='"fast=10,20,30;slow=50,80,120"')
    p.add_argument("--metric", default="sharpe")
    _add_data_args(p)
    p.set_defaults(func=cmd_optimize)

    p = sub.add_parser("walkforward", help="walk-forward IS/OOS (+ --html)")
    p.add_argument("--strategy", required=True, choices=list(strategies.REGISTRY))
    p.add_argument("--grid", required=True)
    p.add_argument("--metric", default="sharpe")
    p.add_argument("--splits", type=int, default=4)
    p.add_argument("--html", help="путь для HTML walk-forward (сшитая OOS-кривая + окна)")
    _add_data_args(p)
    p.set_defaults(func=cmd_walkforward)

    p = sub.add_parser("montecarlo", help="бутстрап устойчивости")
    p.add_argument("--strategy", required=True, choices=list(strategies.REGISTRY))
    p.add_argument("--params", help="k=v,k=v")
    p.add_argument("--source", choices=["trades", "returns"], default="trades")
    p.add_argument("--n", type=int, default=2000)
    p.add_argument("--seed", type=int, default=0)
    _add_data_args(p)
    p.set_defaults(func=cmd_montecarlo)

    p = sub.add_parser("robust", help="оценка робастности (PSR/DSR, чувствительность)")
    p.add_argument("--strategy", required=True, choices=list(strategies.REGISTRY))
    p.add_argument("--params", help="k=v,k=v (если без --grid)")
    p.add_argument("--grid", help='сетка как у optimize; тогда испытаний = размер сетки')
    p.add_argument("--metric", default="sharpe")
    _add_data_args(p)
    p.set_defaults(func=cmd_robust)

    p = sub.add_parser("bench", help="сравнение с buy&hold (alpha/beta/capture)")
    p.add_argument("--strategy", required=True, choices=list(strategies.REGISTRY))
    p.add_argument("--params", help="k=v,k=v")
    _add_data_args(p)
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("tearsheet", help="HTML-оверлей нескольких стратегий")
    p.add_argument("--strategies", required=True, help="через запятую: buyhold,sma_cross,macd")
    p.add_argument("--title", default="tearsheet")
    p.add_argument("--html")
    _add_data_args(p)
    p.set_defaults(func=cmd_tearsheet)

    p = sub.add_parser("export", help="выгрузить equity/trades в CSV+JSON")
    p.add_argument("--strategy", required=True, choices=list(strategies.REGISTRY))
    p.add_argument("--params", help="k=v,k=v")
    p.add_argument("--prefix", help="префикс выходных файлов")
    _add_data_args(p)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("heatmap", help="2D-тепловая карта оптимизации (HTML)")
    p.add_argument("--strategy", required=True, choices=list(strategies.REGISTRY))
    p.add_argument("--grid", required=True)
    p.add_argument("--x", required=True, help="параметр по оси X")
    p.add_argument("--y", required=True, help="параметр по оси Y")
    p.add_argument("--metric", default="sharpe")
    p.add_argument("--html")
    _add_data_args(p)
    p.set_defaults(func=cmd_heatmap)

    p = sub.add_parser("costs", help="чувствительность к комиссии/слиппеджу")
    p.add_argument("--strategy", required=True, choices=list(strategies.REGISTRY))
    p.add_argument("--params", help="k=v,k=v")
    _add_data_args(p)
    p.set_defaults(func=cmd_costs)

    p = sub.add_parser("ensemble", help="портфель из нескольких стратегий (аллокация капитала)")
    p.add_argument("--strategies", required=True, help="через запятую: sma_cross,donchian,macd")
    p.add_argument("--risk-parity", action="store_true", dest="risk_parity",
                   help="веса ∝ 1/волатильность рукава (иначе равные)")
    _add_data_args(p)
    p.set_defaults(func=cmd_ensemble)

    p = sub.add_parser("scenarios", help="прогон по многим синтетическим мирам/режимам")
    p.add_argument("--strategy", required=True, choices=list(strategies.REGISTRY))
    p.add_argument("--params", help="k=v,k=v")
    p.add_argument("--generator", default="gbm",
                   choices=["gbm", "trend", "mean_revert", "sine"])
    p.add_argument("--regimes", action="store_true", help="прогнать по всем режимам сразу")
    p.add_argument("--seeds", type=int, default=50, help="число миров (сидов)")
    p.add_argument("--bars", type=int, default=750)
    p.add_argument("--metric", default="sharpe")
    p.add_argument("--commission", type=float, default=0.0005)
    p.add_argument("--slippage", type=float, default=0.0)
    p.set_defaults(func=cmd_scenarios)

    p = sub.add_parser("risk", help="риск-отчёт (VaR/CVaR/Ulcer/просадки) + календарь доходности")
    p.add_argument("--strategy", required=True, choices=list(strategies.REGISTRY))
    p.add_argument("--params", help="k=v,k=v")
    _add_data_args(p)
    p.set_defaults(func=cmd_risk)

    p = sub.add_parser("study", help="полный пайплайн: optimize→WF→robust→MC→bench + HTML")
    p.add_argument("--strategy", required=True, choices=list(strategies.REGISTRY))
    p.add_argument("--grid", required=True)
    p.add_argument("--metric", default="sharpe")
    p.add_argument("--splits", type=int, default=4)
    p.add_argument("--mc-n", type=int, default=2000, dest="mc_n")
    p.add_argument("--html")
    _add_data_args(p)
    p.set_defaults(func=cmd_study)

    p = sub.add_parser("dashboard", help="сравнительный HTML-дашборд всех JSON-прогонов в папке")
    p.add_argument("--dir", default=".", metavar="DIR",
                   help="директория для поиска *.json (рекурсивно)")
    p.add_argument("--html", help="путь выходного файла (дефолт dashboard.html)")
    p.add_argument("--title", default="Backtest Dashboard")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("fetch", help="скачать реальные свечи в кэш (read-only)")
    p.add_argument("--uid")
    p.add_argument("--ticker")
    p.add_argument("--days", type=int, default=400)
    p.set_defaults(func=cmd_fetch)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
