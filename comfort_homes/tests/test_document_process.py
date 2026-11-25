"""Confirmed profile/consent/paper controls. Pure, mocked and parsed-PDF tests only.

Not a Frappe migration, live workflow, handwriting, identity or PDF-render test.
"""
import ast
import base64
from datetime import date, datetime
from io import BytesIO
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest
from pypdf import PdfWriter

from comfort_homes.tests.test_gross_controls import Row, env, COMPANY
from comfort_homes.hp_lending.document_policy import (
    LEGAL_DOCUMENT_CODES, DIGITAL_CONTRACT_SIGNING_ENABLED, fingerprint,
    consent_complete, scan_issues, pack_order_issues,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 17, 12)
SIGNATURE = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\nsignature-test-placeholder').decode()


class Doc(Row):
    def as_dict(self): return dict(self)


def doc(previous=None, **kwargs):
    result = Doc(name='APP', doctype='Loan Application', company=COMPANY, applicant_name='Test Client',
        workflow_state='Draft', docstatus=0, custom_cira_authority_acknowledged=1,
        custom_cira_authority_signature=SIGNATURE, custom_cira_authority_name='Test Client',
        meta=SimpleNamespace(has_field=lambda name: True), get_doc_before_save=lambda: previous,
        check_permission=Mock(), save=Mock(), add_comment=Mock())
    result.update(kwargs)
    return result


@pytest.fixture
def controls(env, monkeypatch):
    f, load = env
    f.utils.now_datetime = lambda: NOW
    f.get_meta = Mock(return_value=SimpleNamespace(get_field=lambda field: SimpleNamespace(options='Consent wording')))
    f.session = SimpleNamespace(user='reviewer@example.invalid')
    module = load('document_controls.py')
    monkeypatch.setattr(module, '_profiles', lambda application: [])
    return f, module


def test_cira_capture_uses_server_time_not_supplied_or_application_date(controls):
    _, m = controls
    a = doc(custom_hp_cira_signed_on='2000-01-01', application_date='2001-01-01')
    m._capture_cira(a)
    assert a.custom_hp_cira_signed_on == NOW and consent_complete(a)
    assert a.custom_hp_cira_fingerprint


def test_unchanged_cira_keeps_original_capture_time(controls):
    _, m = controls
    a = doc(); m._capture_cira(a)
    old_time = datetime(2026, 11, 1, 9)
    a.custom_hp_cira_signed_on = old_time
    b = doc(previous=a); m._capture_cira(b)
    assert b.custom_hp_cira_signed_on == old_time


def test_unknown_historical_capture_is_not_invented(controls):
    _, m = controls
    with pytest.raises(ValueError, match='Historical CIRA'):
        m._capture_cira(doc(previous=doc()))


def test_changed_signer_cannot_reuse_same_signature(controls):
    _, m = controls
    a = doc(); m._capture_cira(a)
    with pytest.raises(ValueError, match='fresh client consent'):
        m._capture_cira(doc(previous=a, custom_cira_authority_name='Different Person'))


@pytest.mark.parametrize('signature', ['https://external.invalid/picture', 'data:text/html;base64,YWJj', 'data:image/png;base64,!!!!'])
def test_cira_rejects_remote_or_non_png_signature_input(controls, signature):
    _, m = controls
    with pytest.raises(ValueError, match='captured PNG'):
        m._capture_cira(doc(custom_cira_authority_signature=signature))


def test_cira_revocation_clears_proof_and_active_pdf_but_does_not_delete_files(controls):
    f, m = controls
    a = doc(custom_cira_authority_acknowledged=0, custom_cira_authority_pdf='/private/files/old.pdf')
    m._capture_cira(a)
    assert a.custom_hp_cira_signed_on is None and a.custom_cira_authority_pdf is None
    assert not hasattr(f, 'delete_doc')


def test_individual_cira_name_must_match(controls):
    _, m = controls
    a = doc(custom_hp_cira_signed_on=NOW, custom_cira_authority_name='Another Person')
    with pytest.raises(ValueError, match='match the individual'):
        m._require_cira(a)


