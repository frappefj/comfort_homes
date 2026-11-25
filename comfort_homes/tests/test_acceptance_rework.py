"""Regressions for observed devcom findings. Not a Frappe/database/ledger certification."""
import ast
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from comfort_homes.tests.test_gross_controls import env, Row, COMPANY
from comfort_homes.tests.test_document_process import controls, doc, Doc, pdf, NOW
from comfort_homes.hp_lending.document_policy import LEGAL_DOCUMENT_CODES, scan_issues, pack_order_issues

ROOT = Path(__file__).resolve().parents[1]


def test_native_print_flag_no_longer_silently_skips_assessment_save(controls, monkeypatch):
    f, m = controls
    a = doc(custom_document_collection_complete='Yes', custom_hp_cira_signed_on=NOW)
    a.flags = SimpleNamespace(in_print=False)
    render = doc(); render.flags = SimpleNamespace(in_print=False)
    storage = {}
    def save(**kw):
        if a.flags.in_print: return a  # native Document._save guard observed in source
        storage.update({k:a.get(k) for k in m.ASSESSMENT_FIELDS})
        return a
    a.save = save
    f.db.get_value.side_effect = lambda *args, **kwargs: dict(storage)
    monkeypatch.setattr(m, '_application', lambda name:a)
    f.get_doc.return_value = render
    def print_pdf(*args, **kw):
        kw['doc'].flags.in_print = True
        return pdf()
    f.get_print = Mock(side_effect=print_pdf)
    monkeypatch.setattr(m, '_attach', lambda *args:Doc(file_url='/private/files/current.pdf'))
    assert m.generate_assessment_pdf('APP')['file_url']=='/private/files/current.pdf'
    assert render.flags.in_print and not a.flags.in_print
    assert storage['custom_hp_assessment_pdf']=='/private/files/current.pdf'
    assert storage['custom_hp_assessment_content_hash'] and storage['custom_hp_assessment_fingerprint']


def test_assessment_success_requires_database_persistence(controls):
    f,m=controls
    a=doc(custom_hp_assessment_pdf='/private/files/current.pdf')
    f.db.get_value.return_value={k:None for k in m.ASSESSMENT_FIELDS}
    with pytest.raises(ValueError,match='evidence was not saved'):
        m._save(a,'assessment')


def pairs():
    return {code:(Doc(name=code, document_code=code, status='Pending'),
                  Doc(requires_director_signature=int(code in {'HP-AGREEMENT','SCHEDULE-1','FINANCIAL-OBLIGATIONS'})))
            for code in LEGAL_DOCUMENT_CODES}


def setup_pack(m,monkeypatch):
    a=doc(workflow_state='Contract Signing');p=pairs();blob=pdf()
    monkeypatch.setattr(m,'_application',lambda *args,**kw:a)
    monkeypatch.setattr(m,'_legal_rows',lambda doc:p)
    loader=Mock(return_value=blob);monkeypatch.setattr(m,'_load_pdf',loader)
    monkeypatch.setattr(m,'_row_hash',lambda doc,template:'terms')
    saver=Mock();monkeypatch.setattr(m,'_save',saver)
    return a,p,loader,saver


def test_combined_pack_records_six_rows_once_and_same_day_is_valid(controls,monkeypatch):
    _,m=controls;a,p,loader,saver=setup_pack(m,monkeypatch)
    result=m.record_signed_pack('APP','/private/files/pack.pdf','2026-11-15','2026-11-15','Test Manager',1,1)
    assert result['recorded']==6 and result['requires_manager_verification']
    loader.assert_called_once();saver.assert_called_once_with(a,'pack',manager=False)
    assert {r.final_signed_file for r,t in p.values()}=={'/private/files/pack.pdf'}
    assert all(r.status=='Ready' and not r.custom_hp_verified_by for r,t in p.values())
    for r,t in p.values():
        assert r.custom_hp_customer_signed_date=='2026-11-15'
        assert r.custom_hp_manager_signed_date==('2026-11-15' if t.requires_director_signature else None)
    assert not hasattr(m.frappe,'delete_doc')


