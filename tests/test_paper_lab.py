from analysis.paper_lab import PaperPosition, render_markdown, snapshot


def test_snapshot_computes_value_and_return():
    positions = [
        PaperPosition("TMON", "money market", qty=10, entry_price=100.0, target_rub=1000),
        PaperPosition("OFZ", "bond", qty=5, entry_price=200.0, target_rub=1000),
    ]

    snap = snapshot(positions, {"TMON": 101.0, "OFZ": 190.0})

    assert snap["cost"] == 2000.0
    assert snap["value"] == 1960.0
    assert snap["pnl"] == -40.0
    assert snap["return_pct"] == -2.0


def test_render_markdown_includes_missing_prices():
    positions = [PaperPosition("TMON", "money market", qty=10, entry_price=100.0, target_rub=1000)]

    md = render_markdown(snapshot(positions, {}), title="Учебный отчёт")

    assert "# Учебный отчёт" in md
    assert "MISSING_PRICE" in md
    assert "TMON" in md
