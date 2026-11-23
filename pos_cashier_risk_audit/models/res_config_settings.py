# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # The POS settings framework maps every ``pos_<name>`` field onto
    # ``pos.config.<name>`` of the selected Point of Sale.
    pos_crm_enabled = fields.Boolean(
        related="pos_config_id.crm_enabled", readonly=False)
    pos_crm_cash_diff_threshold = fields.Float(
        related="pos_config_id.crm_cash_diff_threshold", readonly=False)
    pos_crm_refund_pct_threshold = fields.Float(
        related="pos_config_id.crm_refund_pct_threshold", readonly=False)
    pos_crm_discount_pct_threshold = fields.Float(
        related="pos_config_id.crm_discount_pct_threshold", readonly=False)
    pos_crm_cash_out_threshold = fields.Float(
        related="pos_config_id.crm_cash_out_threshold", readonly=False)