def test_kyc_initiation_cannot_skip_cira(controls):
    _, m = controls
    with pytest.raises(ValueError, match='before KYC initiation'):
        m.validate_application(doc(previous=doc(), workflow_state='Initiated', custom_cira_authority_acknowledged=0))


def test_public_submission_requires_cira_even_if_still_draft(controls):
    f, m = controls
    f.flags.in_web_form = True
    with pytest.raises(ValueError, match='before KYC initiation'):
        m.validate_application(doc(custom_cira_authority_acknowledged=0))


def test_profile_approval_records_fingerprint_only_after_kyc_and_role_check(controls):
    f, m = controls
    old = doc(doctype='Co-Applicant', workflow_state='Pending Approval'); m._capture_cira(old)
    a = doc(previous=old, doctype='Co-Applicant', workflow_state='Approved', custom_document_collection_complete='Yes')
    m.validate_profile(a)
    assert a.custom_hp_profile_review_fingerprint == fingerprint(m._profile_data(a))
    f.only_for.assert_called_once_with(('Finance Manager', 'Director'))


def test_profile_approval_denied_before_kyc_complete(controls):
    _, m = controls
    old = doc(workflow_state='Pending Approval'); m._capture_cira(old)
    with pytest.raises(ValueError, match='co-applicant KYC'):
        m.validate_profile(doc(previous=old, doctype='Co-Applicant', workflow_state='Approved'))


def test_profile_edit_after_approval_requires_renewed_review(controls, monkeypatch):
    _, m = controls
    p = doc(doctype='Co-Applicant', workflow_state='Approved', custom_hp_cira_signed_on=NOW)
    p.custom_hp_profile_review_fingerprint = fingerprint(m._profile_data(p))
    p.custom_flexi_employer_name = 'New Employer'
    monkeypatch.setattr(m, '_profiles', lambda a: [p])
    with pytest.raises(ValueError, match='renew the profile review of co-applicant'):
        m._require_profiles(doc())


def test_proof_fields_cannot_be_injected_through_parent_save(controls):
    _, m = controls
    a = doc(custom_hp_assessment_pdf='/private/files/fake.pdf', custom_hp_assessment_fingerprint='fake',
        custom_cira_authority_pdf='/private/files/fake-cira.pdf')
    m._restore_protected(a)
    assert a.custom_hp_assessment_pdf is None and a.custom_cira_authority_pdf is None
    row = Doc(name='ROW', status='Completed', custom_hp_verified_by='fake', final_signed_file='fake')
    a.custom_customer_documents = [row]; m._restore_protected(a)
    assert row.status == 'Pending' and not row.custom_hp_verified_by and not row.final_signed_file


def test_scoped_action_allows_only_its_own_evidence_change_and_resets(controls):
    _, m = controls
    a = doc(custom_hp_assessment_pdf='/private/files/current.pdf', custom_cira_authority_pdf='fake')
    with m._action(a, 'assessment'):
        m._restore_protected(a)
    assert a.custom_hp_assessment_pdf == '/private/files/current.pdf' and a.custom_cira_authority_pdf is None
    assert m._ACTION.get() is None


def test_direct_child_save_cannot_claim_verified_evidence(controls):
    _, m = controls
    child = doc(doctype='Loan Customer Agreement', parenttype='Loan Application', status='Completed')
    with pytest.raises(ValueError, match='direct child edit'):
        m.validate_agreement(child)


def test_recorded_rows_cannot_be_removed_or_reassigned(controls):
    _, m = controls
    row = Doc(name='ROW', final_signed_file='/private/files/old.pdf', document_template='T1', document_code='FORM-1')
    old = doc(custom_customer_documents=[row])
    with pytest.raises(ValueError, match='Keep recorded signature rows'):
        m._restore_protected(doc(previous=old))
    changed = Doc(**{**row, 'document_template': 'T2'})
    with pytest.raises(ValueError, match='different legal document'):
        m._restore_protected(doc(previous=old, custom_customer_documents=[changed]))


