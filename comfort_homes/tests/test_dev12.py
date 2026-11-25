"""dev12: spouse/Loan Purpose form rules, refresh_input guard, HP GL Trace logic. Local tests only."""
import importlib.util
import json
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "fixtures/web_form.json").read_text())[0]
MARRIED = 'eval:doc.custom_flexi_marital_status == "Married";'


def _fields():
    return {f["fieldname"]: f for f in FIXTURE["web_form_fields"]}


def test_fixture_spouse_rules():
    f = _fields()
    assert f["custom_flexi_spouse_section"]["depends_on"] == MARRIED
    assert not f["custom_flexi_spouse_section"].get("mandatory_depends_on")  # the cause of "refresh_input is not a function"
    for name in ("first_name", "surname", "mobile", "employer", "employer_contact"):
        assert f["custom_flexi_spouse_" + name]["mandatory_depends_on"] == MARRIED
    for name in ("middle_name", "dob", "email", "occupation"):
        assert not f["custom_flexi_spouse_" + name].get("mandatory_depends_on")
    assert f["loan_purpose"]["hidden"] == 1 and not f["loan_purpose"]["reqd"]


def test_migrate_rule_function_repairs_an_existing_site_form():
    src = (ROOT / "webform_setup.py").read_text()
    i = src.index("SPOUSE_SECTION ="); j = src.index("def _ensure_employment_fields")
    ns = {}; exec(src[i:j], ns)
    mk = lambda n, **k: types.SimpleNamespace(fieldname=n, depends_on="", mandatory_depends_on="", reqd=0, hidden=0, **k)
    form = types.SimpleNamespace(web_form_fields=[
        mk("custom_flexi_spouse_section", ), mk("custom_flexi_spouse_first_name"), mk("custom_flexi_spouse_email"), mk("loan_purpose")])
    form.web_form_fields[0].mandatory_depends_on = MARRIED  # the broken site state
    form.get = lambda k: form.web_form_fields
    ns["_apply_dev12_field_rules"](form)
    sec, first, email, purpose = form.web_form_fields
    assert sec.mandatory_depends_on == "" and sec.depends_on == MARRIED
    assert first.mandatory_depends_on == MARRIED and email.mandatory_depends_on == ""
    assert purpose.hidden == 1 and purpose.reqd == 0


def test_script_has_refresh_input_guard():
    assert "guardRefreshInput" in FIXTURE["client_script"]
    assert FIXTURE["client_script"].endswith((ROOT / "public/js/hp_webform_submit.js").read_text())


def test_guard_in_browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try: browser = p.chromium.launch()
        except Exception as exc: pytest.skip(str(exc))
        pg = browser.new_page()
        html = ("<html><body><script>window.$=()=>({on(){}});window.__=s=>s;window.frappe={web_form:{fields_dict:"
                "{sec:{df:{reqd:1}},data:{df:{},refresh_input(){window.kept=1}}},events:{on(){}}}};</script></body></html>")
        pg.route("http://t.test/**", lambda r: r.fulfill(body=html if not r.request.url.endswith(".js") else (ROOT / "public/js/hp_webform_submit.js").read_text(),
                                                          content_type="text/html" if not r.request.url.endswith(".js") else "text/javascript"))
        pg.goto("http://t.test/x")
        pg.add_script_tag(url="http://t.test/s.js")
        res = pg.evaluate("""() => { const f = frappe.web_form.fields_dict; flexiSubmit.guardRefreshInput(frappe.web_form);
            f.sec.refresh_input(); f.data.refresh_input(); return [typeof f.sec.refresh_input, window.kept]; }""")
        browser.close()
    assert res == ["function", 1]  # section gets a no-op, real controls keep their own method


