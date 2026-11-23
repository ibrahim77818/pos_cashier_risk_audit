# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import format_amount

RULE_SELECTION = [
    ("cash_difference", "Cash Difference"),
    ("refund", "Refunds (% of sales)"),
    ("discount", "Discounts (% of sales)"),
    ("cash_out", "Cash Out"),
]
PERCENT_RULES = ("refund", "discount")

DECISION_SELECTION = [
    ("pending", "Pending Review"),
    ("accepted", "Accepted"),
    ("violation", "Violation"),
]

# Fields a manager may change directly; everything else is written by the
# evaluation engine or the decision workflow only.
_DIRECT_WRITE_FIELDS = {"review_notes"}
_MAIL_FIELD_PREFIXES = ("message_", "activity_", "website_message")


class PosCashierRiskAudit(models.Model):
    _name = "pos.cashier.risk.audit"
    _description = "Cashier Risk Audit"
    _inherit = ["mail.thread"]
    _order = "evaluation_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(string="Reference", required=True, readonly=True,
                       copy=False, default="/")
    session_id = fields.Many2one(
        "pos.session", string="Session", required=True, readonly=True,
        ondelete="restrict", index=True, check_company=True)
    config_id = fields.Many2one(
        "pos.config", string="Point of Sale", required=True, readonly=True,
        index=True, check_company=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True, readonly=True, index=True)
    currency_id = fields.Many2one(
        "res.currency", string="Currency", required=True, readonly=True)
    evaluation_date = fields.Datetime(
        string="Evaluation Date", required=True, readonly=True, index=True,
        default=fields.Datetime.now)
    session_start_at = fields.Datetime(string="Session Opened On", readonly=True)
    session_stop_at = fields.Datetime(string="Session Closed On", readonly=True)
    user_id = fields.Many2one(
        "res.users", string="Session Responsible", readonly=True, index=True)
    employee_id = fields.Many2one(
        "hr.employee", string="Responsible Employee", readonly=True, index=True,
        help="Employee held accountable for this breach: the cashier who "
             "processed most of the refunds/discounts for those rules, otherwise "
             "the session's main cashier.")
    cashier_employee_ids = fields.Many2many(
        "hr.employee", "pos_cashier_risk_audit_employee_rel", "audit_id",
        "employee_id", string="Session Cashiers", readonly=True)

    rule = fields.Selection(RULE_SELECTION, string="Breach Rule", required=True,
                            readonly=True, index=True)
    rule_kind = fields.Selection(
        [("amount", "Amount"), ("percent", "Percentage")], string="Threshold Type",
        compute="_compute_rule_kind", store=True)
    threshold = fields.Float(
        string="Threshold", digits=(16, 2), readonly=True, group_operator="avg",
        help="Configured threshold: an amount for cash rules, a percentage of "
             "sales for refund and discount rules.")
    actual_value = fields.Float(
        string="Actual Value", digits=(16, 2), readonly=True, group_operator="avg",
        help="Measured value compared with the threshold (amount or percentage).")
    variance_pct = fields.Float(
        string="Over Threshold (%)", digits=(16, 2), compute="_compute_variances",
        store=True, group_operator="avg",
        help="How far the actual value exceeds the threshold, relative to the threshold.")
    base_amount = fields.Monetary(
        string="Sales Base", readonly=True,
        help="Sales figure used as the base of percentage rules.")
    breach_amount = fields.Monetary(
        string="Breach Amount", readonly=True,
        help="Cash difference, refunded, discounted or cashed out amount.")
    allowed_amount = fields.Monetary(
        string="Allowed Amount", readonly=True,
        help="Amount the threshold allows (for percentage rules: sales base x threshold).")
    variance_amount = fields.Monetary(
        string="Amount Over Threshold", compute="_compute_variances", store=True)
    expected_cash = fields.Monetary(string="Expected Cash", readonly=True)
    counted_cash = fields.Monetary(string="Counted Cash", readonly=True)
    cash_difference = fields.Monetary(
        string="Cash Difference", readonly=True,
        help="Counted cash minus expected cash at closing.")
    order_count = fields.Integer(string="Session Orders", readonly=True,
                                 group_operator="max")
    zero_sales = fields.Boolean(
        string="No Sales Base", readonly=True,
        help="The session had no sales to compute the percentage against; the "
             "breach is reported at 100%.")
    session_breach_count = fields.Integer(
        string="Rules Breached in Session", compute="_compute_session_breach_count",
        store=True, group_operator="max")
    breach_explanation = fields.Text(
        string="Breach Explanation", compute="_compute_breach_explanation")

    decision = fields.Selection(
        DECISION_SELECTION, string="Admin Decision", required=True, default="pending",
        readonly=True, index=True, tracking=True, copy=False)
    decision_user_id = fields.Many2one(
        "res.users", string="Decided By", readonly=True, tracking=True, copy=False)
    decision_date = fields.Datetime(string="Decided On", readonly=True,
                                    tracking=True, copy=False)
    review_notes = fields.Text(string="Review Notes", tracking=True)
    violation_count = fields.Integer(
        string="Violations", compute="_compute_decision_counters", store=True)
    pending_count = fields.Integer(
        string="Pending", compute="_compute_decision_counters", store=True)

    _sql_constraints = [
        ("session_rule_unique", "unique(session_id, rule)",
         "A cashier risk breach is logged only once per session and rule."),
    ]

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends("rule")
    def _compute_rule_kind(self):
        for audit in self:
            audit.rule_kind = "percent" if audit.rule in PERCENT_RULES else "amount"

    @api.depends("actual_value", "threshold", "breach_amount", "allowed_amount")
    def _compute_variances(self):
        for audit in self:
            audit.variance_amount = audit.breach_amount - audit.allowed_amount
            audit.variance_pct = (
                (audit.actual_value - audit.threshold) / audit.threshold * 100.0
                if audit.threshold else 0.0
            )

    @api.depends("session_id.crm_audit_ids")
    def _compute_session_breach_count(self):
        for audit in self:
            audit.session_breach_count = len(audit.session_id.crm_audit_ids)

    @api.depends("decision")
    def _compute_decision_counters(self):
        for audit in self:
            audit.violation_count = 1 if audit.decision == "violation" else 0
            audit.pending_count = 1 if audit.decision == "pending" else 0

    @api.depends("rule", "threshold", "actual_value", "base_amount", "breach_amount",
                 "allowed_amount", "expected_cash", "counted_cash", "zero_sales")
    def _compute_breach_explanation(self):
        for audit in self:
            if not audit.rule:
                audit.breach_explanation = False
                continue

            def money(value, audit=audit):
                return format_amount(audit.env, value, audit.currency_id)

            if audit.rule == "cash_difference":
                text = _(
                    "Counted cash %(counted)s against expected %(expected)s: a "
                    "difference of %(diff)s exceeds the allowed %(threshold)s.",
                    counted=money(audit.counted_cash),
                    expected=money(audit.expected_cash),
                    diff=money(audit.breach_amount),
                    threshold=money(audit.threshold),
                )
            elif audit.rule == "cash_out":
                text = _(
                    "Cash taken out of the register (%(amount)s) exceeds the "
                    "allowed %(threshold)s by %(excess)s.",
                    amount=money(audit.breach_amount),
                    threshold=money(audit.threshold),
                    excess=money(audit.variance_amount),
                )
            elif audit.zero_sales:
                label = (_("Refunds") if audit.rule == "refund"
                         else _("Discounts"))
                text = _(
                    "%(label)s of %(amount)s were recorded while the session had no "
                    "sales; reported as 100%% against a threshold of %(threshold)s%%.",
                    label=label,
                    amount=money(audit.breach_amount),
                    threshold="%.2f" % audit.threshold,
                )
            else:
                label = (_("Refunds") if audit.rule == "refund"
                         else _("Discounts"))
                text = _(
                    "%(label)s of %(amount)s represent %(actual)s%% of %(base)s, "
                    "above the threshold of %(threshold)s%% (allowed %(allowed)s, "
                    "exceeded by %(excess)s).",
                    label=label,
                    amount=money(audit.breach_amount),
                    actual="%.2f" % audit.actual_value,
                    base=money(audit.base_amount),
                    threshold="%.2f" % audit.threshold,
                    allowed=money(audit.allowed_amount),
                    excess=money(audit.variance_amount),
                )
            audit.breach_explanation = text

    # ------------------------------------------------------------------
    # ORM protections (audit records are immutable evidence)
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                company = self.env["res.company"].browse(vals.get("company_id")) \
                    or self.env.company
                vals["name"] = self.env["ir.sequence"].with_company(company) \
                    .next_by_code("pos.cashier.risk.audit") or "/"
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get("crm_audit_system"):
            protected = {
                fname for fname in vals
                if fname not in _DIRECT_WRITE_FIELDS
                and not fname.startswith(_MAIL_FIELD_PREFIXES)
            }
            if protected:
                raise UserError(_(
                    "Cashier risk audit figures and decisions cannot be edited "
                    "directly. Use the Accept / Log as Violation actions."))
            if "review_notes" in vals:
                self._crm_check_manager()
        return super().write(vals)

    def copy(self, default=None):
        raise UserError(_("Cashier risk audit entries cannot be duplicated."))

    @api.ondelete(at_uninstall=False)
    def _unlink_except_decided(self):
        if any(audit.decision != "pending" for audit in self):
            raise UserError(_(
                "Decided cashier risk audit entries are kept for auditability "
                "and cannot be deleted."))

    # ------------------------------------------------------------------
    # Decision workflow
    # ------------------------------------------------------------------
    def _crm_check_manager(self):
        if self.env.su:
            return
        if not self.env.user.has_group("point_of_sale.group_pos_manager"):
            raise AccessError(_(
                "Only Point of Sale managers can review cashier risk breaches."))

    def _crm_set_decision(self, decision, notes=False):
        if decision not in ("accepted", "violation"):
            raise UserError(_("Invalid cashier risk decision."))
        self._crm_check_manager()
        decided = self.filtered(lambda a: a.decision != "pending")
        if decided:
            raise UserError(_(
                "These breaches were already decided and cannot be decided "
                "again: %s", ", ".join(decided.mapped("name"))))
        vals = {
            "decision": decision,
            "decision_user_id": self.env.user.id,
            "decision_date": fields.Datetime.now(),
        }
        if notes:
            vals["review_notes"] = notes
        self.with_context(crm_audit_system=True).write(vals)
        return True

    def action_accept(self, notes=False):
        return self._crm_set_decision("accepted", notes)

    def action_log_violation(self, notes=False):
        return self._crm_set_decision("violation", notes)

    def action_reset_to_pending(self):
        if not self.env.su and not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators can reopen a decided breach."))
        self.with_context(crm_audit_system=True).write({
            "decision": "pending",
            "decision_user_id": False,
            "decision_date": False,
        })
        return True

    def _crm_open_decision_wizard(self, decision):
        self._crm_check_manager()
        pending = self.filtered(lambda a: a.decision == "pending")
        if not pending:
            raise UserError(_("Select at least one breach pending review."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Accept Breach") if decision == "accepted"
            else _("Log as Violation"),
            "res_model": "pos.cashier.risk.decision.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_audit_ids": [(6, 0, pending.ids)],
                "default_decision": decision,
            },
        }

    def action_open_accept_wizard(self):
        return self._crm_open_decision_wizard("accepted")

    def action_open_violation_wizard(self):
        return self._crm_open_decision_wizard("violation")

    def action_open_session(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "pos.session",
            "res_id": self.session_id.id,
            "view_mode": "form",
        }
