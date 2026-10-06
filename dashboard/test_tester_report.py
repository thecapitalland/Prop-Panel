"""Smoke tests for MT5 Strategy Tester HTML report parser — no live trading."""
from __future__ import annotations

import os
import unittest

from tester_report import parse_tester_report, compute_score
from tester_runner import (
    generate_ini,
    get_preset,
    import_existing_report,
    list_presets,
    load_history_status,
    RESULTS_DIR,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "tester", "fixtures", "OLC_smoke_gold.htm")


class TesterReportParseTests(unittest.TestCase):
    def test_sample_report_metrics(self):
        self.assertTrue(os.path.isfile(SAMPLE), f"missing sample: {SAMPLE}")
        row = parse_tester_report(SAMPLE, deposit=50000.0)
        self.assertEqual(row["symbol"], "XAUUSD")
        self.assertEqual(row["expert"], "OrderLevelControl_Prop_EA")
        self.assertEqual(row["total_trades"], 9)
        self.assertEqual(row["win_rate"], 44.4)
        self.assertEqual(row["profit_factor"], 0.44)
        self.assertEqual(row["net_profit_usd"], -222.81)
        self.assertAlmostEqual(row["net_profit_pct"], round((-222.81 / 50000) * 100, 2))
        self.assertEqual(row["max_drawdown_pct"], 0.64)
        self.assertEqual(row["strategy"], "NY ORB")
        self.assertEqual(row["risk_pct"], 0.3)
        self.assertEqual(row["source"], "mt5_tester")
        expected_score = compute_score(row["net_profit_pct"], row["profit_factor"], row["max_drawdown_pct"])
        self.assertEqual(row["score"], expected_score)

    def test_generate_ini_contains_symbol_and_dates(self):
        preset = get_preset("ny_orb_gold")
        self.assertIsNotNone(preset)
        body = generate_ini(
            preset,
            "2025-12-07",
            "2026-08-04",
            r"C:\PropPanel\dashboard\tester_results\demo",
            r"C:\PropPanel\tester\olc_ny_orb_gold.set",
        )
        self.assertIn("Symbol=XAUUSD", body)
        self.assertIn("FromDate=2025.12.07", body)
        self.assertIn("ToDate=2026.08.04", body)
        self.assertIn("Expert=OrderLevelControl_Prop_EA", body)
        self.assertIn("ShutdownTerminal=1", body)

    def test_presets_and_history(self):
        presets = list_presets()
        ids = {p["id"] for p in presets}
        self.assertIn("ny_orb_gold", ids)
        self.assertIn("asian_eurusd", ids)
        self.assertIn("asian_eurcad", ids)
        hist = load_history_status()
        self.assertTrue(hist.get("ok"), hist)
        syms = {s["symbol"] for s in hist["symbols"]}
        self.assertTrue({"EURUSD", "GBPUSD", "XAUUSD", "EURCAD"}.issubset(syms))

    def test_import_sample_into_results(self):
        row = import_existing_report(SAMPLE, deposit=50000.0)
        self.assertEqual(row["total_trades"], 9)
        self.assertTrue(os.path.isdir(RESULTS_DIR))
        self.assertTrue(os.path.isfile(row["json_path"]))


if __name__ == "__main__":
    unittest.main()
