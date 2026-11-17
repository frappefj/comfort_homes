"""Access-denial tests only; no assertion about legal signature execution."""
import ast
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest

from comfort_homes.tests.test_gross_controls import Row, env, COMPANY


@pytest.fixture
def signing(env, monkeypatch):
    f, load = env
    f.utils.now_datetime = lambda: datetime(2026, 11, 17, 12)
    f.request = SimpleNamespace(method='POST')
    f.form_dict = {'docname': 'APP', 'rowname': 'ROW', 'signature': 'TEST-SIGNATURE',
                   'director_name': 'Test signatory', 'director_signature': 'TEST-SIGNATURE'}
    f.db.set_value = Mock()
    api = load('api.py')
    monkeypatch.setitem(sys.modules, 'comfort_homes.api', api)
    helper = load('agreement_signing.py')
    return f, api, helper


def app():
    return Row(name='APP', company=COMPANY, docstatus=0, check_permission=Mock())


def agreement(**kwargs):
    row = Row(name='ROW', parent='APP', parenttype='Loan Application', parentfield='custom_customer_documents',
              status='Pending', document_code='HP-AGREEMENT', document_template='CURRENT-TEMPLATE')
    row.update(kwargs)
    return row


@pytest.mark.parametrize('endpoint', ['hp_agreement_customer_sign', 'hp_agreement_director_sign'])
def test_direct_signing_requires_write_permission_before_any_mutation(signing, endpoint):
    f, _, helper = signing
    application = app(); application.check_permission.side_effect = ValueError('write denied')
    f.get_doc.return_value = application
    with pytest.raises(ValueError, match='write denied'):
        getattr(helper, endpoint)()
    f.db.set_value.assert_not_called()
    f.db.commit.assert_not_called()


@pytest.mark.parametrize('endpoint', ['hp_agreement_customer_sign', 'hp_agreement_director_sign'])
def test_get_cannot_mutate_through_retained_python_handlers(signing, endpoint):
    f, _, helper = signing
    f.request.method = 'GET'
    with pytest.raises(ValueError, match='require POST'):
        getattr(helper, endpoint)()
    f.get_doc.assert_not_called()
    f.db.set_value.assert_not_called()


@pytest.mark.parametrize('endpoint', ['hp_agreement_customer_sign', 'hp_agreement_director_sign'])
def test_cross_application_agreement_is_rejected(signing, endpoint):
    f, _, helper = signing
    f.get_doc.side_effect = [app(), agreement(parent='OTHER')]
    with pytest.raises(ValueError, match='does not belong'):
        getattr(helper, endpoint)()
    f.db.set_value.assert_not_called()


@pytest.mark.parametrize('endpoint', ['hp_agreement_customer_sign', 'hp_agreement_director_sign'])
def test_completed_evidence_cannot_be_overwritten(signing, endpoint):
    f, _, helper = signing
    f.get_doc.side_effect = [app(), agreement(status='Completed')]
    with pytest.raises(ValueError, match='cannot be overwritten'):
        getattr(helper, endpoint)()
    f.db.set_value.assert_not_called()


def test_company_signing_requires_the_signatory_role(signing):
    f, _, helper = signing
    f.get_doc.return_value = app()
    f.only_for.side_effect = ValueError('signatory role denied')
    with pytest.raises(ValueError, match='signatory role denied'):
        helper.hp_agreement_director_sign()
    f.db.set_value.assert_not_called()


def test_unrelated_document_template_cannot_use_hp_signing_handler(signing):
    f, _, helper = signing
    application, row = app(), agreement(document_code='FORM-1')
    f.get_doc.side_effect = lambda dt, name: application if dt == 'Loan Application' else row
    with pytest.raises(ValueError, match='only handles the HP Agreement'):
        helper.hp_agreement_customer_sign()
    f.db.set_value.assert_not_called()


def test_co_applicant_permission_is_checked_before_primary_signature_is_saved(signing):
    f, _, helper = signing
    application = app(); application.custom_existing_co_applicant = 'CO-TEST'
    profile = Row(company=COMPANY, check_permission=Mock(side_effect=ValueError('profile write denied')))
    docs = {'Loan Application': application, 'Loan Customer Agreement': agreement(), 'Co-Applicant': profile}
    f.get_doc.side_effect = lambda dt, name: docs[dt]
    with pytest.raises(ValueError, match='profile write denied'):
        helper.hp_agreement_customer_sign()
    f.db.set_value.assert_not_called()


def test_preview_requires_read_access_and_does_not_render_on_denial(signing, monkeypatch):
    f, _, helper = signing
    application = app(); application.check_permission.side_effect = ValueError('read denied')
    f.request.method = 'GET'; f.get_doc.return_value = application
    render = Mock(); monkeypatch.setattr(helper, '_render_agreement', render)
    with pytest.raises(ValueError, match='read denied'):
        helper.hp_agreement_preview()
    application.check_permission.assert_called_once_with('read')
    render.assert_not_called(); f.db.set_value.assert_not_called()


def test_retained_python_signing_decorators_and_stable_template_link():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    source = (root / 'agreement_signing.py').read_text(); tree = ast.parse(source)
    for name in ['hp_agreement_customer_sign', 'hp_agreement_director_sign']:
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        assert any(isinstance(d, ast.Call) and any(k.arg == 'methods' and ast.literal_eval(k.value) == ['POST'] for k in d.keywords) for d in node.decorator_list)
    assert 'LDT-00001' not in source
    assert '"document_template": agreement.document_template' in source


@pytest.mark.parametrize('endpoint', ['hp_agreement_customer_sign', 'hp_agreement_director_sign'])
def test_otherwise_authorized_digital_contract_execution_is_explicitly_disabled(signing, endpoint):
    f, _, helper = signing
    application, row = app(), agreement()
    f.get_doc.side_effect = lambda dt, name: application if dt == 'Loan Application' else row
    with pytest.raises(ValueError, match='Digital HP contract signing is not enabled'):
        getattr(helper, endpoint)()
    f.db.set_value.assert_not_called()
    f.db.commit.assert_not_called()
