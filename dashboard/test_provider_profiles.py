"""Provider profile registry tests."""
import importlib.util
import unittest


class ProviderProfileTests(unittest.TestCase):
    def _module(self):
        self.assertIsNotNone(
            importlib.util.find_spec("provider_profiles"),
            "provider_profiles module must exist",
        )
        import provider_profiles
        return provider_profiles

    def test_moneta_compatibility_profile(self):
        p = self._module().get_profile("moneta_2step_phase1_5_10")
        self.assertEqual(p["provider"], "moneta_funded")
        self.assertEqual(p["daily_loss_limit_pct"], 5.0)
        self.assertEqual(p["max_loss_limit_pct"], 10.0)
        self.assertEqual(p["profit_target_pct"], 5.0)
        self.assertEqual(p["daily_loss"]["reference"], "max_balance_equity_at_reset")
        self.assertTrue(p["daily_loss"]["includes_floating"])
        self.assertEqual(p["daily_loss"]["reset_hour_utc"], 22)

    def test_moneta_lower_drawdown_addon_profile_is_distinct(self):
        p = self._module().get_profile("moneta_2step_phase1_4_8")
        self.assertEqual(p["daily_loss_limit_pct"], 4.0)
        self.assertEqual(p["max_loss_limit_pct"], 8.0)

    def test_sgb_profiles_are_addressable(self):
        m = self._module()
        a = m.get_profile("sgb_plan_a_phase1")
        b = m.get_profile("sgb_plan_b_phase1")
        self.assertEqual(a["provider"], "sgb")
        self.assertEqual(a["profit_target_pct"], 8.0)
        self.assertEqual(b["profit_target_pct"], 10.0)
        self.assertEqual(a["daily_loss_limit_pct"], 5.0)
        self.assertEqual(a["max_loss_limit_pct"], 12.0)

    def test_profile_override_recalculates_usd_limits_for_account_size(self):
        p = self._module().normalize_profile(
            profile={"initial_balance": 100000.0},
            profile_id="moneta_2step_phase1_5_10",
        )
        self.assertEqual(p["profit_target_usd"], 5000.0)
        self.assertEqual(p["daily_loss_limit_usd"], 5000.0)
        self.assertEqual(p["max_loss_limit_usd"], 10000.0)

    def test_custom_profile_exists(self):
        p = self._module().get_profile("custom")
        self.assertEqual(p["provider"], "custom")

    def test_unknown_profile_fails_closed(self):
        with self.assertRaises(ValueError):
            self._module().get_profile("not-a-real-profile")

    def test_legacy_flat_profile_normalizes_without_changing_limits(self):
        legacy = {
            "name": "Legacy",
            "initial_balance": 50000.0,
            "profit_target_usd": 2500.0,
            "daily_loss_limit_usd": 2500.0,
            "max_loss_limit_usd": 5000.0,
            "min_trading_days": 3,
            "min_day_profit_pct": 0.5,
            "day_reset_hour_server": 0,
            "consistency_cap_pct": 20.0,
        }
        p = self._module().normalize_profile(profile=legacy)
        self.assertEqual(p["daily_loss_limit_usd"], 2500.0)
        self.assertEqual(p["max_loss_limit_usd"], 5000.0)
        self.assertEqual(p["profit_target_usd"], 2500.0)
        self.assertEqual(p["profile_id"], "legacy_custom")


if __name__ == "__main__":
    unittest.main()