@pytest.mark.parametrize('changes',[{'confirm_all_forms':0},{'confirm_execution':0},
    {'manager_signed_date':'2026-11-14'},{'customer_signed_date':'2026-12-01'}, {'manager_name':''}])
def test_invalid_combined_pack_never_partially_updates_rows(controls,monkeypatch,changes):
    _,m=controls;a,p,loader,saver=setup_pack(m,monkeypatch)
    before={code:dict(row) for code,(row,t) in p.items()}
    args=dict(loan_application='APP',file_url='/private/files/a.pdf',customer_signed_date='2026-11-15',
              manager_signed_date='2026-11-15',manager_name='Manager',confirm_all_forms=1,confirm_execution=1)
    args.update(changes)
    with pytest.raises(ValueError):m.record_signed_pack(**args)
    assert {code:dict(row) for code,(row,t) in p.items()}==before
    saver.assert_not_called()


def test_combined_pack_cannot_overwrite_verified_evidence_without_reason(controls,monkeypatch):
    _,m=controls;a,p,_,save=setup_pack(m,monkeypatch)
    p['FORM-1'][0].status='Completed'
    with pytest.raises(ValueError,match='explicitly supersede'):
        m.record_signed_pack('APP','/private/files/a.pdf','2026-11-15','2026-11-15','M',1,1)
    save.assert_not_called()


def test_protected_rows_accept_only_scoped_pack_action(controls):
    _,m=controls;r=Doc(name='R',status='Ready',final_signed_file='/private/files/a.pdf')
    a=doc(custom_customer_documents=[r])
    with m._action(a,'pack'):m._restore_protected(a)
    assert r.final_signed_file=='/private/files/a.pdf'
    m._restore_protected(a)
    assert not r.final_signed_file and r.status=='Pending'


def test_missing_form_error_names_document_not_private_upload(controls,monkeypatch):
    _,m=controls;p=pairs()
    for code,(r,t) in p.items():
        if code!='FORM-2':r.final_signed_file='/private/files/a.pdf'
    monkeypatch.setattr(m,'_legal_rows',lambda a:p)
    loader=Mock();monkeypatch.setattr(m,'_load_pdf',loader)
    with pytest.raises(ValueError,match='Signed scan not recorded for: FORM-2'):
        m._check_scans(doc(),False)
    loader.assert_not_called()


def test_combined_endpoint_is_post_only_and_not_guest():
    tree=ast.parse((ROOT/'document_controls.py').read_text())
    n=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='record_signed_pack')
    deco=n.decorator_list[0]
    assert ast.literal_eval(next(k.value for k in deco.keywords if k.arg=='methods'))==['POST']
    assert not any(k.arg=='allow_guest' for k in deco.keywords)


def workflow(env):
    f,load=env
    return f,load('hp_lending/hp_workflow.py')


def application(target='LPO Pending Approval'):
    return Row(name='APP',company=COMPANY,applicant='CUSTOMER',status='Approved',docstatus=1,
               workflow_state=target,custom_flexi_total_receivable=879.60,custom_flexi_cash_price=849,
               get_doc_before_save=lambda:Row(workflow_state='Loan Activated'))


@pytest.mark.parametrize('target',['LPO Pending Approval','LPO Approved','LPO Sent to Supplier',
    'Delivered - Awaiting Documents','Active Loan Account','Closed'])
def test_post_activation_states_require_real_disbursement(env,monkeypatch,target):
    f,m=workflow(env)
    f.get_all.return_value=[Row(name='L')]
    f.get_doc.return_value=Row(name='L',applicant_type='Customer',applicant='CUSTOMER',docstatus=0,
        rate_of_interest=0,loan_amount=879.60,disbursed_amount=0)
    with pytest.raises(ValueError,match='Submit the linked native Loan'):
        m.validate_progress(application(target))


def test_legacy_same_state_save_is_not_silently_rewritten(env,monkeypatch):
    f,m=workflow(env);a=application();a.get_doc_before_save=lambda:Row(workflow_state=a.workflow_state)
    m.validate_progress(a);f.get_all.assert_not_called()


