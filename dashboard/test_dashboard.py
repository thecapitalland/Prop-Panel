"""Unit tests for Wave 1+2 dashboard — no MetaTrader process mutation."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from prop_metrics import build_prop_payload, next_reset_countdown, day_bucket


def _deal(pos, entry, typ, symbol, t, profit=0.0, volume=0.1, price=1.1):
    return {
        "ticket": pos * 10 + entry + 1,
        "order": pos,
        "position_id": pos,
        "time": t,
        "time_msc": t * 1000,
        "type": typ,
        "entry": entry,
        "symbol": symbol,
        "volume": volume,
        "price": price,
        "profit": profit,
        "swap": 0.0,
        "commission": -0.2 if entry == 1 else -0.2,
        "comment": "",
    }


class PropMetricsTests(unittest.TestCase):
    def test_build_payload_uses_explicit_daily_reference_when_available(self):
        account = {
            "balance": 50000.0,
            "equity": 49000.0,
            "profit": -1000.0,
            "currency": "USD",
        }
        payload = build_prop_payload(
            account=account,
            deal_list=[],
            open_positions=[],
            profile=None,
            now=datetime(2026, 8, 4, 13, 0, 0),
            day_start_reference=51000.0,
            day_reference_quality="bridge_exact",
        )
        self.assertEqual(payload["daily_drawdown"]["starting_balance"], 51000.0)
        self.assertEqual(payload["daily_drawdown"]["current_daily_loss"], 2000.0)
        self.assertEqual(payload["daily_drawdown"]["reference_quality"], "bridge_exact")

    def test_profit_target_uses_realized_closed_pnl_not_floating_equity(self):
        t0 = int(datetime(2026, 8, 1, 10, 0).timestamp())
        t1 = int(datetime(2026, 8, 1, 11, 0).timestamp())
        deals = [
            _deal(10, 0, 0, "EURUSD", t0, 0.0, 0.1, 1.1),
            _deal(10, 1, 1, "EURUSD", t1, 500.0, 0.1, 1.11),
        ]
        for d in deals:
            d["commission"] = 0.0
        payload = build_prop_payload(
            account={"balance": 50500.0, "equity": 52500.0, "profit": 2000.0},
            deal_list=deals,
            open_positions=[],
            profile=None,
            now=datetime(2026, 8, 1, 12, 0),
        )
        self.assertEqual(payload["profit_target"]["current_pnl"], 500.0)
        self.assertEqual(payload["profit_target"]["progress_pct"], 20.0)
        self.assertEqual(payload["profit_target"]["status"], "IN_PROGRESS")
        self.assertEqual(payload["live"]["total_pnl"], 2500.0)

    def test_moneta_profitable_day_requires_point_five_percent_closed_profit(self):
        base = datetime(2026, 8, 1, 12, 0)
        deals = []
        for idx, profit in enumerate((10.0, 20.0, 249.99, 250.0), start=1):
            op = int((base + timedelta(days=idx-1)).timestamp())
            cl = op + 3600
            a = _deal(idx, 0, 0, "EURUSD", op, 0.0)
            b = _deal(idx, 1, 1, "EURUSD", cl, profit)
            a["commission"] = b["commission"] = 0.0
            deals.extend([a, b])
        payload = build_prop_payload(
            account={"balance": 50529.99, "equity": 50529.99, "profit": 0.0},
            deal_list=deals,
            open_positions=[],
            profile=None,
            now=datetime(2026, 8, 6, 12, 0),
        )
        self.assertEqual(payload["trading_days"]["counted_days"], 1)
        self.assertEqual(payload["trading_days"]["qualifying_days"], 1)
        self.assertFalse(payload["trading_days"]["target_met"])

    def test_moneta_trading_day_boundary_is_22_utc(self):
        before = int(datetime(2026, 8, 1, 21, 59, tzinfo=timezone.utc).timestamp())
        after = int(datetime(2026, 8, 1, 22, 0, tzinfo=timezone.utc).timestamp())
        self.assertNotEqual(
            day_bucket(before, 0, reset_hour_utc=22),
            day_bucket(after, 0, reset_hour_utc=22),
        )

    def test_countdown_format(self):
        now = datetime(2026, 8, 4, 12, 0, 0)
        c = next_reset_countdown(now, reset_hour=0)
        self.assertEqual(c["hh"], 12)
        self.assertEqual(c["label"], "12:00:00")

    def test_day_bucket_stable(self):
        self.assertEqual(day_bucket(1_700_000_000, 0), day_bucket(1_700_000_000, 0))

    def test_build_payload_challenge_math(self):
        # Day 1: +300 (>= 0.5% of 50k = 250) → qualifying day
        # Day 2: -100
        t1_open = int(datetime(2026, 8, 1, 10, 0).timestamp())
        t1_close = int(datetime(2026, 8, 1, 11, 0).timestamp())
        t2_open = int(datetime(2026, 8, 2, 10, 0).timestamp())
        t2_close = int(datetime(2026, 8, 2, 10, 0, 20).timestamp())  # under 30s

        deals = [
            _deal(1, 0, 0, "EURUSD", t1_open, 0, 0.2, 1.1),
            _deal(1, 1, 1, "EURUSD", t1_close, 300.4, 0.2, 1.11),
            _deal(2, 0, 0, "XAUUSD", t2_open, 0, 0.1, 2300),
            _deal(2, 1, 1, "XAUUSD", t2_close, -99.6, 0.1, 2290),
        ]
        account = {
            "login": 12345678,
            "name": "Test",
            "server": "MonetaFunded-Live",
            "company": "Moneta",
            "currency": "USD",
            "balance": 50200.0,
            "equity": 50150.0,
            "profit": -50.0,
            "margin": 0,
            "margin_free": 50150,
            "margin_level": 0,
            "leverage": 100,
        }
        payload = build_prop_payload(
            account=account,
            deal_list=deals,
            open_positions=[],
            profile=None,
            now=datetime(2026, 8, 4, 13, 0, 0),
            terminal={"id": "moneta_funded", "label": "Funded"},
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["stats"]["total_trades"], 2)
        self.assertEqual(payload["stats"]["under_30s_count"], 1)
        self.assertGreaterEqual(payload["trading_days"]["profitable_days"], 1)
        self.assertGreaterEqual(payload["trading_days"]["days_hitting_05pct"], 1)
        self.assertIn("history_start", payload["trading_days"])
        self.assertEqual(payload["program"]["profit_target_usd"], 2500.0)
        self.assertEqual(payload["daily_drawdown"]["limit_usd"], 2500.0)
        self.assertEqual(payload["overall_drawdown"]["limit_usd"], 5000.0)
        self.assertIn("equity_curve", payload["chart"])
        self.assertEqual(payload["chart"]["lines"]["profit_target_level"], 52500.0)
        self.assertEqual(payload["symbol_stats"][0]["symbol"] in ("EURUSD", "XAUUSD"), True)
        self.assertEqual(payload["live"]["highest_equity"] >= payload["live"]["equity"], True)


class FlaskApiTests(unittest.TestCase):
    def setUp(self):
        # Point app config at a temp terminals.json
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg_path = os.path.join(self.tmp.name, "terminals.json")
        cfg = {
            "active_id": "moneta_funded",
            "prop_profile": {
                "name": "2-Step Program - $50,000 Challenge - Phase I",
                "initial_balance": 50000.0,
                "profit_target_usd": 2500.0,
                "daily_loss_limit_usd": 2500.0,
                "max_loss_limit_usd": 5000.0,
                "min_trading_days": 3,
                "min_day_profit_pct": 0.5,
                "day_reset_hour_server": 0,
                "consistency_cap_pct": 20.0,
            },
            "terminals": [
                {
                    "id": "moneta_funded",
                    "label": "Moneta Funded MT5 Terminal",
                    "path": r"C:\Program Files\Moneta Funded MT5 Terminal\terminal64.exe",
                    "enabled": True,
                },
                {
                    "id": "moneta2",
                    "label": "Moneta2",
                    "path": r"C:\Program Files\Moneta2\terminal64.exe",
                    "enabled": True,
                },
            ],
        }
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)

        import app as dash_app
        self.dash = dash_app
        self._orig = dash_app.TERMINALS_FILE
        dash_app.TERMINALS_FILE = self.cfg_path
        self.client = dash_app.app.test_client()

    def tearDown(self):
        self.dash.TERMINALS_FILE = self._orig
        self.tmp.cleanup()

    def test_list_terminals(self):
        r = self.client.get("/api/terminals")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        ids = [t["id"] for t in body["terminals"]]
        self.assertIn("moneta_funded", ids)
        self.assertIn("moneta2", ids)

    def test_select_terminal(self):
        r = self.client.post("/api/terminals/select", json={"id": "moneta2"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        cfg = json.load(open(self.cfg_path, encoding="utf-8"))
        self.assertEqual(cfg["active_id"], "moneta2")

    def test_add_terminal(self):
        r = self.client.post("/api/terminals/add", json={
            "id": "ic",
            "label": "IC Markets",
            "path": r"C:\Program Files\MetaTrader 5 IC Markets Global\terminal64.exe",
        })
        self.assertEqual(r.status_code, 200)
        ids = [t["id"] for t in r.get_json()["terminals"]]
        self.assertIn("ic", ids)

    @patch("app.load_bridge", return_value=(None, "bridge missing"))
    @patch("app.mt5")
    @patch("app.os.path.isfile", return_value=True)
    def test_api_data_offline_soft_error(self, _isfile, mock_mt5, _bridge):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (-10005, "IPC timeout")
        mock_mt5.terminal_info.return_value = None
        mock_mt5.shutdown.return_value = None
        r = self.client.get("/api/data")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertFalse(body["ok"])
        self.assertIn("error", body)

    @patch("app.load_bridge", return_value=(None, "bridge missing"))
    @patch("app.fetch_positions", return_value=([], None))
    @patch("app.fetch_deals", return_value=([], None))
    @patch("app.ensure_mt5", return_value=True)
    @patch("app.os.path.isfile", return_value=True)
    @patch("app.mt5")
    def test_api_data_live_ok(self, mock_mt5, _isfile, _ens, _deals, _pos, _bridge):
        acc = MagicMock()
        acc.login = 12345678
        acc.name = "Moneta Funded 2-Step Challenge"
        acc.server = "MonetaFunded-Live"
        acc.company = "Moneta"
        acc.currency = "USD"
        acc.balance = 47955.22
        acc.equity = 47896.96
        acc.profit = -58.26
        acc.margin = 100.0
        acc.margin_free = 47700.0
        acc.margin_level = 500.0
        acc.leverage = 100
        mock_mt5.account_info.return_value = acc
        r = self.client.get("/api/data")
        body = r.get_json()
        self.assertTrue(body["ok"], body.get("error"))
        self.assertEqual(body["account"]["login"], 12345678)
        self.assertEqual(body["data_source"], "mt5_api")
        self.assertIn("profit_target", body)
        self.assertIn("daily_reset", body)
        self.assertIn("chart", body)
        self.assertIn("disclaimer", body)

    @patch("app.load_bridge", return_value=(None, "bridge missing"))
    @patch("app.is_terminal_process_running", return_value=False)
    @patch("app.os.path.isfile", return_value=True)
    def test_no_autolaunch_when_process_down(self, _isfile, _running, _bridge):
        r = self.client.get("/api/data")
        body = r.get_json()
        self.assertFalse(body["ok"])
        self.assertIn("not running", body["error"].lower())

    def test_index_contains_pretrade_risk_guard(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        text = r.get_data(as_text=True)
        self.assertIn("Pre-Trade Risk Guard", text)
        self.assertIn("Can I Take This Trade?", text)
        self.assertIn("Advisory only", text)

    @patch("app.get_live_payload")
    def test_api_data_includes_risk_guard(self, mock_live):
        mock_live.return_value = {
            "ok": True,
            "account": {"balance": 50000.0, "equity": 50000.0, "profit": 0.0},
            "program": {
                "profile_id": "moneta_2step_phase1_5_10",
                "provider": "moneta_funded",
                "initial_balance": 50000.0,
            },
            "daily_drawdown": {"starting_balance": 50000.0},
            "open_positions": [],
            "risk_guard": {"available": True, "provider_status": "SAFE"},
        }
        body = self.client.get("/api/data").get_json()
        self.assertIn("risk_guard", body)
        self.assertTrue(body["risk_guard"]["available"])

    def test_profiles_endpoint_lists_moneta_sgb_and_custom(self):
        r = self.client.get("/api/profiles")
        self.assertEqual(r.status_code, 200)
        ids = {p["profile_id"] for p in r.get_json()["profiles"]}
        self.assertTrue({"moneta_2step_phase1_5_10", "sgb_plan_a_phase1", "sgb_plan_b_phase1", "custom"}.issubset(ids))

    @patch("app.mt5")
    @patch("app._mt5_profit_at_close", return_value=-120.0)
    @patch("app.ensure_mt5", return_value=True)
    @patch("app.os.path.isfile", return_value=True)
    @patch("app.get_live_payload")
    def test_risk_preview_is_advisory_and_returns_proposed_risk(self, mock_live, _isfile, _ensure, _profit, mock_mt5):
        mock_live.return_value = {
            "ok": True,
            "account": {"balance": 50000.0, "equity": 50000.0, "profit": 0.0},
            "program": {"profile_id": "legacy_custom"},
            "daily_drawdown": {"starting_balance": 50000.0},
            "open_positions": [],
        }
        acc = MagicMock()
        acc.login = 12345678
        acc.name = "Test"
        acc.server = "Broker-Demo"
        acc.company = "Broker"
        acc.currency = "USD"
        acc.balance = 50000.0
        acc.equity = 50000.0
        acc.profit = 0.0
        acc.margin = 0.0
        acc.margin_free = 50000.0
        acc.margin_level = 0.0
        acc.leverage = 100
        mock_mt5.account_info.return_value = acc
        mock_mt5.positions_get.return_value = []

        r = self.client.post("/api/risk/preview", json={
            "symbol": "XAUUSD",
            "side": "BUY",
            "entry": 2671.40,
            "stop_loss": 2664.20,
            "volume": 0.50,
        })
        self.assertEqual(r.status_code, 200, r.get_json())
        body = r.get_json()
        self.assertTrue(body["advisory_only"])
        self.assertEqual(body["trade"]["risk_usd"], 120.0)
        self.assertIn("risk", body)
        self.assertNotIn("executed", body)

    def test_risk_preview_rejects_bad_trade_input(self):
        r = self.client.post("/api/risk/preview", json={
            "symbol": "XAUUSD", "side": "BUY", "entry": 100, "stop_loss": 101, "volume": 1
        })
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])

    def test_bridge_reader_fresh_file(self):
        from bridge_reader import load_bridge, account_from_bridge, deals_from_bridge
        import time
        payload = {
            "schema": "moneta_bridge_v1",
            "ts": int(time.time()),
            "account": {
                "login": 1, "name": "t", "server": "s", "company": "c",
                "currency": "USD", "balance": 50000, "equity": 50100,
                "profit": 100, "margin": 0, "margin_free": 50100,
                "margin_level": 0, "leverage": 100,
            },
            "deals": [{
                "ticket": 1, "order": 1, "position_id": 9, "time": 1700000000,
                "time_msc": 1700000000000, "type": 0, "entry": 0,
                "symbol": "EURUSD", "volume": 0.1, "price": 1.1,
                "profit": 0, "swap": 0, "commission": 0, "comment": "",
            }],
            "positions": [],
            "quotes": {"EURUSD": {"bid": 1.1, "ask": 1.1001}},
            "deals_count": 1,
        }
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "moneta_bridge.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            with patch("bridge_reader.bridge_path", return_value=path):
                raw, err = load_bridge("moneta_bridge.json", max_age_sec=60)
            self.assertIsNone(err, err)
            self.assertEqual(account_from_bridge(raw)["login"], 1)
            self.assertEqual(len(deals_from_bridge(raw)), 1)


if __name__ == "__main__":
    unittest.main()
