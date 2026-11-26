import logging

import requests

from odoo import api, models, _

_logger = logging.getLogger(__name__)

ALERT_TIMEOUT_SECONDS = 10
MAX_PRODUCTS_PER_ALERT = 10


class SmartInventoryAlert(models.AbstractModel):
    """Short text alerts to Telegram and/or a webhook (WhatsApp or SMS gateway)."""
    _name = "smart.inventory.alert"
    _description = "Smart Inventory Chat Alerts"

    @api.model
    def _channels(self):
        config = self.env["smart.inventory.config"]
        channels = []
        token, chat_id = config.get("telegram_token"), config.get("telegram_chat_id")
        if token and chat_id:
            channels.append(("telegram", f"https://api.telegram.org/bot{token}/sendMessage",
                             {"chat_id": chat_id, "text": None}))
        url = config.get("alert_webhook_url")
        if url:
            channels.append(("webhook", url, {"text": None, "source": "smart_inventory"}))
        return channels

    @api.model
    def _send(self, text):
        """Send `text` to every configured channel. True if at least one accepted it."""
        sent = False
        for name, url, payload in self._channels():
            try:
                response = requests.post(url, json=dict(payload, text=text), timeout=ALERT_TIMEOUT_SECONDS)
                response.raise_for_status()
                sent = True
            except requests.RequestException as error:
                # The URL may contain the bot token: log the channel name only.
                _logger.warning("Smart Inventory: %s alert failed: %s", name, type(error).__name__)
        return sent

    @api.model
    def _send_high_risk(self, templates):
        if not templates or not self._channels():
            return False
        lines = [_("Stock alert: %s product(s) will likely run out soon.", len(templates))]
        for tmpl in templates[:MAX_PRODUCTS_PER_ALERT]:
            if tmpl.avg_daily_demand > 0:
                lines.append(_("- %(name)s: %(stock).0f left, about %(days).0f days",
                               name=tmpl.display_name, stock=tmpl.usable_qty, days=tmpl.days_of_cover))
            else:
                lines.append(_("- %(name)s: %(stock).0f left", name=tmpl.display_name, stock=tmpl.usable_qty))
        if len(templates) > MAX_PRODUCTS_PER_ALERT:
            lines.append(_("...and %s more.", len(templates) - MAX_PRODUCTS_PER_ALERT))
        return self._send("\n".join(lines))

    @api.model
    def _send_digest(self, company, products, orders, suggestions):
        if not self._channels():
            return False
        text = _("%(company)s daily stock summary: %(risks)s high-risk products, %(pos)s restock orders "
                 "and %(sugg)s suggestions waiting.", company=company.name, risks=len(products),
                 pos=len(orders), sugg=suggestions)
        return self._send(text)
