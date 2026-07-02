"""Отчёты: текстовая сводка в консоль и самодостаточный HTML с inline-SVG.

HTML не тянет внешних библиотек — график equity и «подводная» кривая просадки
рисуются строкой SVG прямо в файл. Открывается в любом браузере офлайн.
"""
from __future__ import annotations

import html as _html
from datetime import datetime, timezone

from .engine import Result
from .metrics import Metrics, metrics


# ───────────────────────── текст ─────────────────────────
def _fmt_pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def text_report(res: Result, m: Metrics | None = None) -> str:
    m = m or metrics(res)
    L = []
    L.append(f"═══ {res.strategy}  {res.params} ═══")
    L.append(f"тикеры: {', '.join(res.data_tickers)}   баров: {res.bars}   "
             f"период: {m.days:.0f} дн.")
    L.append("")
    rows = [
        ("Итоговая доходность", _fmt_pct(m.total_return)),
        ("CAGR (годовых)", _fmt_pct(m.cagr)),
        ("Волатильность (год.)", _fmt_pct(m.ann_vol)),
        ("Sharpe", f"{m.sharpe:.2f}"),
        ("Sortino", f"{m.sortino:.2f}"),
        ("Макс. просадка", _fmt_pct(m.max_drawdown)),
        ("Длит. просадки", f"{m.max_dd_duration_bars} баров"),
        ("Calmar", f"{m.calmar:.2f}"),
        ("Средн. экспозиция", _fmt_pct(m.avg_exposure)),
        ("─ сделки ─", ""),
        ("Кол-во (round-trip)", f"{m.num_trades}"),
        ("Винрейт", _fmt_pct(m.win_rate)),
        ("Profit factor", f"{m.profit_factor:.2f}" if m.profit_factor != float('inf') else "∞"),
        ("Средняя сделка", _fmt_pct(m.avg_trade_ret)),
        ("Средн. прибыль/убыток", f"{m.avg_win:+.0f} / {m.avg_loss:+.0f} ₽"),
        ("Матожидание/сделку", f"{m.expectancy:+.0f} ₽"),
        ("Лучший/худший бар", f"{_fmt_pct(m.best_bar)} / {_fmt_pct(m.worst_bar)}"),
        ("─ деньги ─", ""),
        ("Старт. капитал", f"{res.cash0:,.0f} ₽".replace(",", " ")),
        ("Итоговый капитал", f"{m.final_equity:,.0f} ₽".replace(",", " ")),
        ("Комиссий уплачено", f"{m.commissions_paid:,.0f} ₽".replace(",", " ")),
    ]
    for k, v in rows:
        L.append(f"  {k:.<28} {v}")
    return "\n".join(L)


def compare_report(results: list[Result]) -> str:
    """Таблица сравнения нескольких стратегий — по одному прогону на строку."""
    L = []
    head = (f"{'стратегия':<16}{'ret%':>9}{'cagr%':>8}{'sharpe':>8}"
            f"{'maxDD%':>9}{'calmar':>8}{'trades':>8}{'win%':>7}")
    L.append(head)
    L.append("─" * len(head))
    ranked = sorted(results, key=lambda r: metrics(r).sharpe, reverse=True)
    for r in ranked:
        m = metrics(r)
        pf = "∞" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        L.append(f"{r.strategy:<16}{m.total_return * 100:>+9.2f}{m.cagr * 100:>+8.2f}"
                 f"{m.sharpe:>8.2f}{m.max_drawdown * 100:>9.2f}{m.calmar:>8.2f}"
                 f"{m.num_trades:>8}{m.win_rate * 100:>7.1f}")
    return "\n".join(L)


# ───────────────────────── HTML / SVG ─────────────────────────
def _drawdown(equity: list[float]) -> list[float]:
    peak, out = equity[0], []
    for v in equity:
        peak = max(peak, v)
        out.append(v / peak - 1.0 if peak else 0.0)
    return out


