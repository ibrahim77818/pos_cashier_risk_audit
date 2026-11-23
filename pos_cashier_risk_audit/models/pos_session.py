# -*- coding: utf-8 -*-
import logging
from collections import defaultdict

from odoo import _, fields, models
from odoo.tools import float_compare, float_round

_logger = logging.getLogger(__name__)

VALID_ORDER_STATES = ("paid", "done", "invoiced")


class PosSession(models.Model):
    _inherit = "pos.session"

    crm_audit_ids = fields.One2many(
        "pos.cashier.risk.audit", "session_id", string="Cashier Risk Breaches",
        groups="point_of_sale.group_pos_manager")
    crm_audit_count = fields.Integer(
        string="Risk Breaches", compute="_compute_crm_audit_count",
        groups="point_of_sale.group_pos_manager")

    def _compute_crm_audit_count(self):
        counts = {
            session.id: count
            for session, count in self.env["pos.cashier.risk.audit"]._read_group(
                [("session_id", "in", self.ids)], ["session_id"], ["__count"])
        }
        for session in self:
            session.crm_audit_count = counts.get(session.id, 0)

    def action_view_crm_audits(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "pos_cashier_risk_audit.action_pos_cashier_risk_audit")
        action["domain"] = [("session_id", "=", self.id)]
        action["context"] = {"create": False}
        return action

    def try_cash_in_out(self, *args, **kwargs):
        # Tag the statement lines created by the POS Cash In/Out button.
        return super(PosSession, self.with_context(crm_pos_cash_move=True)) \
            .try_cash_in_out(*args, **kwargs)

    # ------------------------------------------------------------------
    # Closing hook
    # ------------------------------------------------------------------
    def _validate_session(self, *args, **kwargs):
        # Metrics are captured *before* the core closes the session: closing
        # posts the cash difference as a statement line, which would hide the
        # difference and pollute the cash-out total afterwards.
        snapshots = {}
        for session in self:
            if session.config_id.crm_enabled and session.state != "closed":
                try:
                    with self.env.cr.savepoint():
                        snapshots[session.id] = session._crm_compute_metrics()
                except Exception:  # never block a cash closing on the audit
                    _logger.exception(
                        "Cashier Risk Monitor: metrics failed for session %s", session.name)
        res = super()._validate_session(*args, **kwargs)
        for session in self:
            if session.id in snapshots and session.state == "closed":
                session._crm_safe_log(snapshots[session.id])
        return res

    def _crm_safe_log(self, metrics):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                audits = self._crm_log_breaches(metrics)
        except Exception:
            _logger.exception(
                "Cashier Risk Monitor: logging failed for session %s", self.name)
            self.sudo().message_post(body=_(
                "Cashier Risk Monitor could not evaluate this session. "
                "Please review it manually."))
            return
        if audits:
            labels = dict(audits._fields["rule"]._description_selection(self.env))
            self.sudo().message_post(body=_(
                "Cashier Risk Monitor: %(count)s threshold(s) breached (%(rules)s). "
                "Pending manager review.",
                count=len(audits),
                rules=", ".join(labels[a.rule] for a in audits),
            ))

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------
    def _crm_employee_of_user(self, user, cache):
        if user.id not in cache:
            cache[user.id] = (user.with_company(self.company_id).employee_id
                              if user else self.env["hr.employee"].sudo())
        return cache[user.id]

    def _crm_is_discount_line(self):
        """Return a predicate detecting global-discount / loyalty reward lines
        (pos_discount / pos_loyalty are optional)."""
        config = self.config_id
        discount_product = (config.discount_product_id
                            if "discount_product_id" in config._fields else False)
        has_reward_flag = "is_reward_line" in self.env["pos.order.line"]._fields

        def predicate(line):
            if discount_product and line.product_id == discount_product:
                return True
            return bool(has_reward_flag and line.is_reward_line)
        return predicate

    @staticmethod
    def _crm_undiscounted_total(line):
        taxes = line.tax_ids_after_fiscal_position
        if taxes:
            return taxes.compute_all(
                line.price_unit, line.order_id.currency_id, line.qty,
                product=line.product_id, partner=line.order_id.partner_id,
            )["total_included"]
        return line.price_unit * line.qty

    def _crm_compute_metrics(self):
        """Measure the session. All sales/refund/discount figures are tax
        included, in the session currency.

        * sales_amount: net sale lines (qty > 0, after line discounts).
        * gross_sales_amount: sales before any discount (base of discount %).
        * refund_amount: refunded lines (qty < 0).
        * discount_amount: line discounts + global discount/reward lines.
        * cash_out_amount: POS "Cash In/Out" cash outs only.
        """
        self.ensure_one()
        session = self.sudo()
        currency = session.currency_id
        employees = self.env["hr.employee"].sudo()
        has_order_employee = "employee_id" in self.env["pos.order"]._fields
        is_discount_line = session._crm_is_discount_line()
        user_cache = {}
        stats = defaultdict(lambda: {"orders": 0, "sales": 0.0, "refund": 0.0, "discount": 0.0})
        sales = refund = line_discount = global_discount = 0.0

        orders = session.get_session_orders().filtered(lambda o: o.state in VALID_ORDER_STATES)
        for order in orders:
            employee = order.employee_id if has_order_employee else employees
            if not employee:
                employee = session._crm_employee_of_user(
                    order.user_id or session.user_id, user_cache)
            stat = stats[employee.id]
            stat["orders"] += 1
            for line in order.lines:
                subtotal = line.price_subtotal_incl
                if is_discount_line(line):
                    if subtotal < 0:
                        global_discount -= subtotal
                        stat["discount"] -= subtotal
                    continue
                if line.qty < 0:
                    refund += abs(subtotal)
                    stat["refund"] += abs(subtotal)
                    continue
                if not line.qty:
                    continue
                sales += subtotal
                stat["sales"] += subtotal
                if line.discount:
                    granted = max(session._crm_undiscounted_total(line) - subtotal, 0.0)
                    line_discount += granted
                    stat["discount"] += granted

        # Cash figures (only meaningful with cash control)
        cash_control = bool(session.config_id.cash_control and session.cash_journal_id)
        # Only lines created by the POS "Cash In/Out" button count: opening /
        # closing counting differences and the closing cash entry are excluded.
        cash_out = -sum(
            st_line.amount for st_line in session.statement_line_ids
            if st_line.crm_is_pos_cash_move and st_line.amount < 0
        )

        # Attribution
        session_employee = session._crm_employee_of_user(session.user_id, user_cache)
        actors = {emp_id: s for emp_id, s in stats.items() if emp_id}

        def top(key, fallback):
            ranked = sorted(actors.items(),
                            key=lambda kv: (kv[1][key], kv[1]["sales"]), reverse=True)
            if ranked and ranked[0][1][key] > 0:
                return ranked[0][0]
            return fallback

        primary = top("orders", session_employee.id or False)

        return {
            "order_count": len(orders),
            "sales_amount": currency.round(sales),
            "gross_sales_amount": currency.round(sales + line_discount),
            "refund_amount": currency.round(refund),
            "discount_amount": currency.round(line_discount + global_discount),
            "cash_control": cash_control,
            "expected_cash": currency.round(session.cash_register_balance_end) if cash_control else 0.0,
            "counted_cash": currency.round(session.cash_register_balance_end_real) if cash_control else 0.0,
            "cash_difference": currency.round(session.cash_register_difference) if cash_control else 0.0,
            "cash_out_amount": currency.round(cash_out),
            "primary_employee_id": primary,
            "refund_employee_id": top("refund", primary),
            "discount_employee_id": top("discount", primary),
            "cashier_employee_ids": sorted(actors) or ([primary] if primary else []),
        }

    # ------------------------------------------------------------------
    # Rule engine
    # ------------------------------------------------------------------
    def _crm_evaluate_breaches(self, metrics):
        """Pure evaluation: return one dict per breached rule."""
        self.ensure_one()
        config = self.config_id
        currency = self.currency_id
        breaches = []

        threshold = config.crm_cash_diff_threshold
        if threshold > 0 and metrics["cash_control"]:
            diff = abs(metrics["cash_difference"])
            if currency.compare_amounts(diff, threshold) > 0:
                breaches.append({
                    "rule": "cash_difference", "threshold": threshold,
                    "actual_value": diff, "breach_amount": diff,
                    "allowed_amount": threshold, "base_amount": 0.0,
                    "zero_sales": False,
                    "employee_id": metrics["primary_employee_id"],
                })

        for rule, threshold, amount, base, employee in (
            ("refund", config.crm_refund_pct_threshold, metrics["refund_amount"],
             metrics["sales_amount"], metrics["refund_employee_id"]),
            ("discount", config.crm_discount_pct_threshold, metrics["discount_amount"],
             metrics["gross_sales_amount"], metrics["discount_employee_id"]),
        ):
            if threshold <= 0 or currency.is_zero(amount) or amount < 0:
                continue
            vals = {"rule": rule, "threshold": threshold, "breach_amount": amount,
                    "base_amount": max(base, 0.0), "employee_id": employee}
            if currency.compare_amounts(base, 0.0) <= 0:
                # Zero-sales edge case: no division, always a breach.
                vals.update(actual_value=100.0, allowed_amount=0.0, zero_sales=True)
                breaches.append(vals)
                continue
            percentage = amount / base * 100.0
            if float_compare(percentage, threshold, precision_digits=2) > 0:
                vals.update(
                    actual_value=float_round(percentage, precision_digits=2),
                    allowed_amount=currency.round(base * threshold / 100.0),
                    zero_sales=False,
                )
                breaches.append(vals)

        threshold = config.crm_cash_out_threshold
        cash_out = metrics["cash_out_amount"]
        if threshold > 0 and currency.compare_amounts(cash_out, threshold) > 0:
            breaches.append({
                "rule": "cash_out", "threshold": threshold,
                "actual_value": cash_out, "breach_amount": cash_out,
                "allowed_amount": threshold, "base_amount": 0.0,
                "zero_sales": False,
                "employee_id": metrics["primary_employee_id"],
            })
        return breaches

    def _crm_log_breaches(self, metrics=None):
        """Create (or refresh, while still pending) one audit entry per
        breached rule. Idempotent: re-evaluating never duplicates entries and
        never alters a decided entry."""
        self.ensure_one()
        Audit = self.env["pos.cashier.risk.audit"].sudo()
        if not self.config_id.crm_enabled:
            return Audit
        if metrics is None:
            metrics = self._crm_compute_metrics()
        breaches = self._crm_evaluate_breaches(metrics)
        if not breaches:
            return Audit

        session = self.sudo()
        common = {
            "session_id": session.id,
            "config_id": session.config_id.id,
            "company_id": session.company_id.id,
            "currency_id": session.currency_id.id,
            "evaluation_date": fields.Datetime.now(),
            "session_start_at": session.start_at,
            "session_stop_at": session.stop_at or fields.Datetime.now(),
            "user_id": session.user_id.id,
            "cashier_employee_ids": [(6, 0, metrics["cashier_employee_ids"])],
            "expected_cash": metrics["expected_cash"],
            "counted_cash": metrics["counted_cash"],
            "cash_difference": metrics["cash_difference"],
            "order_count": metrics["order_count"],
        }
        existing = {a.rule: a for a in Audit.search([("session_id", "=", session.id)])}
        result = Audit
        to_create = []
        for breach in breaches:
            vals = dict(common, **breach)
            audit = existing.get(breach["rule"])
            if audit:
                if audit.decision == "pending":
                    vals.pop("session_id")
                    vals.pop("rule")
                    audit.with_context(crm_audit_system=True).write(vals)
                result |= audit
            else:
                to_create.append(vals)
        if to_create:
            result |= Audit.with_context(crm_audit_system=True).create(to_create)
        return result
