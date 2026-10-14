import io
import json

import xlsxwriter

from odoo import http
from odoo.http import request


class SmartInventoryExport(http.Controller):

    @http.route("/smart_inventory/export/<string:kind>.xlsx", type="http", auth="user")
    def export_xlsx(self, kind, filters="{}", **kwargs):
        if not request.env.user.has_group("smart_inventory.group_smart_inventory_user"):
            return request.not_found()
        try:
            filters = json.loads(filters or "{}")
            if not isinstance(filters, dict):
                raise ValueError(filters)
            title, headers, rows, totals = request.env["smart.inventory.dashboard"]._export_rows(kind, filters)
        except ValueError:
            return request.not_found()
        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
        sheet = workbook.add_worksheet(title[:31])
        bold = workbook.add_format({"bold": True})
        number = workbook.add_format({"num_format": "#,##0.00"})
        sheet.write_row(0, 0, headers, bold)
        for r, row in enumerate(rows, start=1):
            for c, value in enumerate(row):
                sheet.write(r, c, value, number if isinstance(value, float) else None)
        sheet.write_row(len(rows) + 1, 0, totals, bold)
        sheet.set_column(0, 0, 40)
        sheet.set_column(1, len(headers) - 1, 16)
        workbook.close()
        filename = f"{title.replace(' ', '_').lower()}.xlsx"
        return request.make_response(buffer.getvalue(), headers=[
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", http.content_disposition(filename)),
        ])
