import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from .common import SmartInventoryCase

REPLY = {"prediction": 30, "risk": "medium", "insight": "Demand is rising."}


def fake_response(text=None, stop_reason="end_turn"):
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(stop_reason=stop_reason, content=content)


@tagged("post_install", "-at_install")
class TestAiProvider(SmartInventoryCase):

    def setUp(self):
        super().setUp()
        self.set_param("ai_enabled", "True")
        self.set_param("ai_daily_limit", "100")
        self.env["ir.config_parameter"].sudo().set_param("smart_inventory.ai_calls", "")
        self.Template = type(self.env["product.template"])
        self.client = MagicMock()
        self.client.beta.messages.create.return_value = fake_response(json.dumps(REPLY))
        self.client.messages.create.return_value = fake_response(json.dumps(REPLY))

    def patch_client(self):
        return patch.object(self.Template, "_si_ai_client", autospec=True, return_value=self.client)

    def test_request_uses_structured_output_and_fallback(self):
        product = self.make_product("SI Claude")
        with self.patch_client():
            product.action_generate_ai_insights()
        kwargs = self.client.beta.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-opus-5-5")
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertEqual(kwargs["output_config"]["format"]["type"], "json_schema")
        self.assertIn("SI Claude", kwargs["messages"][0]["content"])
        self.assertEqual(product.forecast_source, "ai")
        self.assertEqual(product.risk_level, "medium")
        self.assertEqual(product.ai_insight, "Demand is rising.")

    def test_model_without_fallback_uses_plain_endpoint(self):
        self.set_param("ai_model", "claude-haiku-5-5")
        product = self.make_product("SI Haiku")
        with self.patch_client():
            product.action_generate_ai_insights()
        self.assertFalse(self.client.beta.messages.create.called)
        self.assertEqual(self.client.messages.create.call_args.kwargs["model"], "claude-haiku-5-5")
        self.assertEqual(product.forecast_source, "ai")

    def test_refusal_keeps_baseline(self):
        self.client.beta.messages.create.return_value = fake_response(stop_reason="refusal")
        product = self.make_product("SI Refused")
        with self.patch_client():
            product.action_generate_ai_insights()
        self.assertEqual(product.forecast_source, "baseline")

    def test_api_error_keeps_baseline(self):
        self.client.beta.messages.create.side_effect = ConnectionError("down")
        product = self.make_product("SI Down")
        with self.patch_client():
            product.action_generate_ai_insights()
        self.assertEqual(product.forecast_source, "baseline")

    def test_no_package_keeps_baseline(self):
        product = self.make_product("SI No Package")
        with patch.object(self.Template, "_si_ai_client", autospec=True, return_value=None):
            product.action_generate_ai_insights()
        self.assertEqual(product.forecast_source, "baseline")

    def test_ai_adjustment_is_capped(self):
        self.set_param("ai_max_adjust_pct", "50")
        product = self.make_product("SI Capped")
        self.client.beta.messages.create.return_value = fake_response(
            json.dumps({"prediction": 1000, "risk": "high", "insight": "Spike."}))
        with self.patch_daily_sales(2), self.patch_client():
            product.action_generate_ai_insights()
        baseline = product.forecast_ids[:1].baseline
        self.assertGreater(baseline, 0)
        self.assertAlmostEqual(product.predicted_demand, baseline * 1.5, places=4)

    def test_clamp_rules(self):
        clamp = self.env["product.template"]._si_clamp_ai_prediction
        self.set_param("ai_max_adjust_pct", "30")
        self.assertAlmostEqual(clamp(200.0, 100.0), 130.0)
        self.assertAlmostEqual(clamp(10.0, 100.0), 70.0)
        self.assertAlmostEqual(clamp(90.0, 100.0), 90.0)
        self.assertAlmostEqual(clamp(25.0, 0.0), 25.0)
        self.set_param("ai_max_adjust_pct", "0")
        self.assertAlmostEqual(clamp(500.0, 100.0), 500.0)
