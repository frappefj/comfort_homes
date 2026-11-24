"""Source/local executable regressions. NOT native site/ledger acceptance."""
import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from comfort_homes.tests.test_gross_controls import env, Row, COMPANY

ROOT=Path(__file__).resolve().parents[1]


def functions(names,namespace):
    tree=ast.parse((ROOT/'webform_setup.py').read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    namespace.update(Path=Path,__file__=str(ROOT/'webform_setup.py'))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'setup-transform','exec'),namespace)
    return namespace


def test_full_generated_webform_is_idempotent_and_single_submit_owner():
    script=json.loads((ROOT/'fixtures/web_form.json').read_text())[0]['client_script']
    upgrade=functions(['_upgrade_dev8_script'],{})['_upgrade_dev8_script']
    assert upgrade(script)==script
    assert script.count('/* comfort_homes: unified submit coordinator dev9 */')==1
    assert 'button.click();' not in script
    assert script.count('function bindFieldChecks(')==1
    assert script.count('function bindImmediateInputChecks(')==1
    assert 'async async' not in script
    assert 'field.grid.frm.doc[field.grid.df.fieldname] = rows' in script
    assert 'refreshAddressDropdowns' in script and 'hp_intake.locations' in script


def fee_doc(**values):
    d=Row(name='NEW-SI',company=COMPANY,custom_hp_independent_fee=1,custom_hp_reference_loan='L',custom_hp_reference_application='APP',
        customer='C',currency='FJD',conversion_rate=1,docstatus=0,posting_date='2026-11-20',value_date='2026-11-20',
        custom_hp_fee_event='EVENT-1',custom_hp_fee_event_date='2026-11-20',custom_hp_fee_evidence='Reviewed actual synthetic event',
        custom_hp_fee_authorised=1,disable_rounded_total=1,debit_to='1120',
        items=[Row(item_code='SMS Charge',qty=1,rate=2.5,income_account='4011')],
        taxes=[Row(charge_type='On Net Total',account_head='2310',rate=12.5,included_in_print_rate=1)],
        grand_total=2.5,net_total=2.22,total_taxes_and_charges=.28)
    d.update(values);return d


def fee_env(env,monkeypatch):
    f,load=env;m=load('hp_lending/hp_service_fees.py')
    monkeypatch.setattr(m,'account',lambda n:n)
    f.db.get_value.return_value=1;f.db.exists.return_value=False
    loan=Row(company=COMPANY,applicant_type='Customer',applicant='C',loan_application='APP',check_permission=Mock())
    f.get_doc.return_value=loan
    return f,m,loan


@pytest.mark.parametrize('item,rate,income,net,tax',[('SMS Charge',2.5,'4011',2.22,.28),('Call Charge',2.5,'4011',2.22,.28),('Field Visit',25,'4008',22.22,2.78)])
def test_independent_fee_exact_vip_rules_and_no_loan_writes(env,monkeypatch,item,rate,income,net,tax):
    f,m,loan=fee_env(env,monkeypatch);d=fee_doc(items=[Row(item_code=item,qty=1,rate=rate,income_account=income)],grand_total=rate,net_total=net,total_taxes_and_charges=tax)
    before={k:v for k,v in loan.items() if k!="check_permission"};m.validate(d)
    assert {k:v for k,v in loan.items() if k!="check_permission"}==before
    f.db.commit.assert_not_called()
    assert all(c.args[0]=='Loan' for c in f.get_doc.call_args_list)


@pytest.mark.parametrize('field,value,match',[
    ('loan','L','native Loan'),('loan_disbursement','D','native Loan'),('loan_partner','P','native Loan'),
    ('loan_repayment','R','native Loan'),('against_voucher','L','native Loan'),
    ('value_date',None,'Value Date'),('value_date','2026-11-19','Value Date'),
    ('custom_hp_fee_event','','unique service event'),('custom_hp_fee_evidence','','unique service event'),
    ('custom_hp_fee_event_date','2026-11-21','truthful event'),('disable_rounded_total',0,'exact cents'),
    ('currency','USD','FJD'),('conversion_rate',2,'exchange rate'),('custom_hp_independent_fee',0,'independent fee'),
    ('discount_amount',1,'Stock/POS'),('is_pos',1,'Stock/POS'),('update_stock',1,'Stock/POS'),
    ('debit_to','1101','1120'),('grand_total',3,'VAT cents'),('net_total',2.5,'VAT cents')])
def test_independent_fee_rejects_unsafe_entry_paths(env,monkeypatch,field,value,match):
    _,m,_=fee_env(env,monkeypatch)
    with pytest.raises(ValueError,match=match):m.validate(fee_doc(**{field:value}))


