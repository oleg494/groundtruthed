from datetime import date

from scripts.keyrate import KEYRATE, keyrate_on


def test_current_keyrate_matches_latest_known_event():
    assert KEYRATE == 14.25
    assert keyrate_on(date(2026, 7, 5)) == KEYRATE


def test_keyrate_calendar_uses_effective_dates():
    assert keyrate_on(date(2026, 6, 18)) == 14.50
    assert keyrate_on(date(2026, 6, 19)) == 14.25


def test_keyrate_import_fallback_supports_package_import():
    import scripts.stress_test_floaters as stress

    assert stress.KEY_RATE == KEYRATE
