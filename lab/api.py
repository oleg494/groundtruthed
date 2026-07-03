"""Strategy Lab: общий REST-слой песочницы. ТОЛЬКО sandbox-домен и sandbox-токен."""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://sandbox-invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent

# Ходим к tinkoff НАПРЯМУ, в обход системного SOCKS-прокси. На VPS глобально заданы
# HTTPS_PROXY/ALL_PROXY=socks5://127.0.0.1:1080 для hermes-агента; urllib их подхватывал,
# и когда прокси лёг (ребут 2026-06-17) ферма ослепла на 2 дня с Connection refused.
# tinkoff с VPS доступен напрямую — зависеть от чужого прокси ферма не должна.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def load_token() -> str:
    env = ROOT / ".env"
    if not env.exists():
        env = ROOT.parent / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_SANDBOX_KEY="):
            tok = line.split("=", 1)[1].strip()
            if tok:
                return tok
    raise SystemExit("нет TINVEST_SANDBOX_KEY в .env")


_TOKEN = None


def call(method: str, payload: dict, retries: int = 6) -> dict:
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = load_token()
    url = f"{BASE}/tinkoff.public.invest.api.contract.v1.{method}"
    data = json.dumps(payload).encode()
    delay = 2.0
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {_TOKEN}")
        req.add_header("Content-Type", "application/json")
        try:
            with _OPENER.open(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(float(e.headers.get("Retry-After", delay)))
                delay = min(delay * 2, 30)
                continue
            raise RuntimeError(f"HTTP {e.code} {method}: {body}") from e
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise RuntimeError(f"net {method}: {e}") from e
    raise RuntimeError("retries exhausted")


def to_f(v) -> float:
    if not v:
        return 0.0
    if isinstance(v, dict):
        if v.get("value") not in (None, ""):
            return float(v["value"])
        return int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9
    return 0.0


def quot(p: float, step: float) -> dict:
    p = round(round(p / step) * step, 9)
    units = int(p)
    return {"units": str(units), "nano": int(round((p - units) * 1e9))}
