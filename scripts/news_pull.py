"""Сборщик новостей: RSS деловых СМИ + пресс-релизы ЦБ → markdown-файл.

    python scripts/news_pull.py            # всё за сегодня-вчера
    python scripts/news_pull.py --all      # без фильтра по ключевым словам

Без зависимостей (urllib + xml из stdlib). Заголовки фильтруются по ключевым
словам (ставка, ОФЗ, тикеры портфеля...). Результат: analysis/news_YYYY-MM-DD.md —
дальше файл разбирает Claude (методология morning-note из финансовых скиллов Anthropic — не входит в этот репозиторий).
"""
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FEEDS = [
    ("ЦБ РФ: пресс-релизы", "https://www.cbr.ru/rss/RssPress"),
    ("ЦБ РФ: события",      "https://www.cbr.ru/rss/eventrss"),
    ("РБК",                 "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"),
    ("Коммерсантъ",         "https://www.kommersant.ru/rss/news.xml"),
    ("Интерфакс",           "https://www.interfax.ru/rss"),
]

# что считаем релевантным для портфеля «фонд ликвидности + ОФЗ + ставка»
# (\b — граница слова: чтобы «ставк» не ловил «выставку»)
KEYWORDS = re.compile(r"\b(" + "|".join([
    "ключев", "ставк", "цб", "центробанк", "банк россии", "инфляц",
    "офз", "облигаци", "минфин", "аукцион",
    "мосбирж", "moex", "индекс",
    "рубл", "доллар", "юан", "курс",
    "сбер", "т-банк", "тинькофф",
    "налог", "ндфл", "иис", "вклад", "депозит",
    "санкц", "нефт", "brent",
]) + ")")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def parse_rss(data: bytes) -> list:
    """[(dt|None, title, link)] — терпимо к кривым датам и невалидным фидам."""
    out = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        dt = None
        raw = item.findtext("pubDate") or ""
        try:
            dt = parsedate_to_datetime(raw.strip())
        except (ValueError, TypeError):
            pass
        if title:
            out.append((dt, title, link))
    return out


def main():
    take_all = "--all" in sys.argv
    since = datetime.now().astimezone() - timedelta(days=1)
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# Новости {today}",
             f"_фильтр: {'выключен (--all)' if take_all else 'ставка/ОФЗ/рубль/портфель'};"
             f" окно: последние 24ч (или без даты)_", ""]
    total = kept = 0
    for src, url in FEEDS:
        try:
            items = parse_rss(fetch(url))
        except Exception as e:
            lines += [f"## {src}", f"_не доступен: {e}_", ""]
            continue
        total += len(items)
        sel = []
        for dt, title, link in items:
            if dt and dt < since:
                continue
            if not take_all and not KEYWORDS.search(title.lower()):
                continue
            t = dt.strftime("%H:%M") if dt else "--:--"
            sel.append(f"- {t} [{title}]({link})")
        kept += len(sel)
        lines += [f"## {src} ({len(sel)})"] + (sel or ["_ничего релевантного_"]) + [""]

    out = ROOT / "analysis" / f"news_{today}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"лент: {len(FEEDS)}, заголовков: {total}, отобрано: {kept}")
    print(f"файл: {out}")
    print("дальше: попроси Claude разобрать файл по методологии morning-note")


if __name__ == "__main__":
    main()
