# -*- coding: utf-8 -*-
from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged("post_install", "-at_install")
class TestCashierRiskAudit(TestPoSCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.company = cls.config.company_id
        cls.Audit = cls.env["pos.cashier.risk.audit"]

        cls.product = cls.env["product.product"].sudo().create({
            "name": "Risk Item",
            "type": "consu",
            "available_in_pos": True,
            "list_price": 100.0,
            "taxes_id": [(5, 0, 0)],
        }).sudo(False)

        Users = cls.env["res.users"].with_context(no_reset_password=True).sudo()
        base_group = cls.env.ref("base.group_user")
        cls.manager = Users.create({
            "name": "Risk Manager",
            "login": "crm_risk_manager",
            "company_id": cls.company.id,
            "company_ids": [(6, 0, cls.company.ids)],
            "groups_id": [(6, 0, [base_group.id,
                                  cls.env.ref("point_of_sale.group_pos_manager").id])],
        }).sudo(False)
        cls.cashier_user = Users.create({
            "name": "Risk Cashier User",
            "login": "crm_risk_cashier",
            "company_id": cls.company.id,
            "company_ids": [(6, 0, cls.company.ids)],
            "groups_id": [(6, 0, [base_group.id,
                                  cls.env.ref("point_of_sale.group_pos_user").id])],
        }).sudo(False)

        # Employee of the user running the sessions (fallback attribution).
        session_user = cls.env.user
        cls.employee = session_user.sudo().with_company(cls.company).employee_id.sudo(False)
        if not cls.employee:
            cls.employee = cls.env["hr.employee"].sudo().create({
                "name": "Risk Cashier",
                "user_id": session_user.id,
                "company_id": cls.company.id,
            }).sudo(False)
            cls.env.invalidate_all()

    def setUp(self):
        super().setUp()
        self.config = self.basic_config

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _enable(self, **thresholds):
        vals = {
            "crm_enabled": True,
            "crm_cash_diff_threshold": 0.0,
            "crm_refund_pct_threshold": 0.0,
            "crm_discount_pct_threshold": 0.0,
            "crm_cash_out_threshold": 0.0,
        }
        vals.update(thresholds)
        self.config.sudo().write(vals)

    def _make_order(self, session, lines):
        """lines: iterable of (qty, price_unit, discount %), tax free."""
        line_cmds, total = [], 0.0
        for qty, price, discount in lines:
            subtotal = qty * price * (1 - discount / 100.0)
            total += subtotal
            line_cmds.append((0, 0, {
                "product_id": self.product.id,
                "full_product_name": self.product.display_name,
                "qty": qty,
                "price_unit": price,
                "discount": discount,
                "price_subtotal": subtotal,
                "price_subtotal_incl": subtotal,
                "tax_ids": [(6, 0, [])],
            }))
        return self.env["pos.order"].sudo().create({
            "session_id": session.id,
            "pricelist_id": session.config_id.pricelist_id.id,
            "lines": line_cmds,
            "amount_tax": 0.0,
            "amount_total": total,
            "amount_paid": total,
            "amount_return": 0.0,
            "state": "paid",
        })

    @staticmethod
    def _metrics(**values):
        metrics = {
            "order_count": 0, "sales_amount": 0.0, "gross_sales_amount": 0.0,
            "refund_amount": 0.0, "discount_amount": 0.0, "cash_control": True,
            "expected_cash": 0.0, "counted_cash": 0.0, "cash_difference": 0.0,
            "cash_out_amount": 0.0, "primary_employee_id": False,
            "refund_employee_id": False, "discount_employee_id": False,
            "cashier_employee_ids": [],
        }
        metrics.update(values)
        return metrics

    def _audit(self, session, rule):
        return self.Audit.sudo().search(
            [("session_id", "=", session.id), ("rule", "=", rule)])

    # ------------------------------------------------------------------
    # 1. defaults & configuration
    # ------------------------------------------------------------------
    def test_01_disabled_by_default(self):
        self.assertFalse(
            self.env["pos.config"].default_get(["crm_enabled"]).get("crm_enabled"))
        self.assertFalse(self.config.crm_enabled)
        session = self.open_new_session()
        huge = self._metrics(sales_amount=10.0, gross_sales_amount=10.0,
                             refund_amount=1000.0, discount_amount=1000.0,
                             cash_difference=9999.0, cash_out_amount=9999.0)
        self.assertFalse(session._crm_log_breaches(huge))
        self.assertFalse(self.Audit.sudo().search([("session_id", "=", session.id)]))

    def test_02_threshold_validation(self):
        for vals in (
            {"crm_cash_diff_threshold": -1.0},
            {"crm_refund_pct_threshold": 101.0},
            {"crm_discount_pct_threshold": -5.0},
            {"crm_cash_out_threshold": -0.01},
        ):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self._enable(**vals)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._enable()  # enabled with every threshold at 0
        self._enable(crm_refund_pct_threshold=5.0)
        self.assertTrue(self.config.crm_enabled)

    # ------------------------------------------------------------------
    # 2. the four rules
    # ------------------------------------------------------------------
    def test_03_cash_difference_rule(self):
        self._enable(crm_cash_diff_threshold=50.0)
        session = self.open_new_session()
        if not session.config_id.cash_control:
            self.skipTest("Test configuration has no cash control")
        session.sudo().write({
            "cash_register_balance_end_real": session.cash_register_balance_end + 75.0})
        metrics = session._crm_compute_metrics()
        self.assertAlmostEqual(metrics["cash_difference"], 75.0)
        audit = session._crm_log_breaches(metrics)
        self.assertEqual(audit.rule, "cash_difference")
        self.assertAlmostEqual(audit.actual_value, 75.0)
        self.assertAlmostEqual(audit.allowed_amount, 50.0)
        self.assertAlmostEqual(audit.variance_amount, 25.0)
        self.assertAlmostEqual(audit.counted_cash - audit.expected_cash, 75.0)
        self.assertEqual(audit.decision, "pending")
        # within threshold / shortage symmetric
        self.assertFalse(session._crm_evaluate_breaches(self._metrics(cash_difference=-50.0)))
        self.assertTrue(session._crm_evaluate_breaches(self._metrics(cash_difference=-50.01)))
        # no cash control -> rule skipped
        self.assertFalse(session._crm_evaluate_breaches(
            self._metrics(cash_control=False, cash_difference=500.0)))

    def test_04_refund_rule(self):
        self._enable(crm_refund_pct_threshold=5.0)
        session = self.open_new_session()
        self._make_order(session, [(10, 100.0, 0.0)])
        self._make_order(session, [(-1, 100.0, 0.0)])
        metrics = session._crm_compute_metrics()
        self.assertAlmostEqual(metrics["sales_amount"], 1000.0)
        self.assertAlmostEqual(metrics["refund_amount"], 100.0)
        audit = session._crm_log_breaches(metrics)
        self.assertEqual(audit.rule, "refund")
        self.assertEqual(audit.rule_kind, "percent")
        self.assertAlmostEqual(audit.actual_value, 10.0)
        self.assertAlmostEqual(audit.base_amount, 1000.0)
        self.assertAlmostEqual(audit.allowed_amount, 50.0)
        self.assertAlmostEqual(audit.variance_amount, 50.0)
        self.assertAlmostEqual(audit.variance_pct, 100.0)
        self.assertFalse(audit.zero_sales)
        self.assertTrue(audit.breach_explanation)
        # exactly on the threshold is not a breach
        self.assertFalse(session._crm_evaluate_breaches(
            self._metrics(sales_amount=1000.0, refund_amount=50.0)))

    def test_05_discount_rule(self):
        self._enable(crm_discount_pct_threshold=15.0)
        session = self.open_new_session()
        self._make_order(session, [(10, 100.0, 20.0)])
        metrics = session._crm_compute_metrics()
        self.assertAlmostEqual(metrics["sales_amount"], 800.0)
        self.assertAlmostEqual(metrics["gross_sales_amount"], 1000.0)
        self.assertAlmostEqual(metrics["discount_amount"], 200.0)
        audit = session._crm_log_breaches(metrics)
        self.assertEqual(audit.rule, "discount")
        self.assertAlmostEqual(audit.actual_value, 20.0)
        self.assertAlmostEqual(audit.allowed_amount, 150.0)
        self.assertAlmostEqual(audit.variance_amount, 50.0)
        # 100% discounted sale is caught (gross base is never zero here)
        breaches = session._crm_evaluate_breaches(
            self._metrics(sales_amount=0.0, gross_sales_amount=100.0, discount_amount=100.0))
        self.assertEqual(breaches[0]["actual_value"], 100.0)
        self.assertFalse(breaches[0]["zero_sales"])

    def _cash_out(self, session, amount, reason="Bank deposit"):
        session.try_cash_in_out("out", amount, reason, {
            "translatedType": "out", "formattedAmount": str(amount)})

    def test_06_cash_out_rule(self):
        self._enable(crm_cash_out_threshold=500.0)
        session = self.open_new_session()
        if not session.cash_journal_id:
            self.skipTest("Test configuration has no cash journal")
        self._cash_out(session, 600.0)
        # Regression: an opening counting difference (posted by the core to
        # the suspense account) must not be counted as a cash out.
        self.env["account.bank.statement.line"].sudo().create({
            "pos_session_id": session.id,
            "journal_id": session.cash_journal_id.id,
            "amount": -1542.0,
            "date": fields.Date.today(),
            "payment_ref": "Cash difference observed during the counting (Loss) - opening",
        })
        tagged = session.statement_line_ids.filtered("crm_is_pos_cash_move")
        self.assertEqual(len(tagged), 1)
        metrics = session._crm_compute_metrics()
        self.assertAlmostEqual(metrics["cash_out_amount"], 600.0)
        audit = session._crm_log_breaches(metrics)
        self.assertEqual(audit.rule, "cash_out")
        self.assertAlmostEqual(audit.variance_amount, 100.0)

    # ------------------------------------------------------------------
    # 3. edge cases, attribution, idempotency
    # ------------------------------------------------------------------
    def test_07_zero_sales(self):
        self._enable(crm_refund_pct_threshold=50.0, crm_discount_pct_threshold=50.0)
        session = self.open_new_session()
        # no sales, no refunds -> nothing, no division error
        self.assertFalse(session._crm_evaluate_breaches(self._metrics()))
        # refund without any sale
        self._make_order(session, [(-1, 100.0, 0.0)])
        metrics = session._crm_compute_metrics()
        self.assertEqual(metrics["sales_amount"], 0.0)
        audit = session._crm_log_breaches(metrics)
        self.assertEqual(audit.rule, "refund")
        self.assertTrue(audit.zero_sales)
        self.assertAlmostEqual(audit.actual_value, 100.0)
        self.assertAlmostEqual(audit.allowed_amount, 0.0)
        self.assertAlmostEqual(audit.variance_amount, 100.0)
        self.assertTrue(audit.breach_explanation)

    def test_08_responsible_employee(self):
        self._enable(crm_refund_pct_threshold=5.0)
        session = self.open_new_session()
        self._make_order(session, [(10, 100.0, 0.0)])
        self._make_order(session, [(-2, 100.0, 0.0)])
        audit = session._crm_log_breaches()
        self.assertEqual(audit.user_id, session.user_id)
        self.assertEqual(audit.employee_id, self.employee)
        self.assertIn(self.employee, audit.cashier_employee_ids)

    def test_09_multi_rule_and_no_duplicates(self):
        self._enable(crm_refund_pct_threshold=5.0, crm_discount_pct_threshold=10.0)
        session = self.open_new_session()
        metrics = self._metrics(sales_amount=1000.0, gross_sales_amount=1250.0,
                                refund_amount=200.0, discount_amount=250.0)
        audits = session._crm_log_breaches(metrics)
        self.assertEqual(set(audits.mapped("rule")), {"refund", "discount"})
        self.assertEqual(set(audits.mapped("session_breach_count")), {2})

        again = session._crm_log_breaches(metrics)
        self.assertEqual(again, audits)
        self.assertEqual(self.Audit.sudo().search_count([("session_id", "=", session.id)]), 2)

        refund = audits.filtered(lambda a: a.rule == "refund")
        refund.with_user(self.manager).action_accept("ok")
        metrics.update(refund_amount=300.0, discount_amount=300.0)
        session._crm_log_breaches(metrics)
        self.assertAlmostEqual(refund.breach_amount, 200.0, msg="decided entry frozen")
        discount = audits - refund
        self.assertAlmostEqual(discount.breach_amount, 300.0, msg="pending entry refreshed")

    # ------------------------------------------------------------------
    # 4. admin decisions
    # ------------------------------------------------------------------
    def test_10_admin_decisions(self):
        self._enable(crm_refund_pct_threshold=5.0, crm_discount_pct_threshold=10.0)
        session = self.open_new_session()
        audits = session._crm_log_breaches(self._metrics(
            sales_amount=1000.0, gross_sales_amount=1250.0,
            refund_amount=200.0, discount_amount=250.0))
        refund = audits.filtered(lambda a: a.rule == "refund")
        discount = audits - refund

        # cashiers cannot decide
        with self.assertRaises(AccessError):
            refund.with_user(self.cashier_user).action_accept()

        refund.with_user(self.manager).action_accept("Approved by store manager")
        self.assertEqual(refund.decision, "accepted")
        self.assertEqual(refund.decision_user_id, self.manager)
        self.assertTrue(refund.decision_date)
        self.assertEqual(refund.review_notes, "Approved by store manager")

        wizard = self.env["pos.cashier.risk.decision.wizard"].with_user(self.manager).create({
            "audit_ids": [(6, 0, discount.ids)],
            "decision": "violation",
            "review_notes": "Unauthorised discounts",
        })
        wizard.action_apply()
        self.assertEqual(discount.decision, "violation")
        self.assertEqual(discount.violation_count, 1)
        self.assertEqual(discount.decision_user_id, self.manager)

        # no second decision, no tampering, no deletion
        with self.assertRaises(UserError):
            discount.with_user(self.manager).action_accept()
        with self.assertRaises(UserError):
            refund.with_user(self.manager).write({"breach_amount": 1.0})
        with self.assertRaises(UserError):
            refund.with_user(self.manager).write({"decision": "violation"})
        with self.assertRaises(UserError):
            refund.sudo().unlink()

        # administrators may reopen (tracked)
        refund.sudo().action_reset_to_pending()
        self.assertEqual(refund.decision, "pending")
        self.assertFalse(refund.decision_user_id)

    # ------------------------------------------------------------------
    # 5. closing hook (end to end)
    # ------------------------------------------------------------------
    def test_11_session_closing_creates_audit(self):
        self._enable(crm_cash_out_threshold=500.0)
        session = self.open_new_session()
        if not session.cash_journal_id:
            self.skipTest("Test configuration has no cash journal")
        self._cash_out(session, 600.0)
        session.post_closing_cash_details(session.cash_register_balance_end)
        result = session.close_session_from_ui()
        self.assertTrue(result.get("successful"), result)
        self.assertEqual(session.state, "closed")
        audit = self._audit(session, "cash_out")
        self.assertEqual(len(audit), 1)
        self.assertAlmostEqual(audit.breach_amount, 600.0)
        self.assertEqual(audit.config_id, self.config)
        self.assertTrue(audit.session_stop_at)

    # ------------------------------------------------------------------
    # 6. reporting integrity
    # ------------------------------------------------------------------
    def test_12_reporting_list_pivot(self):
        self._enable(crm_refund_pct_threshold=5.0, crm_discount_pct_threshold=10.0,
                     crm_cash_out_threshold=100.0)
        session = self.open_new_session()
        audits = session._crm_log_breaches(self._metrics(
            sales_amount=1000.0, gross_sales_amount=1250.0, refund_amount=200.0,
            discount_amount=250.0, cash_out_amount=180.0,
            primary_employee_id=self.employee.id, refund_employee_id=self.employee.id,
            discount_employee_id=self.employee.id))
        self.assertEqual(len(audits), 3)
        audits.filtered(lambda a: a.rule == "discount").with_user(self.manager) \
            .action_log_violation()

        Audit = self.Audit.with_user(self.manager)
        domain = [("session_id", "=", session.id)]
        groups = Audit.read_group(
            domain, ["breach_amount:sum", "variance_amount:sum", "violation_count:sum"],
            ["rule"])
        by_rule = {g["rule"]: g for g in groups}
        self.assertAlmostEqual(by_rule["refund"]["breach_amount"], 200.0)
        self.assertAlmostEqual(by_rule["refund"]["variance_amount"], 150.0)
        self.assertAlmostEqual(by_rule["discount"]["variance_amount"], 125.0)
        self.assertAlmostEqual(by_rule["cash_out"]["variance_amount"], 80.0)
        self.assertEqual(by_rule["discount"]["violation_count"], 1)

        total = Audit.read_group(domain, ["variance_amount:sum", "violation_count:sum"],
                                 ["employee_id"])
        self.assertEqual(len(total), 1)
        self.assertAlmostEqual(total[0]["variance_amount"], 355.0)
        self.assertEqual(total[0]["violation_count"], 1)
        self.assertEqual(total[0]["employee_id_count"], 3)

        # every report view loads for a manager
        views = Audit.get_views([(False, "tree"), (False, "pivot"),
                                 (False, "form"), (False, "search")])
        for view_type in ("tree", "pivot", "form", "search"):
            self.assertIn(view_type, views["views"])
        # cashiers have no access to the report
        with self.assertRaises(AccessError):
            self.Audit.with_user(self.cashier_user).search([])
