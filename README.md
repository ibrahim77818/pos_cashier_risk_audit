# POS Cashier Risk Audit (`pos_cashier_risk_audit`)

Cashier Risk Monitor for the Odoo Point of Sale: when a session closes, its cash difference, refunds, discounts and cash outs are checked against per-POS limits; every breach is logged against the responsible cashier for a manager to **Accept** or **Log as Violation**.

**This branch:** Odoo 19.0 (module version `19.0.1.0.2`) · Community & Enterprise · English & Arabic · LGPL-3

## Branches

| Branch | Odoo |
|---|---|
| [`16.0`](../../tree/16.0) | 16.0 (default) |
| [`17.0`](../../tree/17.0) | 17.0 |
| [`18.0`](../../tree/18.0) | 18.0 |
| [`19.0`](../../tree/19.0) | 19.0 |
| [`20.0`](../../tree/20.0) | 20.0 |

## Features

- Four thresholds per Point of Sale: **Cash Difference Above**, **Refunds Above (% of sales)**, **Discounts Above (% of sales)**, **Cash Out Above** (0 disables a rule; monitor off by default).
- Evaluated automatically at session closing; one audit entry per session and breached rule, never duplicated, frozen once decided.
- Responsible employee per rule (`pos_hr` cashier or the user's employee); all session cashiers recorded.
- Manager decisions (single or bulk) with notes, tracked in the chatter; figures cannot be edited, entries cannot be duplicated, decided entries cannot be deleted.
- **Point of Sale › Reporting › Cashier Risk Audit**: list, pivot and form views with audit filters and group-bys.
- Edge cases handled: zero-sales sessions, 100% line discounts, global discount / loyalty reward lines, counting differences excluded from cash outs.

## Installation

1. Put this repository (branch matching your Odoo version) in your addons path as a folder named `pos_cashier_risk_audit`, e.g. on Odoo.sh:
   ```bash
   git submodule add -b 19.0 https://github.com/ibrahim77818/pos_cashier_risk_audit.git pos_cashier_risk_audit
   ```
2. Update the apps list and install **POS Cashier Risk Audit** (depends on `point_of_sale` and `hr`).
3. Point of Sale › Configuration › Settings › **Cashier Risk Monitor**: enable it for a POS and set the thresholds.

Run the tests: `odoo-bin -d <db> -u pos_cashier_risk_audit --test-tags /pos_cashier_risk_audit --stop-after-init`

## Author

Ibrahim Yahia · [LinkedIn](https://www.linkedin.com/in/ibrahim-yahia) · ibrahim.yahiah@gmail.com