def test_unmanaged_company_not_gated(env):
    f,m=workflow(env);a=application();a.company='Other';m.validate_progress(a);f.get_all.assert_not_called()


def test_disbursement_requires_one_native_submission_and_booking_journal(env,monkeypatch):
    f,m=workflow(env);loan=Row(name='L',docstatus=1,rate_of_interest=0,loan_amount=879.60,disbursed_amount=879.60)
    monkeypatch.setattr(m,'_one_loan',lambda a:loan)
    f.get_all.return_value=[Row(name='D',disbursed_amount=879.60)]
    with pytest.raises(ValueError,match='gross-booking journal'):m._require_disbursed(application())
    f.db.exists.return_value=True
    assert m._require_disbursed(application()) is loan


def test_active_account_no_longer_waits_for_chd_invoice(env,monkeypatch):
    # dev16: CHD invoices come with the monthly statement; Active Loan Account does not wait for them.
    f,m=workflow(env)
    monkeypatch.setattr(m,'_require_disbursed',lambda a:Row(status='Disbursed'))
    monkeypatch.setattr(m,'_purchase_order',lambda a:Row(docstatus=1))
    f.get_all.return_value=[]
    m.validate_progress(application('Active Loan Account'))


def test_lpo_native_submit_is_role_checked_and_idempotent(env,monkeypatch):
    f,m=workflow(env);a=application('LPO Approved')
    po=Row(name='PO',docstatus=0,company=COMPANY,supplier=m.s.CHD_SUPPLIER,currency='FJD',
        custom_loan_application='APP',grand_total=849,reload=Mock(),check_permission=Mock())
    def submit():po.docstatus=1
    po.submit=Mock(side_effect=submit)
    monkeypatch.setattr(m,'_require_disbursed',Mock())
    monkeypatch.setattr(m,'_purchase_order',lambda a:po)
    m.submit_approved_lpo(a);m.submit_approved_lpo(a)
    po.submit.assert_called_once_with()
    assert any(c.args==('submit',) for c in po.check_permission.call_args_list)
    assert f.only_for.call_args.args==( ('Finance Manager','Director'), )
    assert f.db.commit.call_count==0


def test_lpo_denied_submit_cannot_be_bypassed(env,monkeypatch):
    f,m=workflow(env);a=application('LPO Approved')
    po=Row(name='PO',docstatus=0,company=COMPANY,supplier=m.s.CHD_SUPPLIER,currency='FJD',
        custom_loan_application='APP',grand_total=849,reload=Mock(),submit=Mock())
    def permission(kind):
        if kind=='submit':raise ValueError('denied')
    po.check_permission=permission
    monkeypatch.setattr(m,'_require_disbursed',Mock());monkeypatch.setattr(m,'_purchase_order',lambda a:po)
    with pytest.raises(ValueError,match='denied'):m.submit_approved_lpo(a)
    po.submit.assert_not_called()


def test_handoff_does_not_disclose_inaccessible_financial_docs(env):
    f,m=workflow(env);f.get_all.return_value=[Row(name='PRIVATE')];f.has_permission.return_value=False
    assert m._visible_docs('Loan',{},['status'])==[]


def test_both_lpo_approval_roles_have_native_submit_task_and_update_after_submit_gate():
    w=next(w for w in json.loads((ROOT/'fixtures/zz_workflow.json').read_text()) if w['document_type']=='Loan Application')
    approval=[t for t in w['transitions'] if t['action']=='Approve LPO']
    assert len(approval)==2 and {t['allowed'] for t in approval}=={'Finance Manager','Director'}
    assert all(t['transition_tasks']=='Flexi Approve LPO' for t in approval)
    scope={};exec(compile(ast.parse((ROOT/'hooks.py').read_text()),'<hooks>','exec'),scope)
    assert scope['doc_events']['Loan Application']['before_update_after_submit'].endswith('validate_progress')
    assert len(w['states'])==16 and len(w['transitions'])==30  # dev27: Withdrawn + 10 Withdraw transitions


