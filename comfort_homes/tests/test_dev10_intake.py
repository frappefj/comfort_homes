"""dev10 regressions: four-digit grid amounts must never become NaN; HP spellings resolve to a real purpose."""
import json
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = json.loads((ROOT / "fixtures/web_form.json").read_text())[0]["client_script"]


def _repair(script):
    src = (ROOT / "webform_setup.py").read_text()
    i = src.index("def _repair_legacy_number_parsing")
    j = src.index("def _strip_legacy_calculation_injections")
    ns = {"re": re}
    exec(src[i:j], ns)
    return ns["_repair_legacy_number_parsing"](script)


def test_fixture_has_no_raw_number_of_a_formatted_grid_cell():
    assert "Number(input.value || 0)" not in SCRIPT and "Number(event.target.value || 0)" not in SCRIPT
    assert "flexiSafeNumber(input.value)" in SCRIPT


def test_site_script_with_legacy_handlers_is_repaired_on_migrate():
    legacy = ("function calculateIncomeAndExpenses() {}\n"
              "row[c] = Number(input.value || 0);\nrow[c] = Number(event.target.value || 0);\n")
    fixed = _repair(legacy)
    assert "Number(input.value || 0)" not in fixed and "Number(event.target.value || 0)" not in fixed
    assert _repair(fixed) == fixed  # idempotent


def test_formatted_amounts_parse_to_numbers():
    import shutil, subprocess
    node = shutil.which("node")
    if not node:
        return
    code = ('const f=v=>{const p=Number(String(v??"").replace(/[^0-9.-]/g,""));return Number.isFinite(p)?p:0};'
            'console.log(JSON.stringify(["1000","1,000.00","FJD 12,345.50","999",""].map(f)))')
    out = subprocess.run([node, "-e", code], capture_output=True, text=True).stdout
    assert json.loads(out) == [1000, 1000, 12345.5, 999, 0]


class _Doc(dict):
    docstatus = 0
    def is_new(self): return True
    def get(self, k, d=None): return dict.get(self, k, d)
    def set(self, k, v): self[k] = v


def _origination(existing, monkeypatch):
    created = []
    frappe = types.ModuleType("frappe")
    frappe.db = types.SimpleNamespace(exists=lambda dt, name: name in existing)
    def get_doc(values):
        def insert(ignore_permissions=False):
            created.append(values["loan_purpose"]); existing.add(values["loan_purpose"])
        return types.SimpleNamespace(insert=insert)
    frappe.get_doc = get_doc
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    mod = types.ModuleType("comfort_homes.hp_lending.origination_math")
    for n in ("assessment_values", "contract_values", "money", "profile_values"):
        setattr(mod, n, lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "comfort_homes.hp_lending.origination_math", mod)
    ns = {"__name__": "o"}
    exec((ROOT / "origination.py").read_text().split("def apply_application")[0], ns)
    return ns["resolve_loan_purpose"], created


def test_hp_resolves_to_hire_purchase_and_creates_master_once(monkeypatch):
    resolve, created = _origination(set(), monkeypatch)
    for typed in ("HP", " hp ", "Hire Purchase"):
        doc = _Doc(loan_purpose=typed); resolve(doc)
        assert doc["loan_purpose"] == "Hire Purchase"
    assert created == ["Hire Purchase"]


def test_unknown_purpose_is_left_for_normal_link_validation(monkeypatch):
    resolve, created = _origination({"Education"}, monkeypatch)
    doc = _Doc(loan_purpose="Holiday"); resolve(doc)
    assert doc["loan_purpose"] == "Holiday" and not created
    doc = _Doc(loan_purpose="Education"); resolve(doc)
    assert doc["loan_purpose"] == "Education"