def pdf(pages=1, encrypt=False):
    writer = PdfWriter()
    for _ in range(pages): writer.add_blank_page(width=100, height=100)
    if encrypt: writer.encrypt('not-a-real-secret')
    out = BytesIO(); writer.write(out)
    return out.getvalue()


def attached(f, content):
    f.db.get_value.return_value = 'FILE'
    file = Doc(check_permission=Mock(), get_content=Mock(return_value=content))
    f.get_doc.return_value = file
    return file


def test_real_pdf_parser_accepts_private_attached_unencrypted_pdf(controls):
    f, m = controls
    blob = pdf(); file = attached(f, blob)
    assert m._load_pdf(doc(), '/private/files/scan.pdf') == blob
    file.check_permission.assert_called_once_with('read')
    filters = f.db.get_value.call_args.args[1]
    assert filters['attached_to_doctype'] == 'Loan Application' and filters['attached_to_name'] == 'APP'
    assert filters['is_private'] == 1


@pytest.mark.parametrize('blob', [b'<html>not pdf</html>', b'%PDF-corrupt', pdf(0), pdf(encrypt=True), pdf(201)])
def test_pdf_parser_rejects_fake_corrupt_empty_encrypted_or_too_many_pages(controls, blob):
    f, m = controls; attached(f, blob)
    with pytest.raises(ValueError, match='PDF'):
        m._load_pdf(doc(), '/private/files/scan.pdf')


def test_public_or_unattached_pdf_is_rejected(controls):
    f, m = controls
    with pytest.raises(ValueError, match='private uploaded'):
        m._load_pdf(doc(), 'https://external.invalid/test.pdf')
    f.db.get_value.return_value = None
    with pytest.raises(ValueError, match='not privately attached'):
        m._load_pdf(doc(), '/private/files/other-app.pdf')


def scan(**changes):
    result = Doc(document_code='HP-AGREEMENT', custom_hp_contract_fingerprint='terms', custom_hp_signed_scan_hash='blob',
        custom_hp_execution_attested=1, custom_hp_customer_signed_date='2026-11-15',
        custom_hp_manager_signed_date='2026-11-16', director_name='Test Manager')
    result.update(changes); return result


def test_wet_scan_policy_accepts_declared_customer_then_manager():
    assert scan_issues(scan(), 'terms', 'blob', date(2026, 11, 17), True) == []


@pytest.mark.parametrize('changes', [
    {'custom_hp_customer_signed_date':'2026-12-01'}, {'custom_hp_manager_signed_date':'2026-11-14'},
    {'custom_hp_execution_attested':0}, {'director_name':''}, {'custom_hp_contract_fingerprint':'changed'},
    {'custom_hp_signed_scan_hash':'changed'}, {'custom_hp_customer_signed_date':'bad-date'},
])
def test_wet_scan_policy_rejects_bad_dates_missing_attestation_or_changed_evidence(changes):
    assert scan_issues(scan(**changes), 'terms', 'blob', date(2026, 11, 17), True)


def test_statutory_form_does_not_gain_an_unapproved_manager_signature_requirement():
    assert scan_issues(scan(custom_hp_manager_signed_date=None, director_name=None), 'terms', 'blob', date(2026,11,17), False) == []


def test_same_day_order_relies_on_human_attestation_not_invented_times():
    assert scan_issues(scan(custom_hp_manager_signed_date='2026-11-15'), 'terms', 'blob', date(2026,11,17), True) == []
    assert pack_order_issues([scan(), scan(document_code='FORM-1', custom_hp_customer_signed_date='2026-11-17')], {'HP-AGREEMENT'})