@pytest.mark.parametrize('changes',[
    {'items':[Row(item_code='SMS Charge',qty=2,rate=2.5)]},
    {'items':[Row(item_code='SMS Charge',qty=1,rate=2.51)]},
    {'items':[Row(item_code='SMS Charge',qty=1,rate=2.5),Row(item_code='Call Charge',qty=1,rate=2.5)]},
    {'taxes':[]},{'taxes':[Row(charge_type='On Net Total',account_head='2310',rate=15,included_in_print_rate=1)]}])
def test_fee_wrong_rate_or_tax_or_mixed_lines_fail(env,monkeypatch,changes):
    _,m,_=fee_env(env,monkeypatch)
    with pytest.raises(ValueError):m.validate(fee_doc(**changes))


def test_fee_disabled_default_and_explicit_submit_attestation(env,monkeypatch):
    f,m,_=fee_env(env,monkeypatch);f.db.get_value.return_value=0
    with pytest.raises(ValueError,match='disabled'):m.validate(fee_doc())
    f.db.get_value.return_value=1
    with pytest.raises(ValueError,match='attest'):m.before_submit(fee_doc(custom_hp_fee_authorised=0))
    f.only_for.assert_called_with(('Finance Manager','Director'))


def test_fee_duplicate_reserves_event_even_if_old_invoice_cancelled(env,monkeypatch):
    f,m,_=fee_env(env,monkeypatch);f.db.exists.return_value='OLD'
    with pytest.raises(ValueError,match='already has'):m.validate(fee_doc())
    assert 'FOR UPDATE' in f.db.sql.call_args.args[0]
    assert 'docstatus' not in f.db.exists.call_args.args[1]


@pytest.mark.parametrize('changes',[{'company':'OTHER'},{'applicant':'OTHER'},{'applicant_type':'Employee'},{'loan_application':'WRONG'}])
def test_fee_reference_customer_company_application_binding(env,monkeypatch,changes):
    _,m,loan=fee_env(env,monkeypatch);loan.update(changes)
    with pytest.raises(ValueError):m.validate(fee_doc())


def test_independent_credit_cannot_correct_legacy_invoice(env,monkeypatch):
    f,m,loan=fee_env(env,monkeypatch);legacy=Row(custom_hp_independent_fee=0,check_permission=Mock())
    f.get_doc.side_effect=lambda dt,name:loan if dt=='Loan' else legacy
    d=fee_doc(is_return=1,return_against='LEGACY',items=[Row(item_code='SMS Charge',qty=-1,rate=2.5,income_account='4011')],grand_total=-2.5,net_total=-2.22,total_taxes_and_charges=-.28)
    with pytest.raises(ValueError,match='legacy'):m.validate(d)


def test_fee_receipt_is_invoice_only_not_emi_or_deposit(env,monkeypatch):
    f,m,_=fee_env(env,monkeypatch);invoice=Row(custom_hp_independent_fee=1,docstatus=1,company=COMPANY,customer='C',check_permission=Mock());f.get_doc.return_value=invoice
    d=Row(company=COMPANY,payment_type='Receive',party_type='Customer',party='C',paid_from='1120',references=[Row(reference_doctype='Sales Invoice',reference_name='SI')])
    m.validate_receipt(d)
    d.references.append(Row(reference_doctype='Loan',reference_name='L'))
    with pytest.raises(ValueError,match='separate'):m.validate_receipt(d)


def test_rollout_document_types_create_missing_only_no_migration_apply(env):
    f,load=env;m=load('hp_lending/hp_rollout.py')
    f.get_all.return_value=[]
    plans=m.preview();assert len(plans)==6 and all(p['action']=='create' for p in plans)
    assert {p['values']['loan_document_type'] for p in plans}==set(m.DOCUMENT_TYPES)
    assert 'ID' not in m.DOCUMENT_TYPES
    f.get_all.return_value=['EXISTING'];f.get_doc.return_value=Row(loan_document_type='DIFFERENT')
    assert all(p['action']=='conflict' for p in m.preview())
    assert 'setup_rollout' not in (ROOT/'webform_setup.py').read_text()


def test_rollout_stops_inventory_change_where_stock_ledger_exists(env):
    f,load=env;m=load('hp_lending/hp_rollout.py');f.get_all.return_value=[]
    f.get_doc.return_value=Row(enable_perpetual_inventory=1);f.db.exists.return_value=True
    assert any(p['action']=='conflict' and 'Stock ledger' in p.get('reason','') for p in m.preview(include_company=True))
    with pytest.raises(ValueError,match='conflicts'):m.setup_rollout(0,0,1)