def test_reclass_uses_actual_gl_posting_date_not_historical_value_date(env,monkeypatch):
    f,load=env;m=load('hp_lending/hp_lifecycle.py')
    loan=Row(company=COMPANY,name='L',loan_application='APP',applicant='CUSTOMER',cost_center='HQ')
    a=Row(name='APP',custom_flexi_total_receivable=879.60,custom_flexi_deposit_amount=170,
          custom_flexi_cash_price=849,custom_flexi_term_charges=158.62,custom_flexi_admin_fee=16.98,
          custom_flexi_doc_fee_net=22.22,custom_flexi_doc_fee_vat=2.78)
    je=Row(insert=Mock(),submit=Mock())
    captured=[]
    def get_doc(dt,*args):
        if isinstance(dt,dict):captured.append(dt);return je
        return loan if dt=='Loan' else a
    f.get_doc.side_effect=get_doc
    monkeypatch.setattr(m.s,'entry_exists',lambda *a:False)
    m.on_disbursement_submit(Row(against_loan='L',name='D',disbursed_amount=879.60,
        posting_date='2026-11-19',disbursement_date='2026-07-01'))
    assert captured[0]['posting_date']=='2026-11-19'
    assert sum(r.get('debit_in_account_currency',0) for r in captured[0]['accounts'])==pytest.approx(200.60)
    assert sum(r.get('credit_in_account_currency',0) for r in captured[0]['accounts'])==pytest.approx(200.60)
    je.submit.assert_called_once()


def test_ui_stage_visibility_and_native_handoff_under_node():
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const handlers={},calls=[];
const frappe={session:{user:'officer'},user_roles:['Loan Officer'],ui:{form:{on:(dt,h)=>handlers[dt]=h}},
    call:o=>{calls.push(o);return Promise.resolve({message:{loans:[{name:'L',docstatus:0,disbursed_amount:0}],disbursements:[],purchase_orders:[],deposit:{name:'PE',docstatus:0}}});},
    model:{can_create:()=>false},msgprint:()=>{},set_route:()=>{}};