# ---- HP GL Trace -------------------------------------------------------------------------------------------
@pytest.fixture()
def trace(monkeypatch):
    fr = types.ModuleType("frappe"); fr.PermissionError = PermissionError
    fr.get_roles = lambda: ["Accounts Officer"]; fr.session = types.SimpleNamespace(user="a@b.c")
    def throw(m, e=Exception): raise e(m)
    fr.throw = throw
    ut = types.ModuleType("frappe.utils"); ut.flt = lambda v, p=None: float(v or 0); fr.utils = ut
    st = types.ModuleType("comfort_homes.hp_lending.hp_settings"); st.COMPANY = "C"
    for n, m in {"frappe": fr, "frappe.utils": ut, "comfort_homes": types.ModuleType("comfort_homes"),
                 "comfort_homes.hp_lending": types.ModuleType("comfort_homes.hp_lending"), "comfort_homes.hp_lending.hp_settings": st}.items():
        monkeypatch.setitem(sys.modules, n, m)
    sys.modules["comfort_homes.hp_lending"].hp_settings = st
    spec = importlib.util.spec_from_file_location("hp_gl_trace_t", ROOT / "hp_lending/hp_gl_trace.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _devcom_gl():
    out = []
    for i, line in enumerate((ROOT / "tests/reference/devcom_gl_extract_2026-11-21.txt").read_text().splitlines()):
        t, d, vt, v, acc, dr, cr, party = (line.split("|") + [""])[:8]
        out.append(dict(name=str(i), creation="2026-" + t, posting_date=d, voucher_type=vt, voucher_no=v, account=acc,
                        debit=float(dr), credit=float(cr), party=party, is_cancelled=0))
    return out


def test_real_devcom_extract_balances_and_flags_the_untaxed_fee_invoice(trace):
    rows = trace.assemble(_devcom_gl(), {})
    assert len(rows) == 40
    assert sum(r["debit"] for r in rows) == pytest.approx(sum(r["credit"] for r in rows))
    flagged = {r["voucher"] for r in rows if r["check"]}
    assert flagged == {"ACC-SINV-2026-00001", "ACC-SINV-2026-00002"}  # both fee invoices carry no VAT line
    assert all(r["check"] == "No VAT" for r in rows if r["voucher"] in flagged)


def test_account_and_voucher_views_and_filter(trace):
    rows = trace.assemble(_devcom_gl(), {("Journal Entry", "ACC-JV-2026-00001"): "L1"})
    acc = {a["account"]: a["net"] for a in trace.by_account(rows)}
    assert acc["1180 - HP Goods Clearing Contra - Flexi"] == -849.0 and acc["2010 - Customer HP Deposits - Flexi"] == 0
    assert sum(a["net"] for a in trace.by_account(rows)) == pytest.approx(0)
    vouchers = {v["voucher"]: v for v in trace.by_voucher(rows)}
    assert vouchers["ACC-JV-2026-00001"]["debit"] == 200.6 and vouchers["ACC-JV-2026-00001"]["loan"] == "L1"
    only = trace.assemble(_devcom_gl(), {}, {"account": "1201 - BSP - Flexi - 87349722 - Flexi"})
    assert {r["account"] for r in only} == {"1201 - BSP - Flexi - 87349722 - Flexi"}


def test_unbalanced_voucher_is_flagged(trace):
    gl = [dict(name="1", creation="2026-11-21 10:00:00", posting_date="2026-11-21", voucher_type="Journal Entry", voucher_no="J",
               account="A", debit=10, credit=0, party="", is_cancelled=0)]
    assert trace.assemble(gl, {})[0]["check"] == "Unbalanced"


def test_role_gate(trace, monkeypatch):
    trace.require_role()
    monkeypatch.setattr(trace.frappe, "get_roles", lambda: ["Loan Officer", "Loan Manager"])
    with pytest.raises(PermissionError): trace.require_role()


def test_report_files_are_role_restricted():
    d = json.loads((ROOT / "comfort_homes/report/hp_gl_trace/hp_gl_trace.json").read_text())
    assert {r["role"] for r in d["roles"]} == {"Director", "Finance Manager", "Accounts Officer"} and d["report_type"] == "Script Report"


def test_fetch_follows_loan_application_deposit_supplier_and_payments(trace, monkeypatch):
    gl_rows = {("Loan Disbursement", "D1"): ("1101 - HP Receivables - Current - Flexi", 100, 0), ("Payment Entry", "PE-DEP"): ("1201 - BSP", 10, 0),
               ("Purchase Invoice", "PI1"): ("2111 - Trade Payables", 0, 90), ("Payment Entry", "PE-SUP"): ("1201 - BSP", 0, 90),
               ("Sales Invoice", "SI1"): ("1120 - Other Debtors", 7.5, 0), ("Journal Entry", "JE1"): ("2010 - Deposits", 10, 0)}
    seen = []
    def get_all(dt, filters=None, or_filters=None, fields=None, limit_page_length=0):
        seen.append(dt)
        if dt == "Loan": return [dict(name="L1", loan_application="APP1", applicant="C")]
        if dt == "Loan Disbursement": return [dict(name="D1", against_loan="L1")]
        if dt == "Journal Entry": return [dict(name="JE1", custom_hp_loan="L1")]
        if dt == "Loan Application": return [dict(name="APP1", custom_hp_deposit_payment_entry="PE-DEP")]
        if dt == "Sales Invoice": return [dict(name="SI1", custom_hp_reference_loan="L1", loan="")]
        if dt == "Purchase Invoice": return [dict(name="PI1", custom_loan_application="APP1")]
        if dt == "Payment Entry Reference":
            return [dict(parent="PE-SUP", reference_name="PI1")] if filters["reference_doctype"] == "Purchase Invoice" else []
        if dt == "GL Entry":
            out = []
            for n in filters["voucher_no"][1]:
                a, d, c = gl_rows[(filters["voucher_type"], n)]
                out.append(dict(name=n, creation="2026-11-21 10:00:00", posting_date="2026-11-21", account=a, debit=d, credit=c,
                                voucher_type=filters["voucher_type"], voucher_no=n, party="", is_cancelled=0))
            return out
        return []
    monkeypatch.setattr(trace.frappe, "get_all", get_all, raising=False)
    monkeypatch.setattr(trace.frappe, "db", types.SimpleNamespace(has_column=lambda dt, f: True), raising=False)
    rows = trace.fetch({"application": "APP1"})
    assert {r["voucher"] for r in rows} == {"D1", "JE1", "PE-DEP", "SI1", "PI1", "PE-SUP"}
    assert {r["loan"] for r in rows} == {"L1"}
    no_supplier = trace.fetch({"application": "APP1", "include_supplier": 0})
    assert {r["voucher"] for r in no_supplier} == {"D1", "JE1", "PE-DEP", "SI1"}
    assert {r["voucher"] for r in trace.fetch({"application": "APP1", "voucher_type": "Payment Entry"})} == {"PE-DEP", "PE-SUP"}


# ---- servicing fee VAT check -------------------------------------------------------------------------------
@pytest.fixture()
def feevat(monkeypatch):
    fr = types.ModuleType("frappe"); fr.PermissionError = PermissionError; fr.get_roles = lambda: ["Finance Manager"]
    fr.session = types.SimpleNamespace(user="a@b.c")
    def throw(m, e=Exception): raise e(m)
    fr.throw = throw
    st = types.ModuleType("comfort_homes.hp_lending.hp_settings"); st.COMPANY = "C"
    ro = types.ModuleType("comfort_homes.hp_lending.hp_rollout")
    ro.FEE_RATES = {"SMS Charge": (2.50, "4011"), "Call Charge": (2.50, "4011"), "Field Visit": (25.00, "4008")}
    ro.account = lambda n: {"4011": "4011 - SMS & Com Fee Income - Flexi", "4008": "4008 - Other Income - Flexi", "2310": "2310 - VAT - Flexi"}[n]
    for n, m in {"frappe": fr, "comfort_homes": types.ModuleType("comfort_homes"), "comfort_homes.hp_lending": types.ModuleType("comfort_homes.hp_lending"),
                 "comfort_homes.hp_lending.hp_settings": st, "comfort_homes.hp_lending.hp_rollout": ro}.items():
        monkeypatch.setitem(sys.modules, n, m)
    sys.modules["comfort_homes.hp_lending"].hp_settings = st
    spec = importlib.util.spec_from_file_location("hp_fee_vat_t", ROOT / "hp_lending/hp_fee_vat.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


EXP = {"SMS Charge": "4011 - SMS & Com Fee Income - Flexi", "Call Charge": "4011 - SMS & Com Fee Income - Flexi", "Field Visit": "4008 - Other Income - Flexi"}


def test_inclusive_split_matches_documentation_fee_method(feevat):
    assert tuple(map(float, feevat.split(25))) == (22.22, 2.78)   # same cents as the documentation fee
    assert tuple(map(float, feevat.split(2.5))) == (2.22, 0.28)
    assert tuple(map(float, feevat.split(7.5))) == (6.67, 0.83)


def test_the_two_devcom_invoices_are_flagged_and_a_correct_one_passes(feevat):
    sinv1 = feevat.assess(dict(name="ACC-SINV-2026-00001", posting_date="2026-11-20", customer="C", grand_total=7.5, loan="L3"),
        [dict(item_code=i, amount=2.5, income_account=EXP[i]) for i in ("Call Charge", "SMS Charge", "SMS Charge")], 0, EXP)
    sinv2 = feevat.assess(dict(name="ACC-SINV-2026-00002", posting_date="2026-11-20", customer="C", grand_total=25, loan="L3"),
        [dict(item_code="Field Visit", amount=25, income_account=EXP["Field Visit"])], 0, EXP)
    assert sinv1["status"] == "No VAT" and sinv1["expected_vat"] == 0.83 and sinv1["route"] == "Legacy"
    assert sinv2["status"] == "No VAT" and sinv2["expected_net"] == 22.22 and sinv2["expected_vat"] == 2.78
    ok = feevat.assess(dict(name="N", posting_date="2026-11-21", customer="C", grand_total=25, custom_hp_independent_fee=1),
        [dict(item_code="Field Visit", amount=22.22, income_account=EXP["Field Visit"])], 2.78, EXP)
    assert ok["status"] == "OK" and ok["route"] == "Independent"
    wrong = feevat.assess(dict(name="W", posting_date="2026-11-21", customer="C", grand_total=25),
        [dict(item_code="Field Visit", amount=22.22, income_account="4011 - SMS & Com Fee Income - Flexi")], 2.78, EXP)
    assert wrong["status"] == "Account"


def test_fee_vat_fetch_is_role_gated_and_groups_by_invoice(feevat, monkeypatch):
    def get_all(dt, filters=None, fields=None, limit_page_length=0):
        if dt == "Sales Invoice Item": return [dict(parent="S1", item_code="Field Visit", amount=25, income_account=EXP["Field Visit"])]
        if dt == "Sales Invoice": return [dict(name="S1", posting_date="2026-11-20", customer="C", grand_total=25, loan="L", custom_hp_reference_loan="", custom_hp_independent_fee=0)]
        return []
    monkeypatch.setattr(feevat.frappe, "get_all", get_all, raising=False)
    rows = feevat.fetch({})
    assert len(rows) == 1 and rows[0]["status"] == "No VAT"
    monkeypatch.setattr(feevat.frappe, "get_roles", lambda: ["Loan Officer"])
    with pytest.raises(PermissionError): feevat.fetch({})


def test_loan_connections_list_fee_invoices_through_the_reference_field(monkeypatch):
    fr = types.ModuleType("frappe"); fr._dict = dict
    for n in ("frappe",): monkeypatch.setitem(sys.modules, n, fr)
    src = (ROOT / "loan_mapping.py").read_text()
    i = src.index("def get_loan_dashboard_data"); j = src.index("def get_loan_application_dashboard_data")
    ns = {"frappe": fr}; exec(src[i:j], ns)
    native = {"fieldname": "loan", "non_standard_fieldnames": {"Journal Entry": "reference_name"},
              "transactions": [{"label": "Repayment", "items": ["Loan Repayment"]}, {"label": "Accounting", "items": ["Journal Entry", "Sales Invoice"]}]}
    out = ns["get_loan_dashboard_data"](native)
    assert out["non_standard_fieldnames"]["Sales Invoice"] == "custom_hp_reference_loan" and out["non_standard_fieldnames"]["Journal Entry"] == "reference_name"
    groups = {g["label"]: g["items"] for g in out["transactions"]}
    assert groups["Accounting"] == ["Journal Entry"] and groups["Servicing fees"] == ["Sales Invoice"] and out["fieldname"] == "loan"
    again = ns["get_loan_dashboard_data"](out)  # idempotent
    assert sum("Sales Invoice" in g["items"] for g in again["transactions"]) == 1
    assert 'override_doctype_dashboards' in (ROOT / "hooks.py").read_text() and "get_loan_dashboard_data" in (ROOT / "hooks.py").read_text()
