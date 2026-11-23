# -*- coding: utf-8 -*-
{
    "name": "POS Cashier Risk Audit",
    "summary": "Cashier Risk Monitor: evaluate cash difference, refunds, discounts "
               "and cash outs on POS session closing, with manager Accept / "
               "Violation decisions and audit reporting.",
    "version": "18.0.1.0.2",
    "category": "Sales/Point of Sale",
    "author": "Ibrahim Yahia",
    "website": "https://www.linkedin.com/in/ibrahim-yahia",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "hr"],
    "data": [
        "security/pos_cashier_risk_audit_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "wizard/pos_cashier_risk_decision_wizard_views.xml",
        "views/pos_cashier_risk_audit_views.xml",
        "views/pos_session_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
}
