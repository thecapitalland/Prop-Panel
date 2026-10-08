"""Pure pre-trade risk engine tests."""
import importlib.util
import unittest


PROFILE = {
    "profile_id": "test",
    "provider": "test",
    "initial_balance": 50000.0,
    "daily_loss_limit_usd": 2500.0,
    "max_loss_limit_usd": 5000.0,
}


class RiskEngineTests(unittest.TestCase):
    def _evaluate(self, **kwargs):
        self.assertIsNotNone(importlib.util.find_spec("risk_engine"), "risk_engine module must exist")
        from risk_engine import evaluate_risk
        base = dict(
            account={"balance": 50000.0, "equity": 50000.0, "profit": 0.0},
            profile=PROFILE,
            open_sl_risk_usd=0.0,
            proposed_trade_risk_usd=0.0,
            unbounded_positions=0,
            day_start_reference=50000.0,
        )
        base.update(kwargs)
        return evaluate_risk(**base)

    def test_one_cent_below_daily_breach_is_high_risk_not_breach(self):
        r = self._evaluate(account={"balance": 50000.0, "equity": 47500.01, "profit": -2499.99})
        self.assertEqual(r["provider_status"], "HIGH_RISK")

    def test_exact_daily_breach_is_breach(self):
        r = self._evaluate(account={"balance": 50000.0, "equity": 47500.0, "profit": -2500.0})
        self.assertEqual(r["provider_status"], "BREACH")

    def test_one_cent_beyond_daily_breach_is_breach(self):
        r = self._evaluate(account={"balance": 50000.0, "equity": 47499.99, "profit": -2500.01})
        self.assertEqual(r["provider_status"], "BREACH")

    def test_known_open_and_proposed_risk_are_combined(self):
        r = self._evaluate(open_sl_risk_usd=300.0, proposed_trade_risk_usd=200.0)
        self.assertEqual(r["known_downside_usd"], 500.0)
        self.assertEqual(r["projected_worst_case_equity"], 49500.0)
        self.assertEqual(r["daily"]["projected_loss_usd"], 500.0)

    def test_safe_additional_risk_stops_at_high_risk_threshold(self):
        r = self._evaluate()
        self.assertEqual(r["safe_additional_risk_usd"], 1750.0)
        self.assertEqual(r["remaining_to_breach_usd"], 2500.0)

    def test_personal_stop_can_trigger_before_provider_limit(self):
        r = self._evaluate(
            account={"balance": 50000.0, "equity": 48950.0, "profit": -1050.0},
            personal_policy={"personal_daily_stop_pct": 2.0},
        )
        self.assertEqual(r["provider_status"], "SAFE")
        self.assertEqual(r["personal_status"], "STOP")

    def test_unbounded_position_is_exposure_unknown_not_provider_breach(self):
        r = self._evaluate(unbounded_positions=1)
        self.assertTrue(r["has_unbounded_risk"])
        self.assertEqual(r["provider_status"], "SAFE")
        self.assertEqual(r["exposure_status"], "UNBOUNDED_RISK")
        self.assertEqual(r["recommendation_status"], "CANNOT_ASSERT_SAFE")
        self.assertEqual(r["safe_additional_risk_usd"], 0.0)

    def test_sgb_per_symbol_constraint_can_be_binding(self):
        p = dict(PROFILE)
        p["risk_constraints"] = {
            "aggregate_open_risk": {"enabled": True, "basis": "current_balance", "limit_pct": 3.0},
            "per_symbol_open_risk": {"enabled": True, "basis": "current_balance", "limit_pct": 2.0},
        }
        r = self._evaluate(
            profile=p,
            open_concurrent_risk_usd=700.0,
            open_risk_by_symbol={"XAUUSD": 850.0},
            proposed_symbol="XAUUSD",
            proposed_symbol_risk_usd=200.0,
        )
        self.assertEqual(r["constraints"]["per_symbol"]["status"], "BREACH")
        self.assertEqual(r["provider_status"], "BREACH")
        self.assertEqual(r["binding_constraint"]["code"], "per_symbol_open_risk")
        self.assertEqual(r["safe_additional_risk_usd"], 150.0)

    def test_sgb_aggregate_constraint_is_separate_from_symbol_constraint(self):
        p = dict(PROFILE)
        p["risk_constraints"] = {
            "aggregate_open_risk": {"enabled": True, "basis": "current_balance", "limit_pct": 3.0},
            "per_symbol_open_risk": {"enabled": True, "basis": "current_balance", "limit_pct": 2.0},
        }
        r = self._evaluate(
            profile=p,
            open_concurrent_risk_usd=1450.0,
            open_risk_by_symbol={"XAUUSD": 400.0, "EURUSD": 1050.0},
            proposed_symbol="XAUUSD",
            proposed_symbol_risk_usd=0.0,
        )
        self.assertEqual(r["constraints"]["aggregate"]["status"], "HIGH_RISK")
        self.assertEqual(r["constraints"]["per_symbol"]["status"], "SAFE")

    def test_negative_exposure_is_rejected(self):
        with self.assertRaises(ValueError):
            self._evaluate(open_sl_risk_usd=-1.0)

    def test_zero_limit_is_explicitly_unavailable(self):
        p = dict(PROFILE, daily_loss_limit_usd=0.0)
        r = self._evaluate(profile=p)
        self.assertFalse(r["daily"]["available"])


if __name__ == "__main__":
    unittest.main()
