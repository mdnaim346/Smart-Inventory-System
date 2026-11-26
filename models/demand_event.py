from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class SmartInventoryDemandEvent(models.Model):
    """A season or promotion that multiplies demand on its days (Eid, Ramadan, a sale)."""
    _name = "smart.inventory.demand.event"
    _description = "Smart Inventory Demand Event"
    _order = "date_from desc, id desc"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    date_from = fields.Date(string="From", required=True)
    date_to = fields.Date(string="To", required=True)
    factor = fields.Float(
        string="Demand Factor", default=1.5, required=True, digits=(16, 2),
        help="Expected demand compared with a normal day: 1.5 = 50% more, 0.8 = 20% less.")
    product_tmpl_ids = fields.Many2many("product.template", "smart_inventory_event_product_rel",
                                        "event_id", "product_tmpl_id", string="Products",
                                        help="Empty: all products (or all products of the categories).")
    categ_ids = fields.Many2many("product.category", "smart_inventory_event_categ_rel",
                                 "event_id", "categ_id", string="Categories")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    note = fields.Text()

    @api.constrains("date_from", "date_to", "factor")
    def _check_values(self):
        for rec in self:
            if rec.date_to < rec.date_from:
                raise ValidationError(_("The event must end on or after its start date."))
            if rec.factor <= 0:
                raise ValidationError(_("The demand factor must be positive."))

    def _applies_to(self, template):
        self.ensure_one()
        if self.company_id and template.company_id and template.company_id != self.company_id:
            return False
        if self.product_tmpl_ids and template in self.product_tmpl_ids:
            return True
        if self.categ_ids:
            return bool(template.categ_id) and bool(
                self.categ_ids.filtered(lambda c: template.categ_id.parent_path.startswith(c.parent_path)))
        return not self.product_tmpl_ids

    @api.model
    def _events_between(self, date_from, date_to):
        return self.search([("date_from", "<=", date_to), ("date_to", ">=", date_from)])

    @api.model
    def _day_factors(self, template, date_from, date_to, events=None):
        """{date: factor} for `template` between the two dates; overlapping events multiply."""
        events = self._events_between(date_from, date_to) if events is None else events
        factors = {}
        for event in events.filtered(lambda e: e._applies_to(template)):
            day = max(event.date_from, date_from)
            while day <= min(event.date_to, date_to):
                factors[day] = factors.get(day, 1.0) * event.factor
                day += timedelta(days=1)
        return factors