const ctx={frappe,window:{},__:s=>s};vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
function form(state){return {doc:{name:'APP',docstatus:0,workflow_state:state,custom_document_collection_complete:'Yes'},doctype:'Loan Application',is_new:()=>false,is_dirty:()=>false,buttons:{},add_custom_button(t,f){this.buttons[t]=f;},dashboard:{set_headline_alert:()=>{}}};}
for(const s of ['Initiated','Under Review','Pending Approval','Approved']){const a=form(s);handlers['Loan Application'].refresh(a);assert.ok(a.buttons['Generate Assessment PDF']);assert.equal(a.buttons['Record Combined Signed Pack'],undefined);}
const a=form('Contract Signing');handlers['Loan Application'].refresh(a);assert.ok(a.buttons['Record Combined Signed Pack']);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),ctx);
const b=form('Loan Activated');handlers['Loan Application'].refresh(b);
setImmediate(()=>{assert.ok(b.buttons['Open Draft Loan — Submit Here']);assert.ok(b.buttons['Complete Draft Deposit']);assert.ok(calls.every(c=>c.method.endsWith('.financial_handoff')));console.log('ok');});
'''
    r=subprocess.run(['node','-e',script,str(ROOT/'public/js/document_evidence.js'),str(ROOT/'public/js/hp_workflow_handoff.js')],capture_output=True,text=True,check=True)
    assert r.stdout.strip()=='ok'


def test_approved_chart_and_reference_data_are_not_automatic_fixtures():
    import csv
    rows=list(csv.DictReader((ROOT/'setup_data/chart_of_accounts_flexi.csv').open()))
    by={r['Account Number']:r for r in rows}
    assert len(rows)==111 and by['1195']['Parent Account Number']=='5200'
    assert by['1195']['Root Type']=='Expense' and by['2010']['Account Type']=='Receivable'
    ref=json.loads((ROOT/'setup_data/approved_operational_reference.json').read_text())
    assert ref['reference_only'] and not ref['automatic_import']
    assert len(ref['locations'])==342 and len({r['province'] for r in ref['locations']})==9
    assert len({(r['province'],r['city']) for r in ref['locations']})==15
    assert len(ref['account_metadata'])==111
    assert not (ROOT/'fixtures/approved_operational_reference.json').exists()


def test_master_group_creation_uses_exact_native_parent_not_random_first_group(env):
    f,load=env;m=load('hp_lending/hp_setup.py')
    f.db.exists.side_effect=lambda dt,name:name=='All Supplier Groups'
    inserted=Row(insert=Mock());f.get_doc.return_value=inserted
    assert m._ensure_group('Supplier Group','Distributor','supplier_group_name',
                           'parent_supplier_group','All Supplier Groups')=='Distributor'
    data=f.get_doc.call_args.args[0]
    assert data=={'doctype':'Supplier Group','supplier_group_name':'Distributor',
                  'parent_supplier_group':'All Supplier Groups','is_group':0}
    inserted.insert.assert_called_once_with(ignore_permissions=True)


def test_existing_approved_master_group_is_not_recreated(env):
    f,load=env;m=load('hp_lending/hp_setup.py');f.db.exists.return_value=True
    m._ensure_group('Item Group','Products','item_group_name','parent_item_group','All Item Groups')
    f.get_doc.assert_not_called()


@pytest.mark.parametrize('change',[{'currency':'USD'},{'credit_to':'WRONG'},
    {'taxes':[Row(tax_amount=1)]},{'items':[Row(expense_account='WRONG')]}])
def test_active_account_checks_chd_invoice_accounting_not_only_total(env,monkeypatch,change):
    f,m=workflow(env)
    f.get_all.return_value=[Row(name='PI',grand_total=849)]
    invoice=Row(currency='FJD',credit_to='2111',taxes=[],items=[Row(expense_account='1180')]);invoice.update(change)
    f.get_doc.return_value=invoice
    monkeypatch.setattr(m.s,'account',lambda key,company:{'ACC_CHD_PAYABLE':'2111','ACC_CLEARING':'1180'}[key])
    with pytest.raises(ValueError,match='clear 1180 to 2111'):m._require_invoice(application())


def test_native_submit_and_create_controls_are_not_hidden_or_replaced_by_new_asset():
    src=(ROOT/'public/js/hp_workflow_handoff.js').read_text()
    assert 'frappe.set_route("Form", "Loan"' in src
    assert '.hide()' not in src and 'frappe.client.submit' not in src
    assert '.financial_handoff' in src and 'Open Draft Loan' in src
    api=(ROOT/'api.py').read_text();start=api.index('def create_deposit_payment_entry(');end=api.index('\n\n@frappe.whitelist',start)
    body=api[start:end]
    assert body.index('FOR UPDATE')<body.index('application.reload()')<body.index('existing = application.get(')


def test_test_rollout_preserves_email_suppression_and_single_deposit_button_owner():
    w=next(w for w in json.loads((ROOT/'fixtures/zz_workflow.json').read_text()) if w['document_type']=='Loan Application')
    assert w['send_email_alert']==0
    owners=[]
    for p in (ROOT/'public/js').glob('*.js'):
        if 'comfort_homes.api.create_deposit_payment_entry' in p.read_text():owners.append(p.name)
    assert owners==['hp_workflow_handoff.js']


def test_pinned_native_observations_have_provenance_and_no_runtime_native_patch():
    ref=json.loads((ROOT/'tests/reference/dev7_native_behaviour.json').read_text())
    assert len(ref)==4 and all(len(v['source_sha256'])==64 and v['excerpts'] for v in ref.values())
    assert 'if self.flags.in_print:' in json.dumps(ref)
    assert 'self.posting_date = nowdate()' in json.dumps(ref)
    hooks=(ROOT/'hooks.py').read_text()
    assert 'override_whitelisted_methods =' not in '\n'.join(l for l in hooks.splitlines() if not l.strip().startswith('#'))
    assert 'lending.telemetry' not in hooks
