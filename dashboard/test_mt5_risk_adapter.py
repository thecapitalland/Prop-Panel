"""MT5 risk exposure adapter tests."""
import importlib.util
import unittest


def fake_profit(symbol, side, volume, price_open, price_close):
    direction = 1.0 if side == "BUY" else -1.0
    return (price_close - price_open) * direction * volume * 100.0


class RiskAdapterTests(unittest.TestCase):
    def _module(self):
        self.assertIsNotNone(importlib.util.find_spec("mt5_risk_adapter"), "mt5_risk_adapter module must exist")
        import mt5_risk_adapter
        return mt5_risk_adapter

    def test_buy_position_uses_current_price_to_stop_for_remaining_downside(self):
        p = {"symbol": "XAUUSD", "type": "BUY", "volume": 0.5, "price_current": 100.0, "sl": 98.0}
        r = self._module().position_sl_exposure(p, fake_profit)
        self.assertEqual(r["risk_usd"], 100.0)
        self.assertFalse(r["unbounded"])

    def test_sell_position_exposure(self):
        p = {"symbol": "EURUSD", "type": "SELL", "volume": 1.0, "price_current": 100.0, "sl": 102.0}
        r = self._module().position_sl_exposure(p, fake_profit)
        self.assertEqual(r["risk_usd"], 200.0)

    def test_missing_stop_is_unbounded_not_zero_risk(self):
        p = {"symbol": "XAUUSD", "type": "BUY", "volume": 0.5, "price_current": 100.0, "sl": 0.0}
        r = self._module().position_sl_exposure(p, fake_profit)
        self.assertTrue(r["unbounded"])
        self.assertIsNone(r["risk_usd"])

    def test_aggregate_multiple_positions(self):
        positions = [
            {"symbol": "A", "type": "BUY", "volume": 1.0, "price_current": 10.0, "sl": 9.0},
            {"symbol": "B", "type": "SELL", "volume": 2.0, "price_current": 10.0, "sl": 11.0},
        ]
        r = self._module().aggregate_open_sl_exposure(positions, fake_profit)
        self.assertEqual(r["known_risk_usd"], 300.0)
        self.assertEqual(r["unbounded_positions"], 0)

    def test_aggregate_groups_entry_to_stop_risk_by_normalized_symbol(self):
        positions = [
            {"symbol": "XAUUSD.X", "type": "BUY", "volume": 1.0, "price_open": 100.0, "price_current": 99.0, "sl": 98.0, "profit": -100.0},
            {"symbol": "XAUUSD", "type": "BUY", "volume": 1.0, "price_open": 100.0, "price_current": 99.5, "sl": 99.0, "profit": -50.0},
        ]
        r = self._module().aggregate_open_sl_exposure(
            positions,
            fake_profit,
            symbol_normalizer=lambda s: s[:-2] if s.endswith(".X") else s,
        )
        self.assertEqual(r["entry_risk_usd"], 300.0)
        self.assertEqual(r["risk_by_symbol"]["XAUUSD"], 300.0)
        self.assertEqual(r["current_loss_by_symbol"]["XAUUSD"], 150.0)

    def test_max_volume_for_risk_floors_to_broker_step(self):
        m = self._module()
        self.assertEqual(
            m.max_volume_for_risk(
                risk_budget_usd=275.0,
                risk_per_lot_usd=1000.0,
                volume_min=0.01,
                volume_max=10.0,
                volume_step=0.01,
            ),
            0.27,
        )
        self.assertEqual(
            m.max_volume_for_risk(
                risk_budget_usd=5.0,
                risk_per_lot_usd=1000.0,
                volume_min=0.01,
                volume_max=10.0,
                volume_step=0.01,
            ),
            0.0,
        )

    def test_proposed_trade_requires_positive_volume_and_stop(self):
        m = self._module()
        with self.assertRaises(ValueError):
            m.proposed_trade_exposure({"symbol": "XAUUSD", "side": "BUY", "entry": 100, "stop_loss": 99, "volume": 0}, fake_profit)
        with self.assertRaises(ValueError):
            m.proposed_trade_exposure({"symbol": "XAUUSD", "side": "BUY", "entry": 100, "stop_loss": 0, "volume": 1}, fake_profit)

    def test_proposed_trade_rejects_wrong_stop_side(self):
        m = self._module()
        with self.assertRaises(ValueError):
            m.proposed_trade_exposure({"symbol": "XAUUSD", "side": "BUY", "entry": 100, "stop_loss": 101, "volume": 1}, fake_profit)

    def test_calculator_failure_is_explicit(self):
        def broken(*_args):
            raise RuntimeError("symbol unavailable")
        p = {"symbol": "XAUUSD", "type": "BUY", "volume": 1.0, "price_current": 100.0, "sl": 99.0}
        with self.assertRaises(RuntimeError):
            self._module().position_sl_exposure(p, broken)


if __name__ == "__main__":
    unittest.main()
