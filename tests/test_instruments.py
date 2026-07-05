from datetime import date

import pytest

from lab.instruments import INSTRUMENTS, apply_roll, futures_roll_warnings


@pytest.fixture(autouse=True)
def _restore_instruments():
    """Снимок INSTRUMENTS до теста, восстановление после.

    apply_roll мутирует реестр in-place (это его семантика для runtime-синглтона).
    Тесты изоляции не должны видеть мутации соседей.
    """
    snapshot = {t: dict(m) for t, m in INSTRUMENTS.items()}
    yield
    INSTRUMENTS.clear()
    INSTRUMENTS.update(snapshot)


def test_futures_roll_warnings_near_last_trade_date():
    warnings = futures_roll_warnings(today=date(2026, 7, 27), warn_days=3)

    by_ticker = {w["ticker"]: w for w in warnings}
    assert "NGN6" in by_ticker
    assert by_ticker["NGN6"]["days_to_last_trade"] == 2
    assert by_ticker["NGN6"]["roll_to"] == "NGQ6"
    assert "BMQ6" not in by_ticker


def test_futures_roll_warnings_after_expiration_is_expired():
    warnings = futures_roll_warnings(today=date(2026, 7, 31), warn_days=3)

    ng = [w for w in warnings if w["ticker"] == "NGN6"][0]
    assert ng["status"] == "EXPIRED"


# --- apply_roll: офлайн-ролл фьючерса на преемника ----------------------------

def test_apply_roll_ngn6_to_ngq6_swaps_uid_and_dates():
    """До ролла NGN6 указывает на NG-7.26, после — на NG-8.26 (преемник)."""
    before = INSTRUMENTS["NGN6"]
    assert before["uid"] == "ddd7405e-f3df-4c29-a876-e865013c4e54"  # NG-7.26
    assert before["exp"] == "2026-07-30"

    rolled = apply_roll("NGN6")

    assert rolled["uid"] == "79d16f7f-3ca4-4ae2-aa5b-183ed22618e9"  # NG-8.26
    assert rolled["exp"] == "2026-08-28"
    assert rolled["last_trade"] == "2026-08-27"
    assert rolled["roll_to"] == ""  # у нового контракта преемника пока нет
    # структурные поля сохраняются
    assert rolled["kind"] == "futures"
    assert rolled["point_rub"] == before["point_rub"]
    # реестр обновлён in-place
    assert INSTRUMENTS["NGN6"]["uid"] == rolled["uid"]


def test_apply_roll_restores_previous_state_on_rollback():
    """Повторный ролл невозможен: у свежекатанного контракта нет roll_to → ValueError."""
    apply_roll("NGN6")
    with pytest.raises(ValueError, match="roll_to"):
        apply_roll("NGN6")


def test_apply_roll_unknown_ticker_raises():
    with pytest.raises(KeyError):
        apply_roll("NOPE")


def test_apply_roll_share_raises():
    """Ролл акций не определён — только фьючерсы."""
    with pytest.raises(ValueError, match="фьючерс"):  # русское сообщение в коде
        apply_roll("SBER")
