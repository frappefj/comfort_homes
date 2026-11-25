"""dev13: purchase-value limit, delivery docket, deposit-before-contract, assessment row, CIRA button, co-applicants."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from comfort_homes.tests.test_document_process import controls, doc  # noqa: F401
from comfort_homes.tests.test_gross_controls import env  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _fixture(name):
    return json.loads((ROOT / "fixtures" / name).read_text())


def test_finance_manager_limit_uses_purchase_value():
    wf = _fixture("zz_workflow.json")
    text = json.dumps(wf)
    assert "doc.loan_amount < 3000" not in text
    assert text.count("(doc.custom_flexi_cash_price or 0) < 3000") >= 2


def test_delivery_docket_field_is_single_attach():
    f = [x for x in _fixture("custom_field.json") if x["name"] == "Loan Application-custom_hp_delivery_docket"]
    assert len(f) == 1 and f[0]["fieldtype"] == "Attach" and f[0]["allow_on_submit"] == 1


def test_assessment_not_auto_added_as_customer_document():
    hooks = (ROOT / "hooks.py").read_text()
    assert "ensure_assessment_customer_document" not in hooks
    assert "require_deposit_before_contract" in hooks


def test_cira_button_only_early_stages():
    js = [x for x in _fixture("client_script.json") if x["name"] == "Flexi CIRA Authority Desk Flow"][0]["script"]
    assert '["Draft", "Initiated"].includes(frm.doc.workflow_state)' in js


def test_cira_message_names_co_applicant(controls):
    _, m = controls
    p = doc(doctype="Co-Applicant", name="CO-APP-1", applicant_name="Jane Roe", custom_cira_authority_signature=None)
    with pytest.raises(ValueError, match=r"co-applicant Jane Roe \(CO-APP-1\).*before KYC initiation"):
        m._require_cira(p)


def test_cira_message_for_main_applicant_unchanged(controls):
    _, m = controls
    with pytest.raises(ValueError, match="digital signature before KYC initiation"):
        m._require_cira(doc(custom_cira_authority_signature=None))


def test_co_applicant_status_summary(controls, monkeypatch):
    f, m = controls
    app = doc()
    f.get_doc = Mock(return_value=app)
    p = doc(doctype="Co-Applicant", name="CO-APP-1", workflow_state="Draft", custom_document_collection_complete="No", custom_hp_cira_signed_on="2026-11-17")
    monkeypatch.setattr(m, "_profiles", lambda a: [p])
    out = m.co_applicant_status("APP")
    assert out == [dict(name="CO-APP-1", applicant_name="Test Client", workflow_state="Draft", cira_signed=1, kyc_complete=0)]