def test_assessment_stale_inputs_or_changed_content_blocks_approval(controls, monkeypatch):
    _, m = controls
    monkeypatch.setattr(m, '_assessment_hash', lambda a: 'current')
    a = doc(custom_hp_assessment_pdf='/private/files/a.pdf', custom_hp_assessment_fingerprint='old')
    with pytest.raises(ValueError, match='current Application Print WIP'): m._require_assessment(a)
    a.custom_hp_assessment_fingerprint = 'current'
    monkeypatch.setattr(m, '_load_pdf', lambda *a: b'changed')
    a.custom_hp_assessment_content_hash = hashlib.sha256(b'old').hexdigest()
    with pytest.raises(ValueError, match='content changed'): m._require_assessment(a)


def test_assessment_cannot_generate_before_kyc(controls, monkeypatch):
    _, m = controls
    monkeypatch.setattr(m, '_application', lambda name: doc())
    with pytest.raises(ValueError, match='Complete KYC'):
        m.generate_assessment_pdf('APP')


def test_manual_assessment_requires_explicit_current_print_attestation(controls, monkeypatch):
    _, m = controls
    monkeypatch.setattr(m, '_application', lambda name: doc(custom_document_collection_complete='Yes'))
    with pytest.raises(ValueError, match='attest'):
        m.record_assessment_pdf('APP', '/private/files/a.pdf')


def test_scan_record_and_manager_verification_are_separate(controls, monkeypatch):
    f, m = controls
    a = doc(workflow_state='Contract Signing')
    row = Doc(name='ROW', status='Pending'); template=Doc(requires_director_signature=1)
    monkeypatch.setattr(m, '_application', lambda *a, **kw: globals()['doc'](workflow_state='Contract Signing'))
    monkeypatch.setattr(m, '_legal_rows', lambda a: {'HP-AGREEMENT':(row,template)})
    monkeypatch.setattr(m, '_load_pdf', lambda *a: pdf())
    monkeypatch.setattr(m, '_row_hash', lambda *a: 'terms')
    save = Mock(); monkeypatch.setattr(m, '_save', save)
    result = m.record_signed_scan('APP','ROW','/private/files/a.pdf','2026-11-15','2026-11-16','Manager',1)
    assert result['requires_manager_verification'] and row.status == 'Ready'
    assert row.custom_hp_verified_by is None and row.custom_hp_verified_on is None


def test_completed_scan_requires_explicit_supersession_reason(controls, monkeypatch):
    _, m = controls
    monkeypatch.setattr(m, '_application', lambda *a, **kw: doc(workflow_state='Contract Signing'))
    row = Doc(name='ROW',status='Completed'); template=Doc(requires_director_signature=1)
    monkeypatch.setattr(m, '_legal_rows', lambda a: {'HP-AGREEMENT':(row,template)})
    with pytest.raises(ValueError, match='explicitly supersede'):
        m.record_signed_scan('APP','ROW','/private/files/a.pdf','2026-11-15')


def test_manager_verification_sets_evidence_review_not_digital_signatures(controls, monkeypatch):
    _, m = controls
    a=doc(workflow_state='Contract Signing'); row=Doc(name='ROW',status='Ready')
    application = Mock(return_value=a); monkeypatch.setattr(m,'_application',application)
    for name in ['_require_cira','_require_profiles','_require_assessment']: monkeypatch.setattr(m,name,Mock())
    monkeypatch.setattr(m,'_check_scans',lambda *a,**kw:[row])
    save=Mock(); monkeypatch.setattr(m,'_save',save)
    assert m.verify_contract_pack('APP')['verified'] == 1
    application.assert_called_once_with('APP',manager=True)
    assert row.status == 'Completed' and row.custom_hp_verified_by == 'reviewer@example.invalid'
    assert row.custom_hp_verified_on == NOW and not row.get('director_signature')
    save.assert_called_once_with(a,'verify',manager=True)


def test_six_distinct_legal_templates_are_required(controls):
    _, m=controls
    with pytest.raises(ValueError,match='All six'): m._legal_rows(doc())


