# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PosCashierRiskDecisionWizard(models.TransientModel):
    _name = "pos.cashier.risk.decision.wizard"
    _description = "Cashier Risk Decision"

    audit_ids = fields.Many2many(
        "pos.cashier.risk.audit", string="Breaches", required=True)
    decision = fields.Selection(
        [("accepted", "Accept the threshold break"),
         ("violation", "Log it as a violation")],
        string="Decision", required=True, default="accepted")
    review_notes = fields.Text(string="Review Notes")
    audit_count = fields.Integer(string="Breaches Selected",
                                 compute="_compute_audit_count")

    @api.depends("audit_ids")
    def _compute_audit_count(self):
        for wizard in self:
            wizard.audit_count = len(wizard.audit_ids)

    def action_apply(self):
        self.ensure_one()
        if not self.audit_ids:
            raise UserError(_("Select at least one breach pending review."))
        if self.decision == "violation":
            self.audit_ids.action_log_violation(self.review_notes)
        else:
            self.audit_ids.action_accept(self.review_notes)
        return {"type": "ir.actions.act_window_close"}