def _polyline(values: list[float], w: int, h: int, pad: int,
              vmin: float, vmax: float) -> str:
    n = len(values)
    if n < 2 or vmax == vmin:
        return ""
    pts = []
    for i, v in enumerate(values):
        x = pad + (w - 2 * pad) * i / (n - 1)
        y = pad + (h - 2 * pad) * (1 - (v - vmin) / (vmax - vmin))
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def _svg_equity(res: Result, w: int = 920, h: int = 320) -> str:
    eq = res.equity or [res.cash0]
    pad = 40
    vmin, vmax = min(eq), max(eq)
    margin = (vmax - vmin) * 0.05 or 1
    vmin, vmax = vmin - margin, vmax + margin
    line = _polyline(eq, w, h, pad, vmin, vmax)
    base_y = pad + (h - 2 * pad) * (1 - (res.cash0 - vmin) / (vmax - vmin))
    # горизонтали-гайды
    grid = "".join(
        f'<line x1="{pad}" y1="{pad + (h - 2 * pad) * k / 4:.0f}" '
        f'x2="{w - pad}" y2="{pad + (h - 2 * pad) * k / 4:.0f}" '
        f'stroke="#26304a" stroke-width="1"/>' for k in range(5))
    return f'''<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg">
  <rect width="{w}" height="{h}" fill="#0d1424"/>
  {grid}
  <line x1="{pad}" y1="{base_y:.1f}" x2="{w - pad}" y2="{base_y:.1f}"
        stroke="#5a6" stroke-dasharray="4 4" stroke-width="1"/>
  <polyline points="{line}" fill="none" stroke="#4fd1c5" stroke-width="2"/>
  <text x="{pad}" y="22" fill="#8aa" font-family="monospace" font-size="13">
    equity: {res.cash0:,.0f} → {eq[-1]:,.0f} ₽</text>
</svg>'''.replace(",", " ")


def _svg_drawdown(res: Result, w: int = 920, h: int = 160) -> str:
    dd = _drawdown(res.equity or [res.cash0])
    pad = 40
    vmin, vmax = min(dd + [0.0]), 0.0
    area = _polyline(dd, w, h, pad, vmin, vmax)
    # замыкаем в заливку до нулевой линии
    zero_y = pad + (h - 2 * pad) * (1 - (0 - vmin) / (vmax - vmin)) if vmax != vmin else pad
    poly = f"{pad},{zero_y:.1f} {area} {w - pad},{zero_y:.1f}" if area else ""
    return f'''<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg">
  <rect width="{w}" height="{h}" fill="#0d1424"/>
  <polygon points="{poly}" fill="#e5484d33" stroke="#e5484d" stroke-width="1.5"/>
  <text x="{pad}" y="22" fill="#8aa" font-family="monospace" font-size="13">
    просадка (underwater), мин {min(dd) * 100:.1f}%</text>
</svg>'''


_PALETTE = ["#4fd1c5", "#f6ad55", "#9f7aea", "#fc8181", "#68d391",
            "#63b3ed", "#f687b3", "#d6bcfa"]


def _norm_equity(res: Result) -> list[float]:
    base = res.cash0 or 1.0
    return [e / base * 100.0 for e in (res.equity or [base])]


