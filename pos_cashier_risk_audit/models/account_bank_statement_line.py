# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    crm_is_pos_cash_move = fields.Boolean(
        string="POS Cash In/Out", readonly=True, copy=False, index=True,
        help="Set on statement lines created by the POS Cash In/Out button; "
             "used by the Cashier Risk Monitor to total cash outs.")

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("crm_pos_cash_move"):
            for vals in vals_list:
                if vals.get("pos_session_id"):
                    vals.setdefault("crm_is_pos_cash_move", True)
        return super().create(vals_list)
