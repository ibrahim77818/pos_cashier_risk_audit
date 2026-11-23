# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PosConfig(models.Model):
    _inherit = "pos.config"

    crm_enabled = fields.Boolean(
        string="Cashier Risk Monitor",
        default=False,
        help="Evaluate the cashier risk thresholds when a session of this "
             "Point of Sale is closed and log every breach for manager review.",
    )
    crm_cash_diff_threshold = fields.Float(
        string="Cash Difference Above",
        digits="Account",
        default=0.0,
        help="Absolute difference between counted and expected closing cash "
             "above which a breach is logged. Requires cash control. "
             "Set to 0 to disable this rule.",
    )
    crm_refund_pct_threshold = fields.Float(
        string="Refunds Above (% of sales)",
        digits=(5, 2),
        default=0.0,
        help="Refunded amount as a percentage of session sales above which a "
             "breach is logged. Set to 0 to disable this rule.",
    )
    crm_discount_pct_threshold = fields.Float(
        string="Discounts Above (% of sales)",
        digits=(5, 2),
        default=0.0,
        help="Discounts granted (line discounts, global discounts and loyalty "
             "rewards) as a percentage of sales before discounts above which a "
             "breach is logged. Set to 0 to disable this rule.",
    )
    crm_cash_out_threshold = fields.Float(
        string="Cash Out Above",
        digits="Account",
        default=0.0,
        help="Total manual cash taken out of the register during the session "
             "above which a breach is logged. Set to 0 to disable this rule.",
    )

    @api.constrains(
        "crm_enabled",
        "crm_cash_diff_threshold",
        "crm_refund_pct_threshold",
        "crm_discount_pct_threshold",
        "crm_cash_out_threshold",
    )
    def _check_crm_thresholds(self):
        for config in self:
            amounts = (config.crm_cash_diff_threshold, config.crm_cash_out_threshold)
            percents = (config.crm_refund_pct_threshold, config.crm_discount_pct_threshold)
            if any(value < 0 for value in amounts + percents):
                raise ValidationError(_(
                    "Cashier Risk Monitor thresholds cannot be negative (%s).",
                    config.name,
                ))
            if any(value > 100 for value in percents):
                raise ValidationError(_(
                    "Refund and discount thresholds are percentages of sales and "
                    "must be between 0 and 100 (%s).",
                    config.name,
                ))
            if config.crm_enabled and not any(amounts + percents):
                raise ValidationError(_(
                    "The Cashier Risk Monitor is enabled on %s but every threshold "
                    "is 0. Set at least one threshold or disable the monitor.",
                    config.name,
                ))
