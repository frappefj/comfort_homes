"""Configuration/attachment unit tests, not PDF rendering or site migration tests."""
import ast
import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from comfort_homes.tests.test_gross_controls import Row, env

ROOT = Path(__file__).resolve().parents[1]


def functions(path, names, context):
    nodes=[node for node in ast.parse((ROOT/path).read_text()).body if isinstance(node,ast.FunctionDef) and node.name in names]
    assert len(nodes)==len(names)
    for node in nodes: node.decorator_list=[]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<isolated-reviewed-functions>','exec'),context)
    return context


def test_backfill_defaults_to_preview_with_no_writes(env):
    f,_=env
    f.get_all.return_value=['APP-1','APP-2']
    context=functions('signed_pack.py',['add_assessment_document_to_existing_applications'],
                      {'frappe':f,'_':lambda x:x,'_assessment_template':lambda:Row(name='ASSESSMENT',document_code='APPLICATION-ASSESSMENT',version=1)})
    result=context['add_assessment_document_to_existing_applications']()
    assert result=={'dry_run':1,'would_add':['APP-1','APP-2'],'added':0}
    f.get_doc.assert_not_called()
    f.db.commit.assert_not_called()
    f.db.sql.assert_not_called()
    f.only_for.assert_called_once_with('System Manager')


def test_migration_does_not_invoke_existing_application_backfill():
    tree=ast.parse((ROOT/'webform_setup.py').read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='setup_web_form_and_print_formats')
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='add_assessment_document_to_existing_applications' for n in ast.walk(node))


def test_pack_attachments_are_versioned_and_earlier_files_are_not_deleted(env):
    f,_=env
    application=Row(name='APP',check_permission=Mock())
    inserted=Row(name='FILE',file_url='/private/files/pack.pdf')
    new=Row(insert=Mock(return_value=inserted))
    f.get_doc.return_value=new
    context=functions('signed_pack.py',['_attach'],{'frappe':f,'hashlib':hashlib})
    assert context['_attach'](application,'Pack.pdf',b'first') is inserted
    record=f.get_doc.call_args.args[0]
    assert record['is_private']==1 and record['file_name'].startswith('Pack-')
    assert record['content']==b'first'
    assert record['attached_to_name']=='APP'
    assert not hasattr(f,'delete_doc')
    assert 'FOR UPDATE' in f.db.sql.call_args.args[0]


def test_identical_pack_reuses_existing_private_evidence(env):
    f,_=env
    f.db.exists.return_value='FILE'
    old=Row(check_permission=Mock(),get_content=Mock(return_value=b'same'))
    f.get_doc.return_value=old
    context=functions('signed_pack.py',['_attach'],{'frappe':f,'hashlib':hashlib})
    assert context['_attach'](Row(name='APP',check_permission=Mock()),'Pack.pdf',b'same') is old
    old.check_permission.assert_called_once_with('read')


def test_conflicting_existing_pack_is_not_overwritten(env):
    f,_=env
    f.db.exists.return_value='FILE'
    f.get_doc.return_value=Row(check_permission=Mock(),get_content=Mock(return_value=b'old'))
    context=functions('signed_pack.py',['_attach'],{'frappe':f,'hashlib':hashlib})
    with pytest.raises(ValueError,match='conflicting content'):
        context['_attach'](Row(name='APP',check_permission=Mock()),'Pack.pdf',b'new')


def test_signed_pack_excludes_the_non_signing_assessment():
    tree=ast.parse((ROOT/'signed_pack.py').read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='make_signed_pack')
    assert any(isinstance(n,ast.Attribute) and n.attr=='requires_signature' for n in ast.walk(node))
    assert any(isinstance(n,ast.FunctionDef) and n.name=='_check_collection_complete' for n in tree.body)


def test_document_fixture_covers_all_codes_without_reusing_identifiers():
    records=json.loads((ROOT/'fixtures/loan_document_template.json').read_text())
    assert len({r['name'] for r in records})==len(records)==8
    assert {r['document_code'] for r in records}=={'HP-AGREEMENT','SCHEDULE-1','FORM-1','FORM-2','FINANCIAL-OBLIGATIONS','CONSENT-FORM','REQ','APPLICATION-ASSESSMENT'}
    assessment=next(r for r in records if r['document_code']=='APPLICATION-ASSESSMENT')
    assert not assessment['requires_signature'] and assessment['print_format']=='Application Print WIP'


def test_chart_has_all_native_importer_mandatory_account_types_and_restored_types():
    # Requirement list read from ERPNext v16.55.0 get_mandatory_account_types.
    records=list(csv.DictReader((ROOT/'setup_data/chart_of_accounts_flexi.csv').open()))
    assert len(records)==111
    required={'Cost of Goods Sold','Depreciation','Fixed Asset','Payable','Receivable','Stock Adjustment','Bank','Cash','Stock'}
    assert required <= {r['Account Type'] for r in records}
    by_number={r['Account Number']:r for r in records}
    assert by_number['2008']['Account Type']=='Payable'
    assert by_number['2009']['Account Type']=='Bank'
    assert by_number['2010']['Account Type']=='Receivable'
    assert by_number['2310']['Account Type']=='Tax'
    assert all(by_number[n]['Is Group']=='0' for n in ['1196','5003','5004','5005'])


def test_setup_dry_run_does_not_apply_or_commit(env,monkeypatch):
    f,load=env
    module=load('hp_lending/hp_setup.py')
    f.db.exists.return_value=True
    monkeypatch.setattr(module,'_plan',lambda:['Create HP-STD'])
    monkeypatch.setattr(module,'field_diffs',lambda:[{'field':'payment_account','before':None,'after':'1201'}])
    for name in ['_roles','_company','_supplier','_item','_loan_product']:
        monkeypatch.setattr(module,name,Mock())
    result=module.setup_masters()
    assert result['dry_run']==1 and result['field_diffs'][0]['after']=='1201'
    module._company.assert_not_called()
    f.db.commit.assert_not_called()


def test_failed_setup_validation_does_not_explicitly_commit(env,monkeypatch):
    f,load=env
    module=load('hp_lending/hp_setup.py')
    f.db.exists.return_value=True
    monkeypatch.setattr(module,'_plan',lambda:[])
    monkeypatch.setattr(module,'field_diffs',lambda:[])
    for name in ['_roles','_company','_supplier','_item','_loan_product']:
        monkeypatch.setattr(module,name,Mock())
    monkeypatch.setattr(module,'s',SimpleNamespace(COMPANY='COMPANY',check_setup=lambda:{'ok':False,'problems':['missing mapping']}))
    with pytest.raises(ValueError,match='Setup validation failed'):
        module.setup_masters(0)
    f.db.commit.assert_not_called()


def test_legacy_api_scripts_have_shared_permission_gate_before_old_body():
    records=json.loads((ROOT/'fixtures/server_script.json').read_text())
    scripts=[r for r in records if r['script_type']=='API' and r['name']!='Dynamic Laon P']
    assert len(scripts)==11
    for row in scripts:
        assert row['script'].startswith('frappe.call("comfort_homes.api.check_application_access"'),row['name']
    customer=next(r for r in records if r['name']=='Flexi Create Customer From Loan Application')
    assert customer['doctype_event']=='Before Validate'
