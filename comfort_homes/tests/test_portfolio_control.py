"""Portfolio Control rules, with frappe stubbed. Local logic tests only; not a site test."""
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 21, 9, 0, 0)


@pytest.fixture()
def c(monkeypatch):
    fr = types.ModuleType("frappe")
    fr.whitelist = lambda *a, **k: (lambda f: f)
    fr.PermissionError = PermissionError
    fr.get_roles = lambda: ["Loan Officer"]
    fr.session = types.SimpleNamespace(user="x@y.z")
    def throw(m, e=Exception): raise e(m)
    fr.throw = throw
    ut = types.ModuleType("frappe.utils")
    ut.flt = lambda v, p=None: float(v or 0)
    ut.getdate = lambda v: v.date() if isinstance(v, datetime) else (datetime.strptime(v, "%Y-%m-%d").date() if isinstance(v, str) else v)
    ut.now_datetime = lambda: NOW
    ut.today = lambda: "2026-11-21"
    fr.utils = ut
    settings = types.ModuleType("comfort_homes.hp_lending.hp_settings"); settings.COMPANY = "Comfort Home Furnishing PTE Limited"
    for name, mod in {"frappe": fr, "frappe.utils": ut, "comfort_homes": types.ModuleType("comfort_homes"),
                      "comfort_homes.hp_lending": types.ModuleType("comfort_homes.hp_lending"),
                      "comfort_homes.hp_lending.hp_settings": settings}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    sys.modules["comfort_homes.hp_lending"].hp_settings = settings
    spec = importlib.util.spec_from_file_location("hp_control_under_test", ROOT / "hp_lending/hp_control.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def app(name, state, hours_ago=1, branch="Suva"):
    return dict(name=name, workflow_state=state, custom_branchlocation=branch, applicant_name="C " + name,
                modified=NOW - timedelta(hours=hours_ago))


def loan(name, application, **kw):
    base = dict(name=name, loan_application=application, status="Disbursed", docstatus=1, is_npa=0, cost_center="Suva",
                applicant_name="C", modified=NOW, total_payment=879.6, total_principal_paid=73.3,
                debit_adjustment_amount=0, credit_adjustment_amount=0)
    base.update(kw); return base


@pytest.mark.parametrize("dpd,expected", [(0, "Current"), (1, "Grace"), (7, "Grace"), (8, "D30"), (30, "D30"),
                                          (31, "D60"), (60, "D60"), (61, "D90"), (89, "D90"), (90, "NPA")])
def test_buckets_match_agreed_ranges(c, dpd, expected):
    assert c.bucket(dpd) == expected
    assert c.bucket(5, is_npa=True) == "NPA"


def test_light_rules(c):
    assert c.light(5, "", 73) == "Red" and c.light(5, "", 72) == "Orange" and c.light(11, "", 2) == "Orange"
    assert c.light(13, "Current", 999) == "Green" and c.light(13, "Grace", 0) == "Orange"
    assert c.light(13, "D30", 0) == "Red" and c.light(13, "NPA", 0) == "Red"
    assert c.light(14, "", 999) == "Green"


def test_rows_are_ordered_worst_first_and_cover_every_stage(c):
    apps = [app("A-ok", "Active Loan Account"), app("A-npa", "Active Loan Account"), app("A-idle", "LPO Sent to Supplier", 100),
            app("A-new", "Initiated", 2), app("A-closed", "Closed"), app("A-rej", "Rejected")]
    loans = [loan("L-ok", "A-ok"), loan("L-npa", "A-npa"), loan("L-closed", "A-closed", status="Closed")]
    demands = {"L-npa": dict(oldest="2026-08-01", amount=300.0)}
    rows = c.assemble(apps, loans, demands, {"L-ok": "2026-11-28"}, {}, {}, NOW, {"include_closed": 1})
    assert [r["application"] for r in rows] == ["A-npa", "A-idle", "A-new", "A-ok", "A-closed"]  # rejected hidden by default
    by = {r["application"]: r for r in rows}
    assert by["A-npa"]["bucket"] == "NPA" and by["A-npa"]["light"] == "Red" and by["A-npa"]["overdue"] == 300.0
    assert by["A-idle"]["light"] == "Red" and by["A-idle"]["delivery"] == "Pending"
    assert by["A-new"]["light"] == "Orange" and by["A-ok"]["light"] == "Green" and by["A-ok"]["next_due"] == "2026-11-28"
    assert by["A-ok"]["balance"] == 806.3


def test_filters_and_pi_and_delivery(c):
    apps = [app("A1", "Delivered - Awaiting Documents"), app("A2", "Active Loan Account")]
    inv = {"A1": [dict(grand_total=500, outstanding_amount=500)], "A2": [dict(grand_total=500, outstanding_amount=0)]}
    rows = c.assemble(apps, [loan("L2", "A2")], {}, {}, inv, {}, NOW, {})
    by = {r["application"]: r for r in rows}
    assert by["A1"]["pi"] == "Open" and by["A1"]["delivery"] == "Yes" and by["A2"]["pi"] == "Paid"
    assert [r["application"] for r in c.assemble(apps, [loan("L2", "A2")], {}, {}, inv, {}, NOW, {"stage": "Active"})] == ["A2"]
    assert c.pi_status([dict(grand_total=500, outstanding_amount=200)]) == "Part" and c.pi_status([]) == "None"


def test_recent_comment_or_po_activity_resets_idle(c):
    rows = c.assemble([app("A", "LPO Approved", 200)], [], {}, {}, {}, {"A": NOW - timedelta(hours=5)}, NOW, {})
    assert rows[0]["light"] == "Orange" and rows[0]["idle_hours"] == 5.0


def test_only_directors_and_managers(c, monkeypatch):
    with pytest.raises(PermissionError): c.require_role()
    monkeypatch.setattr(c.frappe, "get_roles", lambda: ["Loan Manager"]); c.require_role()
    monkeypatch.setattr(c.frappe, "get_roles", lambda: ["Loan Officer", "Accounts Officer", "Loan Reporter", "Auditor"])
    with pytest.raises(PermissionError): c.require_role()


def test_report_workspace_and_cards_are_restricted_to_directors_and_managers():
    mod = ROOT / "comfort_homes"
    allowed = {"Director", "Finance Manager", "Loan Manager"}
    for path in [mod / "report/portfolio_control/portfolio_control.json", mod / "workspace/portfolio_control/portfolio_control.json"]:
        assert {r["role"] for r in json.loads(path.read_text())["roles"]} == allowed
    for card in (mod / "number_card").glob("portfolio_*/*.json"):
        d = json.loads(card.read_text())
        assert d["type"] == "Custom" and d["method"].startswith("comfort_homes.hp_lending.hp_control.card_")


def test_dev13_co_app_status(c):
    assert c.co_app_status([]) == "None"
    assert c.co_app_status(["Approved", "Draft"]) == "Pending"
    assert c.co_app_status(["Approved"]) == "Approved"


def test_dev13_row_has_co_app(c):
    app = dict(name="A1", workflow_state="Under Review", custom_branchlocation="X", applicant_name="N", modified=datetime(2026, 11, 20))
    rows = c.assemble([app], [], {}, {}, {}, {}, NOW, None, {"A1": ["Draft"]})
    assert rows[0]["co_app"] == "Pending"
    rows = c.assemble([app], [], {}, {}, {}, {}, NOW)
    assert rows[0]["co_app"] == "None"
