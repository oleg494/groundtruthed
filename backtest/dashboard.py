"""Сводный дашборд прогонов: HTML-таблица по всем JSON-файлам из export.to_json.

Позволяет сравнить десятки прогонов одним взглядом: CAGR, Sharpe, maxDD, число сделок,
итоговый equity — всё в одной таблице с цветовой тепловой картой столбцов и мини-
спарклайном equity-кривой.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

# ──────────────────────────────── загрузка ─────────────────────────────────

def load_results(directory: str) -> list[dict]:
    """Сканирует директорию рекурсивно, возвращает список распарсенных JSON-записей.

    Каждый элемент — словарь из to_json (strategy/params/metrics/equity/...).
    Файлы без обязательных ключей пропускаются.
    """
    results = []
    for p in sorted(Path(directory).rglob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if "metrics" not in data or "strategy" not in data:
            continue
        data["_file"] = str(p)
        results.append(data)
    return results


# ─────────────────────────────── спарклайн ─────────────────────────────────

def _sparkline_svg(equity: list[float], width: int = 80, height: int = 24) -> str:
    """Мини SVG-линия equity (inline, без внешних зависимостей)."""
    vals = [e["equity"] if isinstance(e, dict) else e for e in equity]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = hi - lo or 1.0
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = i / (n - 1) * width
        y = height - (v - lo) / span * height
        pts.append(f"{x:.1f},{y:.1f}")
    color = "#2a7" if vals[-1] >= vals[0] else "#c44"
    polyline = " ".join(pts)
    return (f'<svg width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
            f'<polyline points="{polyline}" fill="none" stroke="{color}" '
            f'stroke-width="1.5" stroke-linejoin="round"/></svg>')


# ──────────────────────────── тепловая карта ───────────────────────────────

def _heat_bg(value: float, lo: float, hi: float,
             low_good: bool = False) -> str:
    """CSS background-color для ячейки: зелёный=хорошо, красный=плохо."""
    if not math.isfinite(value) or not math.isfinite(lo) or not math.isfinite(hi) or lo == hi:
        return ""
    t = (value - lo) / (hi - lo)        # 0..1, 1 = лучше числом
    if low_good:
        t = 1 - t
    r = int(220 * (1 - t) + 40 * t)
    g = int(40 * (1 - t) + 180 * t)
    b = 60
    return f"background:rgba({r},{g},{b},0.25)"


def _col_bounds(rows: list[dict], key: str) -> tuple[float, float]:
    vals = []
    for r in rows:
        v = r["metrics"].get(key)
        if v is not None and math.isfinite(float(v)):
            vals.append(float(v))
    if not vals:
        return 0.0, 1.0
    return min(vals), max(vals)


# ─────────────────────────── построение HTML ───────────────────────────────

_COLS = [
    # (ключ метрики, заголовок, low_good, формат)
    ("total_return",       "Return",      False, "{:.1%}"),
    ("cagr",               "CAGR",        False, "{:.1%}"),
    ("ann_vol",            "Vol",         True,  "{:.1%}"),
    ("sharpe",             "Sharpe",      False, "{:.2f}"),
    ("sortino",            "Sortino",     False, "{:.2f}"),
    ("max_drawdown",       "MaxDD",       True,  "{:.1%}"),
    ("calmar",             "Calmar",      False, "{:.2f}"),
    ("num_trades",         "Trades",      False, "{:.0f}"),
    ("win_rate",           "WinRate",     False, "{:.1%}"),
    ("profit_factor",      "PF",          False, "{:.2f}"),
    ("commissions_paid",   "Commissions", True,  "{:.0f}"),
    ("final_equity",       "FinalEquity", False, "{:.0f}"),
]


def build_dashboard(results: list[dict], title: str = "Backtest Dashboard") -> str:
    """Строит HTML-страницу с таблицей сравнения прогонов.

    Возвращает строку HTML (UTF-8). Сохрани через Path(...).write_text(...).
    """
    if not results:
        return "<html><body><p>Нет данных.</p></body></html>"

    # предвычислить границы для тепловой карты
    bounds = {key: _col_bounds(results, key) for key, *_ in _COLS}

    # CSS
    style = """
body{font:13px/1.4 monospace;background:#111;color:#ccc;margin:20px}
h1{color:#eee;font-size:16px;margin-bottom:12px}
table{border-collapse:collapse;width:100%}
th{background:#222;color:#aaa;padding:5px 8px;border:1px solid #333;
   font-size:11px;white-space:nowrap;position:sticky;top:0;z-index:1}
td{padding:4px 8px;border:1px solid #2a2a2a;white-space:nowrap;font-size:12px}
tr:hover td{background:#1e1e1e!important}
.strat{color:#8bf;max-width:200px;overflow:hidden;text-overflow:ellipsis}
.params{color:#888;max-width:180px;overflow:hidden;text-overflow:ellipsis;font-size:11px}
.file{color:#555;font-size:10px;max-width:160px;overflow:hidden;text-overflow:ellipsis}
.pos{color:#5c5}
.neg{color:#c55}
"""

    rows_html = []
    for r in results:
        m = r["metrics"]
        strat = r.get("strategy", "?")
        params = ", ".join(f"{k}={v}" for k, v in r.get("params", {}).items())
        fname = Path(r.get("_file", "")).name

        eq_raw = r.get("equity", [])
        spark = _sparkline_svg(eq_raw) if eq_raw else ""

        cells = [
            f'<td class="strat" title="{strat}">{strat}</td>',
            f'<td class="params" title="{params}">{params or "—"}</td>',
            f'<td>{spark}</td>',
            f'<td class="file" title="{r.get("_file","")}">{fname}</td>',
        ]

        for key, _, low_good, fmt in _COLS:
            raw = m.get(key)
            if raw is None:
                cells.append("<td>—</td>")
                continue
            v = float(raw)
            lo, hi = bounds[key]
            bg = _heat_bg(v, lo, hi, low_good)
            style_attr = f' style="{bg}"' if bg else ""
            # цвет знакового числа
            cls = ""
            if key in ("total_return", "cagr", "max_drawdown", "sharpe", "sortino", "calmar"):
                cls = ' class="pos"' if v >= 0 else ' class="neg"'
            # бесконечность profit_factor
            if not math.isfinite(v):
                txt = "∞" if v > 0 else "-∞"
            else:
                txt = fmt.format(v)
            cells.append(f"<td{style_attr}{cls}>{txt}</td>")

        rows_html.append("<tr>" + "".join(cells) + "</tr>")

    header_cells = (
        "<th>Strategy</th><th>Params</th><th>Equity</th><th>File</th>"
        + "".join(f"<th>{h}</th>" for _, h, *_ in _COLS)
    )

    count = len(results)
    html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>{title}</title>
<style>{style}</style>
</head><body>
<h1>{title} &nbsp;<small style="color:#555;font-size:12px">{count} прогонов</small></h1>
<table>
<thead><tr>{header_cells}</tr></thead>
<tbody>
{"".join(rows_html)}
</tbody>
</table>
</body></html>"""
    return html


def save_dashboard(directory: str, out_path: str,
                   title: str = "Backtest Dashboard") -> int:
    """Сканирует directory, строит дашборд, сохраняет в out_path.

    Возвращает число загруженных прогонов.
    """
    results = load_results(directory)
    html = build_dashboard(results, title=title)
    Path(out_path).write_text(html, encoding="utf-8")
    return len(results)
