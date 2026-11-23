=======================
POS Cashier Risk Audit
=======================

Odoo 16.0 Community & Enterprise. Depends on ``point_of_sale`` and ``hr``.

Configuration
=============
Point of Sale > Configuration > Settings > (select the POS) > *Cashier Risk Monitor*.
The monitor is disabled by default. Once enabled, set any of the four thresholds
(0 disables a rule, at least one must be set):

* **Cash Difference Above** - absolute |counted - expected| closing cash (needs cash control).
* **Refunds Above (% of sales)** - refunded lines / net sale lines.
* **Discounts Above (% of sales)** - (line discounts + global discount / loyalty reward lines)
  / sales before discounts.
* **Cash Out Above** - manual cash outs of the session (opening/closing counting
  differences are excluded; only lines created by the POS Cash In/Out button count).

All sales figures are tax included, in the session currency.
Zero sales: refunds or discounts recorded with no sales base are always logged,
reported at 100% and flagged *No Sales Base* (no division by zero).

How it works
============
* Metrics are captured right before the core closes the session (``_validate_session``)
  and one audit entry per breached rule is created once the session is closed.
  A failure in the monitor never blocks a cash closing; it is logged in the session chatter.
* Responsible employee: the cashier (``pos_hr`` employee, else the order user's
  employee) with the most refunds/discounts for those rules, otherwise the session's
  main cashier; all session cashiers are also recorded.
* Re-evaluation is idempotent: one entry per session and rule (SQL unique), pending
  entries are refreshed, decided entries are never modified.

Review
======
Point of Sale > Reporting > *Cashier Risk Audit* (list, pivot, form), POS managers only.
Select entries and click **Accept** or **Log as Violation** (optional review notes).
Decision, user and date are tracked in the chatter; audit figures cannot be edited,
duplicated or deleted once decided. Administrators can reopen a decision.

Tests
=====
``odoo-bin -d <db> -i pos_cashier_risk_audit --test-tags /pos_cashier_risk_audit``