def test_fingerprint_covers_employer_and_all_child_input_columns(controls):
    _, m=controls
    a=doc(); first=m._assessment_hash(a)
    a.custom_flexi_employer_name='Changed'; assert m._assessment_hash(a)!=first
    first=m._assessment_hash(a)
    a.custom_flexi_income_entries=[Doc(amount=100,frequency='Weekly',other_eligibility_input='new')]
    assert m._assessment_hash(a)!=first
    first=m._assessment_hash(a)
    a.custom_flexi_income_entries[0].name='new-row-id'
    assert m._assessment_hash(a)==first


def test_row_fingerprint_covers_template_code_and_print_content(controls):
    f,m=controls
    fmt=Doc(html='<p>Version one</p>'); f.get_doc.return_value=fmt
    template=Doc(name='T',document_code='FORM-1',print_format='Form 1',requires_director_signature=0)
    a=doc(); first=m._row_hash(a,template)
    fmt.html='<p>Revised wording</p>'; assert m._row_hash(a,template)!=first
    fmt.html='<p>Version one</p>'; template.document_code='FORM-2'
    assert m._row_hash(a,template)!=first


def events():
    tree=ast.parse((ROOT/'hooks.py').read_text())
    return ast.literal_eval(next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='doc_events' for t in n.targets)))


def test_final_controls_follow_origination_finalization_on_save_and_submit():
    e=events()
    callbacks=['comfort_homes.loan_application_workflow.calculate_financials','comfort_homes.document_controls.validate_application', 'comfort_homes.hp_lending.hp_workflow.validate_progress', 'comfort_homes.hp_lending.hp_approval.bind_approval']
    assert e['Loan Application']['before_save']==callbacks==e['Loan Application']['before_submit']
    assert e['Co-Applicant']['before_save']==['comfort_homes.origination.apply_profile','comfort_homes.hp_lending.hp_intake.validate_locations', 'comfort_homes.document_controls.validate_profile']
    assert e['Loan Customer Agreement']['before_save'].endswith('validate_agreement')
    source=(ROOT/'hp_lending/hp_lifecycle.py').read_text()
    assert 'validate_signed_pack(application)' in source


def test_profile_workflow_has_no_financial_tasks_or_submit_states():
    workflows=json.loads((ROOT/'fixtures/zz_workflow.json').read_text())
    assert len(workflows)==2
    profile=next(w for w in workflows if w['document_type']=='Co-Applicant')
    assert {s['state'] for s in profile['states']}=={'Draft','Initiated','Under Review','Pending Approval','Approved','Rejected'}
    assert all(str(s['doc_status'])=='0' for s in profile['states'])
    assert all(not t.get('transition_tasks') and not t.get('condition') for t in profile['transitions'])
    schema=json.loads((ROOT/'fixtures/00_co_applicant_schema.json').read_text())[0]
    assert schema['is_submittable']==0 and schema['track_changes']==1
    scripts=json.loads((ROOT/'fixtures/server_script.json').read_text())
    financial=[s for s in scripts if s.get('reference_doctype')=='Co-Applicant' and 'PO' in s['name']]
    assert len(financial)==2 and all(s['disabled'] for s in financial)


def test_digital_contract_mutation_apis_are_gated_but_cira_is_retained():
    assert DIGITAL_CONTRACT_SIGNING_ENABLED is False
    records=json.loads((ROOT/'fixtures/server_script.json').read_text())
    names={'FLEXI DOCUMENT CUSTOMER SIGN','FLEXI DOCUMENT DIRECTOR SIGN','HP AGREEMENT CUSTOMER SIGN',
           'HP AGREEMENT DIRECTOR SIGN','HIRE PURCHASE AGREEMENT','form1_print','form2_print','schedule1_print'}
    for r in records:
        if r['name'] in names: assert 'contract_signing=1' in r['script'].splitlines()[0]
    assert next(r for r in records if r['name']=='Attach signed CIRA Authority PDF')['disabled']
    fmt=next(r for r in json.loads((ROOT/'fixtures/print_format.json').read_text()) if r['name']=='CIRA Authority Form')
    assert 'doc.custom_hp_cira_signed_on' in fmt['html'] and 'doc.application_date' not in fmt['html']


