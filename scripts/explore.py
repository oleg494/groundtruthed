"""T-Invest API Explorer — проверка подключения и базовый обзор портфеля (SDK-путь).

Мигрирован на новый SDK `t_tech.invest` (пакет t-tech-investments, домен *.tbank.ru).
Импорт — с откатом на старый `tinkoff.invest`, пока он ещё где-то стоит: берётся тот
SDK, что доступен. Новый домен `invest-public-api.tbank.ru` использует сертификат
МинЦифры — он зашит в пакет и включается переменной SSL_TBANK_VERIFY=True (ставим
по умолчанию, если не задана). Токен — боевой read-only (TINVEST_API_KEY).
"""
import os

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get("TINVEST_API_KEY")
if not TOKEN:
    print("ERROR: TINVEST_API_KEY не найден в .env!")
    exit(1)

# Новый домен *.tbank.ru отдаёт сертификат МинЦифры (не в дефолтном trust store).
# SDK t_tech везёт его с собой; включаем проверку этим сертификатом.
os.environ.setdefault("SSL_TBANK_VERIFY", "True")

# Предпочитаем новый SDK; откатываемся на старый, если установлен только он.
try:
    from t_tech.invest import Client
    _SDK = "t_tech.invest (t-tech-investments, домен tbank.ru)"
except ImportError:
    try:
        from tinkoff.invest import Client
        _SDK = "tinkoff.invest (устаревший, домен tinkoff.ru)"
    except ImportError:
        print("ERROR: нет SDK. Установи новый:\n"
              "  pip install t-tech-investments --index-url "
              "https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple")
        exit(1)


def main():
    print(f"SDK: {_SDK}\n")
    with Client(TOKEN) as client:
        accounts = client.users.get_accounts()
        print("=== АККАУНТЫ ===")
        for acc in accounts.accounts:
            print(f"  {acc.name} | {acc.id} | {acc.status} | {acc.type}")

        if not accounts.accounts:
            print("  Нет аккаунтов!")
            return

        acc_id = accounts.accounts[0].id

        portfolio = client.operations.get_portfolio(account_id=acc_id)
        print(f"\n=== ПОРТФЕЛЬ {acc_id} (итого: {portfolio.total_amount_portfolio}) ===")
        for pos in portfolio.positions:
            print(f"  {pos.instrument_type:<8} | {pos.quantity} | "
                  f"цена {pos.current_price} | дох {pos.expected_yield}")

        # PortfolioResponse не имеет .currencies (это был баг старого кода, не
        # всплывавший: SDK не был установлен). Кэш — в total_amount_currencies;
        # отдельные валютные позиции лежат в positions с instrument_type=='currency'.
        print("\n=== ВАЛЮТЫ (кэш) ===")
        print(f"  всего: {portfolio.total_amount_currencies}")
        for pos in portfolio.positions:
            if pos.instrument_type == "currency":
                print(f"  {pos.quantity}")


if __name__ == "__main__":
    main()