def test_location_guest_endpoint_is_allowlisted_and_parent_filtered(env):
    f,load=env;m=load('hp_lending/hp_intake.py')
    assert m.locations('City',None)==[];f.get_all.assert_not_called()
    f.get_all.return_value=[Row(name='CITY-ID',city_name='Same Label')]
    assert m.locations('City','PROVINCE-ID')==[{'value':'CITY-ID','label':'Same Label'}]
    assert f.get_all.call_args.kwargs['filters']=={'country':'Fiji','province':'PROVINCE-ID'}
    with pytest.raises(ValueError):m.locations('Customer','anything')


def test_location_server_rejects_cross_parent_id(env):
    f,load=env;m=load('hp_lending/hp_intake.py')
    f.db.get_value.side_effect=[Row(country='Fiji',province='Ba Province',is_active=1),Row(country='Fiji',province='OTHER',city_name='City')]
    with pytest.raises(ValueError,match='City must'):m.validate_locations(Row(company=COMPANY,custom_province='P',custom_new_city='C'))


def test_fee_configuration_disabled_and_no_trigger_fields_in_native_draft_source():
    fields=json.loads((ROOT/'fixtures/custom_field.json').read_text());by={r['name']:r for r in fields}
    assert by['Company-custom_hp_independent_fees_enabled']['default']=='0'
    assert by['Sales Invoice-custom_hp_fee_event']['unique']==1
    for k in ['custom_hp_reference_loan','custom_hp_reference_application']:assert by['Sales Invoice-'+k]['no_copy']==1
    tree=ast.parse((ROOT/'hp_lending/hp_service_fees.py').read_text())
    for n in ast.walk(tree):
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute):assert n.func.attr not in ('set_value','db_set','submit','cancel','delete_doc','commit')


def test_fee_report_counts_invoices_once_and_does_not_query_demands(env,monkeypatch):
    f,load=env;m=load('hp_lending/hp_portfolio.py');f.get_list=Mock(return_value=[
        Row(name='NEW',custom_hp_independent_fee=1,custom_hp_reference_loan='L',loan=None,outstanding_amount=2.5),
        Row(name='OLD',custom_hp_independent_fee=0,loan='L',outstanding_amount=7.5)])
    result=m.fee_position('L');assert result['independent_fees']==2.5 and result['legacy_fee_exceptions']==7.5
    assert all(c.args[0]=='Sales Invoice' for c in f.get_list.call_args_list)
    f.has_permission.return_value=False
    assert m.fee_position('L')['independent_fees'] is None


def test_new_generator_native_permissions_locks_and_stable_link(env,monkeypatch):
    f,load=env;m=load('hp_lending/hp_workflow.py');monkeypatch.setattr(m,'_require_disbursed',Mock())
    app=Row(name='APP',company=COMPANY,docstatus=1,workflow_state='LPO Pending Approval',custom_flexi_cash_price=849)
    f.get_all.return_value=[Row(name='PO')]
    po=Row(company=COMPANY,supplier=m.s.CHD_SUPPLIER,currency='FJD',grand_total=849,custom_loan_application='APP',name='PO',check_permission=Mock());f.get_doc.return_value=po
    assert m.generate_lpo(app)=='PO';f.only_for.assert_called_with('Disbursement Officer');po.check_permission.assert_called_with('read')
    assert 'FOR UPDATE' in f.db.sql.call_args.args[0]
    source=(ROOT/'hp_lending/hp_workflow.py').read_text().split('def generate_lpo(')[1]
    assert 'ignore_permissions' not in source.split('# native')[0] and 'po.insert()' in source


def test_financial_jobs_and_core_acceptance_modules_unchanged_from_dev7_reference():
    old=Path(__file__).resolve().parents[2]/'RELEASE_MANIFEST.json'
    # Hash constants taken from authenticated dev7 manifest, not recalculated from new source.
    pinned={'hp_interest.py':'dcddb9b99e18f14ead887f1d9aee8fed1bebe659ed3bbc8caa98b536617ea204',
        'hp_penalties.py':'372f1817ea97d384f8cf75ccd602a84754ccab26f5040d20c281f15fbcef9b45',
        'hp_lifecycle.py':'41649b76bc53fb8261ca72d3292630d7aeff1858c8df06b3077c0adc0faf4c3e',
        'hp_settlement.py':'4c93394dc2105629fd2b1b871206f95f1393772d31812be9ac5590aec4983673'}
    for name,digest in pinned.items():assert hashlib.sha256((ROOT/'hp_lending'/name).read_bytes()).hexdigest()==digest


def test_public_desk_api_guard_runs_with_noncallable_form_on():
    asset=ROOT/'public/js/loan_application_branch_filter.js'
    code="const fs=require('fs'),vm=require('vm');const window={location:{pathname:'/elsewhere'},frappe:{ui:{form:{}}}};vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{window,frappe:window.frappe});console.log('ok')"
    assert subprocess.run(['node','-e',code,str(asset)],capture_output=True,text=True,check=True).stdout.strip()=='ok'