def test_all_new_mutation_endpoints_are_post_only_without_guest_access():
    tree=ast.parse((ROOT/'document_controls.py').read_text())
    names={'generate_cira_pdf','generate_assessment_pdf','record_assessment_pdf','record_signed_scan','verify_contract_pack'}
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    assert len(nodes)==5
    for node in nodes:
        decorator=node.decorator_list[0]
        assert ast.literal_eval(next(k.value for k in decorator.keywords if k.arg=='methods'))==['POST']
        assert not any(k.arg=='allow_guest' for k in decorator.keywords)


def test_cira_pdf_api_uses_individual_saved_record_and_private_attachment(controls, monkeypatch):
    from types import ModuleType
    f,m=controls
    a=doc(custom_hp_cira_signed_on=NOW)
    template=Doc(html='Individual CIRA {{ doc.applicant_name }}')
    f.get_doc.side_effect=lambda dt,name: a if dt=='Loan Application' else template
    f.render_template=Mock(return_value='<p>Individual signed consent</p>')
    native_pdf=ModuleType('frappe.utils.pdf'); native_pdf.get_pdf=Mock(return_value=pdf())
    monkeypatch.setitem(sys.modules,'frappe.utils.pdf',native_pdf)
    attachment=Mock(return_value=Doc(file_url='/private/files/cira.pdf'))
    save=Mock(); monkeypatch.setattr(m,'_attach',attachment);monkeypatch.setattr(m,'_save',save)
    assert m.generate_cira_pdf('Loan Application','APP')=={'file_url':'/private/files/cira.pdf'}
    f.render_template.assert_called_once_with(template.html,{'doc':a})
    a.check_permission.assert_called_once_with('write')
    save.assert_called_once_with(a,'cira_pdf')
    assert a.custom_cira_authority_pdf=='/private/files/cira.pdf'
    assert 'FOR UPDATE' in f.db.sql.call_args.args[0]


def test_assessment_generated_pdf_records_content_and_inputs(controls,monkeypatch):
    f,m=controls
    a=doc(custom_document_collection_complete='Yes',custom_hp_cira_signed_on=NOW)
    monkeypatch.setattr(m,'_application',lambda name:a)
    blob=pdf(); f.get_print=Mock(return_value=blob)
    monkeypatch.setattr(m,'_attach',lambda *args:Doc(file_url='/private/files/assessment.pdf'))
    monkeypatch.setattr(m,'_save',Mock())
    result=m.generate_assessment_pdf('APP')
    assert result['file_url']=='/private/files/assessment.pdf'
    assert a.custom_hp_assessment_content_hash==hashlib.sha256(blob).hexdigest()
    assert a.custom_hp_assessment_fingerprint==m._assessment_hash(a)
    assert f.get_print.call_args.kwargs['print_format']=='Application Print WIP'
    assert f.get_print.call_args.kwargs['as_pdf'] is True


def test_compliance_requires_verified_pack_and_not_only_uploaded_files(controls,monkeypatch):
    _,m=controls
    for name in ['_require_cira','_require_profiles','_require_assessment']:monkeypatch.setattr(m,name,Mock())
    row=scan(name='ROW',final_signed_file='/private/files/scan.pdf',status='Ready')
    template=Doc(requires_director_signature=1)
    monkeypatch.setattr(m,'_legal_rows',lambda a:{'HP-AGREEMENT':(row,template)})
    blob=pdf();row.custom_hp_signed_scan_hash=hashlib.sha256(blob).hexdigest()
    monkeypatch.setattr(m,'_load_pdf',lambda *a:blob)
    monkeypatch.setattr(m,'_row_hash',lambda *a:'terms')
    with pytest.raises(ValueError,match='Manager verification'):
        m.validate_signed_pack(doc())