def tearsheet_html(results: list[Result], title: str = "tearsheet") -> str:
    """Сравнение нескольких прогонов: оверлей нормированных equity (база 100) + таблица."""
    w, h, pad = 920, 360, 40
    all_vals = [v for r in results for v in _norm_equity(r)]
    vmin, vmax = (min(all_vals), max(all_vals)) if all_vals else (100, 100)
    margin = (vmax - vmin) * 0.05 or 1
    vmin, vmax = vmin - margin, vmax + margin
    lines, legend = [], []
    for k, r in enumerate(results):
        color = _PALETTE[k % len(_PALETTE)]
        pts = _polyline(_norm_equity(r), w, h, pad, vmin, vmax)
        lines.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8"/>')
        m = metrics(r)
        legend.append((color, r.strategy, m))
    base_y = pad + (h - 2 * pad) * (1 - (100.0 - vmin) / (vmax - vmin))
    grid = "".join(
        f'<line x1="{pad}" y1="{pad + (h - 2 * pad) * k / 4:.0f}" '
        f'x2="{w - pad}" y2="{pad + (h - 2 * pad) * k / 4:.0f}" '
        f'stroke="#26304a" stroke-width="1"/>' for k in range(5))
    svg = (f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg">'
           f'<rect width="{w}" height="{h}" fill="#0d1424"/>{grid}'
           f'<line x1="{pad}" y1="{base_y:.1f}" x2="{w - pad}" y2="{base_y:.1f}" '
           f'stroke="#5a6" stroke-dasharray="4 4"/>' + "".join(lines) +
           f'<text x="{pad}" y="22" fill="#8aa" font-family="monospace" font-size="13">'
           f'equity, база 100</text></svg>')
    rows = "".join(
        f'<tr><td><span style="color:{c}">●</span> {_html.escape(name)}</td>'
        f'<td>{m.total_return*100:+.2f}%</td><td>{m.cagr*100:+.2f}%</td>'
        f'<td>{m.sharpe:.2f}</td><td>{m.max_drawdown*100:.2f}%</td>'
        f'<td>{m.num_trades}</td></tr>' for c, name, m in legend)
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html.escape(title)}</title><style>
 body{{background:#0a0f1c;color:#cdd6e4;font-family:-apple-system,Segoe UI,sans-serif;margin:0;padding:24px}}
 h1{{font-size:20px}} .chart{{border:1px solid #1e2942;border-radius:10px;overflow:hidden;margin:12px 0}}
 table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{text-align:right;padding:6px 10px;border-bottom:1px solid #1e2942}}
 td:first-child,th:first-child{{text-align:left}}
</style></head><body><h1>{_html.escape(title)}</h1>
<div class="chart">{svg}</div>
<table><tr><th>стратегия</th><th>ret</th><th>CAGR</th><th>Sharpe</th><th>maxDD</th><th>сделок</th></tr>
{rows}</table></body></html>'''


def heatmap_html(grid_points, x_param: str, y_param: str,
                 metric: str = "sharpe", title: str = "optimize heatmap") -> str:
    """2D-тепловая карта метрики по двум параметрам сетки (inline-SVG)."""
    xs = sorted({p.params[x_param] for p in grid_points})
    ys = sorted({p.params[y_param] for p in grid_points})
    cell = {}
    for p in grid_points:
        v = getattr(p.metrics, metric)
        cell[(p.params[x_param], p.params[y_param])] = v if v == v and abs(v) != float("inf") else None
    vals = [v for v in cell.values() if v is not None]
    lo, hi = (min(vals), max(vals)) if vals else (0, 1)
    rng = (hi - lo) or 1.0

    def color(v):
        if v is None:
            return "#222"
        f = (v - lo) / rng                      # 0..1 → красный→зелёный
        r = int(229 + (104 - 229) * f)
        g = int(72 + (211 - 72) * f)
        b = int(77 + (145 - 77) * f)
        return f"rgb({r},{g},{b})"

    cw, ch, ox, oy = 70, 36, 70, 40
    W = ox + cw * len(xs) + 20
    H = oy + ch * len(ys) + 40
    rects = []
    for iy, yv in enumerate(ys):
        for ix, xv in enumerate(xs):
            v = cell.get((xv, yv))
            x = ox + ix * cw
            y = oy + iy * ch
            label = "" if v is None else f"{v:.2f}"
            rects.append(
                f'<rect x="{x}" y="{y}" width="{cw-2}" height="{ch-2}" fill="{color(v)}"/>'
                f'<text x="{x+cw/2-1}" y="{y+ch/2+4}" fill="#0a0f1c" font-size="11" '
                f'text-anchor="middle" font-family="monospace">{label}</text>')
    xlab = "".join(f'<text x="{ox+ix*cw+cw/2-1}" y="{oy-8}" fill="#8aa" font-size="11" '
                   f'text-anchor="middle" font-family="monospace">{xv}</text>'
                   for ix, xv in enumerate(xs))
    ylab = "".join(f'<text x="{ox-8}" y="{oy+iy*ch+ch/2+4}" fill="#8aa" font-size="11" '
                   f'text-anchor="end" font-family="monospace">{yv}</text>'
                   for iy, yv in enumerate(ys))
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>{_html.escape(title)}</title><style>body{{background:#0a0f1c;margin:0;padding:20px}}
 text{{}} h1{{color:#cdd6e4;font-family:monospace;font-size:15px}}</style></head><body>
<h1>{_html.escape(title)}: {_html.escape(metric)} по {_html.escape(x_param)} (X) × {_html.escape(y_param)} (Y)</h1>
<svg viewBox="0 0 {W} {H}" width="{W}" xmlns="http://www.w3.org/2000/svg">
<rect width="{W}" height="{H}" fill="#0a0f1c"/>{xlab}{ylab}{"".join(rects)}</svg>
</body></html>'''


def walkforward_html(wf, title: str = "walk-forward (OOS)") -> str:
    """HTML walk-forward: сшитая out-of-sample equity-кривая + таблица окон."""
    w, h, pad = 920, 320, 40
    eq = wf.equity or [1.0]
    base = eq[0] or 1.0
    norm = [e / base * 100.0 for e in eq]
    vmin, vmax = min(norm), max(norm)
    margin = (vmax - vmin) * 0.05 or 1
    vmin, vmax = vmin - margin, vmax + margin
    line = _polyline(norm, w, h, pad, vmin, vmax)
    base_y = pad + (h - 2 * pad) * (1 - (100.0 - vmin) / (vmax - vmin))
    grid = "".join(
        f'<line x1="{pad}" y1="{pad + (h - 2 * pad) * k / 4:.0f}" '
        f'x2="{w - pad}" y2="{pad + (h - 2 * pad) * k / 4:.0f}" '
        f'stroke="#26304a" stroke-width="1"/>' for k in range(5))
    svg = (f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg">'
           f'<rect width="{w}" height="{h}" fill="#0d1424"/>{grid}'
           f'<line x1="{pad}" y1="{base_y:.1f}" x2="{w - pad}" y2="{base_y:.1f}" '
           f'stroke="#5a6" stroke-dasharray="4 4"/>'
           f'<polyline points="{line}" fill="none" stroke="#4fd1c5" stroke-width="2"/>'
           f'<text x="{pad}" y="22" fill="#8aa" font-family="monospace" font-size="13">'
           f'сквозная OOS equity (база 100), итог {wf.oos_return()*100:+.1f}%</text></svg>')
    rows = "".join(
        f'<tr><td>{i+1}</td><td>{w_.is_metric:.2f}</td>'
        f'<td>{w_.oos.total_return*100:+.2f}%</td><td>{w_.oos.sharpe:.2f}</td>'
        f'<td>{w_.oos.max_drawdown*100:.2f}%</td><td>{_html.escape(str(w_.best_params))}</td></tr>'
        for i, w_ in enumerate(wf.windows))
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html.escape(title)}</title><style>
 body{{background:#0a0f1c;color:#cdd6e4;font-family:-apple-system,Segoe UI,sans-serif;margin:0;padding:24px}}
 h1{{font-size:20px}} .chart{{border:1px solid #1e2942;border-radius:10px;overflow:hidden;margin:12px 0}}
 table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{text-align:right;padding:6px 10px;border-bottom:1px solid #1e2942}}
 td:last-child,th:last-child{{text-align:left}} td:first-child,th:first-child{{text-align:left}}
</style></head><body><h1>{_html.escape(title)} · оптим. по {_html.escape(wf.metric)}</h1>
<div class="chart">{svg}</div>
<table><tr><th>окно</th><th>IS {_html.escape(wf.metric)}</th><th>OOS ret</th><th>OOS Sharpe</th>
<th>OOS maxDD</th><th>лучшие параметры</th></tr>{rows}</table></body></html>'''


def study_html(study, title: str | None = None) -> str:
    """Единый HTML-отчёт исследования (Study): метрики, equity+overlay, heatmap, вердикт."""
    res = study.best
    m = study.best_metrics
    title = title or f"study · {res.strategy}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pf = "∞" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
    cards = [
        ("Доходность", _fmt_pct(m.total_return)), ("CAGR", _fmt_pct(m.cagr)),
        ("Sharpe", f"{m.sharpe:.2f}"), ("Макс. просадка", _fmt_pct(m.max_drawdown)),
        ("Deflated SR", f"{study.robustness.deflated_sharpe*100:.0f}%"),
        ("OOS ratio", f"{study.oos.get('ratio', 0):.2f}"),
        ("MC p5", _fmt_pct(study.montecarlo.ret_p5)),
        ("alpha (год.)", _fmt_pct(study.benchmark.alpha_annual)),
    ]
    card_html = "".join(
        f'<div class="card"><div class="k">{_html.escape(k)}</div>'
        f'<div class="v">{_html.escape(v)}</div></div>' for k, v in cards)
    # оверлей best vs buy&hold
    overlay = tearsheet_html([res, study.benchmark_result], "best vs buy&hold")
    overlay_svg = overlay[overlay.find("<svg"):overlay.find("</svg>") + 6]
    # heatmap если ровно 2 параметра в сетке
    hm = ""
    grid_params = [k for k, v in study.grid.items() if len(v) > 1]
    if len(grid_params) >= 2:
        h = heatmap_html(study.grid_points, grid_params[0], grid_params[1], study.metric)
        hm = '<div class="chart">' + h[h.find("<svg"):h.find("</svg>") + 6] + "</div>"
    from .study import text_study
    sections = _html.escape(text_study(study))
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html.escape(title)}</title><style>
 body{{background:#0a0f1c;color:#cdd6e4;font-family:-apple-system,Segoe UI,sans-serif;margin:0;padding:24px}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#6b7a99;font-size:13px;margin-bottom:18px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin:16px 0}}
 .card{{background:#121a2e;border:1px solid #1e2942;border-radius:10px;padding:12px}}
 .k{{color:#6b7a99;font-size:12px}} .v{{font-size:20px;font-weight:600;margin-top:4px}}
 .chart{{border:1px solid #1e2942;border-radius:10px;overflow:hidden;margin:12px 0}}
 pre{{background:#121a2e;border:1px solid #1e2942;border-radius:10px;padding:14px;overflow:auto;font-size:13px;line-height:1.45}}
 code{{color:#4fd1c5}}
</style></head><body>
<h1>study · <code>{_html.escape(res.strategy)}</code></h1>
<div class="sub">сетка {_html.escape(str(study.grid))} · лучшие {_html.escape(str(study.best_params))}
 · тикеры {_html.escape(", ".join(res.data_tickers))} · {now}</div>
<div class="grid">{card_html}</div>
<div class="chart">{_svg_equity(res)}</div>
<div class="chart">{_svg_drawdown(res)}</div>
<div class="chart">{overlay_svg}</div>
{hm}
<h3>Полная сводка пайплайна</h3>
<pre>{sections}</pre>
</body></html>'''


def html_report(res: Result, m: Metrics | None = None) -> str:
    m = m or metrics(res)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pf = "∞" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
    cards = [
        ("Доходность", _fmt_pct(m.total_return)),
        ("CAGR", _fmt_pct(m.cagr)),
        ("Sharpe", f"{m.sharpe:.2f}"),
        ("Sortino", f"{m.sortino:.2f}"),
        ("Макс. просадка", _fmt_pct(m.max_drawdown)),
        ("Calmar", f"{m.calmar:.2f}"),
        ("Волат-ть (год.)", _fmt_pct(m.ann_vol)),
        ("Сделок", f"{m.num_trades}"),
        ("Винрейт", _fmt_pct(m.win_rate)),
        ("Profit factor", pf),
        ("Экспозиция", _fmt_pct(m.avg_exposure)),
        ("Комиссии", f"{m.commissions_paid:,.0f} ₽".replace(",", " ")),
    ]
    card_html = "".join(
        f'<div class="card"><div class="k">{_html.escape(k)}</div>'
        f'<div class="v">{_html.escape(v)}</div></div>' for k, v in cards)
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>backtest — {_html.escape(res.strategy)}</title>
<style>
  body{{background:#0a0f1c;color:#cdd6e4;font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px}}
  h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#6b7a99;font-size:13px;margin-bottom:18px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin:16px 0}}
  .card{{background:#121a2e;border:1px solid #1e2942;border-radius:10px;padding:12px}}
  .k{{color:#6b7a99;font-size:12px}} .v{{font-size:20px;font-weight:600;margin-top:4px}}
  .chart{{border:1px solid #1e2942;border-radius:10px;overflow:hidden;margin:12px 0}}
  code{{color:#4fd1c5}}
</style></head><body>
<h1>backtest · <code>{_html.escape(res.strategy)}</code></h1>
<div class="sub">{_html.escape(str(res.params))} · тикеры {_html.escape(", ".join(res.data_tickers))}
 · {res.bars} баров · {m.days:.0f} дн · сгенерировано {now}</div>
<div class="grid">{card_html}</div>
<div class="chart">{_svg_equity(res)}</div>
<div class="chart">{_svg_drawdown(res)}</div>
</body></html>'''
