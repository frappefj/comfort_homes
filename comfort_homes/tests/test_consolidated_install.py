"""Whole-package/source and in-memory installation checks, not a site install."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from comfort_homes.tests.test_documents_setup import functions
from comfort_homes.tests.test_install_permissions import native_doc, native_validator
from comfort_homes.tests.test_origination_wiring import assignment

ROOT = Path(__file__).resolve().parents[1]


def records():
    return [(p.name, r) for p in sorted((ROOT/'fixtures').glob('*.json')) for r in json.loads(p.read_text())]


def bootstrap():
    created, fields = {}, {}
    def create(name, **kw):
        created[name] = dict(name=name, fields=kw.get('fields', []), istable=int(kw.get('istable', False)),
                             is_submittable=0, permissions=[] if kw.get('istable') else [dict(role='System Manager',read=1,write=1,create=1,delete=1)])
    def custom(d, **kw):
        for name, rows in d.items(): fields.setdefault(name, []).extend(rows)
    calls = functions('webform_setup.py', ['_schema_field', 'ensure_install_schema'],
        {'frappe':SimpleNamespace(db=SimpleNamespace(exists=lambda *a, **k:False),clear_cache=Mock()),
         '_ensure_custom_doctype':create, '_ensure_grid_columns':Mock(), 'create_custom_fields':custom})
    calls['ensure_install_schema']()
    return created, fields


def test_bootstrap_fixture_link_and_child_schema_closure():
    schemas, custom = bootstrap()
    native = json.loads((ROOT/'tests/reference/native_schema_index_v16.json').read_text())['doctypes']
    schemas = {**native, **schemas}
    for _, row in records():
        if row['doctype']=='DocType': schemas[row['name']] = row
        elif row['doctype']=='Custom Field': custom.setdefault(row['dt'], []).append(row)
    for dt, rows in custom.items():
        assert dt in schemas, dt
        merged={x['fieldname']:x for x in schemas[dt]['fields']}
        for row in rows: merged[row['fieldname']]=row
        schemas[dt]={**schemas[dt], 'fields':list(merged.values())}
    for name, schema in schemas.items():
        if name in native and name not in custom: continue
        for field in schema['fields']:
            if name in native and field['fieldname'] not in {r['fieldname'] for r in custom.get(name, [])}:
                continue  # Native app owns its own internal dependencies.
            if field['fieldtype'] in ('Link','Table','Table MultiSelect'):
                target=field.get('options')
                assert target in schemas, (name,field['fieldname'],target)
                if field['fieldtype'] in ('Table','Table MultiSelect'):
                    assert schemas[target]['istable'], (name,field['fieldname'],target)
    form=next(r for _,r in records() if r['doctype']=='Web Form')
    fields={r['fieldname'] for r in schemas[form['doc_type']]['fields']}
    no_value={'Section Break','Column Break','Page Break','HTML','Heading','Button','Tab Break'}
    assert all(not r.get('fieldname') or r['fieldtype'] in no_value or r['fieldname'] in fields for r in form['web_form_fields'])


def test_all_non_table_schema_fixtures_pass_native_permission_validator():
    for _, data in records():
        if data['doctype']=='DocType' and not data.get('istable'):
            native_validator()(native_doc(data))


def test_fixture_record_identity_is_unique_and_explicit():
    keys=[]
    for filename,row in records():
        assert row.get('doctype') and row.get('name'),filename
        keys.append((row['doctype'],row['name']))
    assert len(keys)==len(set(keys))


def test_overlay_compatibility_fixture_is_empty_not_a_second_workflow():
    assert (ROOT/'fixtures/workflow.json').read_text().strip()=='[]'
    assert len([r for _,r in records() if r['doctype']=='Workflow'])==2


def test_workflow_states_actions_tasks_and_roles_are_shipped_before_use():
    seen={'Role':{'System Manager'},'Workflow State':set(),'Workflow Action Master':set(),'Workflow Transition Tasks':set(),'Server Script':set()}
    for filename,row in records():
        dt=row['doctype']
        if dt=='Workflow Transition Tasks':
            # Any script linked by a task group must already be imported.
            for task in row.get('tasks',[]):
                if task.get('task')=='Server Script': assert task['link'] in seen['Server Script']
        if dt=='Workflow':
            for state in row['states']:
                assert state['state'] in seen['Workflow State']
                assert state['allow_edit'] in seen['Role']
            for transition in row['transitions']:
                assert transition['action'] in seen['Workflow Action Master']
                assert transition['allowed'] in seen['Role']
                if transition.get('transition_tasks'): assert transition['transition_tasks'] in seen['Workflow Transition Tasks']
        if dt in seen:seen[dt].add(row['name'])


def test_all_geography_property_setters_survive_fixture_export():
    config=next(r for r in assignment('fixtures') if r.get('dt')=='Property Setter')
    names=set(config['filters'][0][2])
    assert {r['name'] for _,r in records() if r['doctype']=='Property Setter'} <= names


def test_final_setup_runs_after_fixtures_on_both_install_and_migrate():
    hooks=ast.parse((ROOT/'hooks.py').read_text())
    assigned={n.targets[0].id for n in hooks.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name)}
    assert 'after_install' not in assigned
    assert assignment('after_sync')==assignment('after_migrate')=='comfort_homes.webform_setup.setup_after_sync'
    assert assignment('before_install')==assignment('before_migrate')=='comfort_homes.webform_setup.ensure_install_schema'
    install=(ROOT/'tests/reference/frappe_16_60_0_install_app.py.txt').read_text()
    migrate=(ROOT/'tests/reference/frappe_16_60_0_post_schema_updates.py.txt').read_text()
    assert install.index('for after_install in') < install.index('sync_fixtures(name)') < install.index('for after_sync in')
    assert migrate.index('sync_fixtures()') < migrate.index('"after_migrate"')


def test_setup_wrapper_orders_profile_before_public_form_and_never_applies_business_masters(monkeypatch):
    import sys
    order=[]
    monkeypatch.setitem(sys.modules,'comfort_homes.co_applicant',SimpleNamespace(
        ensure_loan_application_fields=lambda:order.append('fields'),
        configure_co_applicant_profile=lambda:order.append('profile')))
    # 7.1.4: the Lending workspace shortcut is (re-)added after the profile, before forms and print formats
    monkeypatch.setitem(sys.modules,'comfort_homes.hp_lending.hp_workspace',SimpleNamespace(
        ensure_lending_shortcuts=lambda:order.append('shortcuts')))
    # 7.1.6: reconciliation codes on the bank accounts, after the shortcut
    monkeypatch.setitem(sys.modules,'comfort_homes.hp_lending.hp_reconciliation_setup',SimpleNamespace(
        ensure_bank_codes=lambda:order.append('bank_codes')))
    context=functions('webform_setup.py',['setup_after_sync'],{'setup_web_form_and_print_formats':lambda:order.append('form_print')})
    context['setup_after_sync']()
    context['setup_after_sync']()
    assert order==['fields','profile','shortcuts','bank_codes','form_print']*2
    source=ast.get_source_segment((ROOT/'webform_setup.py').read_text(),next(n for n in ast.parse((ROOT/'webform_setup.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='setup_after_sync'))
    assert 'setup_masters' not in source and 'hp_permissions' not in source


@pytest.mark.parametrize('exists',[True,False])
def test_print_setup_propagates_save_or_insert_failure(exists):
    def fail(*a,**kw):raise ValueError('synthetic native print validation failure')
    doc=SimpleNamespace(update=Mock(),save=fail,insert=fail)
    f=SimpleNamespace(db=SimpleNamespace(exists=lambda *a:exists),get_doc=lambda *a:doc)
    context=functions('signed_pack.py',['sync_reviewed_print_formats'],{
        'frappe':f,'_reviewed_print_format_sources':lambda:{'Test':{'name':'Test','html':'<p>test</p>'}},
        'REVIEWED_PRINT_FORMATS':['Test'],'LEGACY_PRINT_FORMAT_ALIASES':{},'DOCUMENT_PRINT_FORMATS':{},
        '_ensure_assessment_document_template':Mock()})
    with pytest.raises(ValueError,match='synthetic native print'):context['sync_reviewed_print_formats']()
    context['_ensure_assessment_document_template'].assert_not_called()


def test_assessment_template_insert_failure_is_not_silenced():
    def fail(*a,**k):raise ValueError('synthetic template failure')
    f=SimpleNamespace(db=SimpleNamespace(exists=lambda *a:True),get_meta=lambda *a:SimpleNamespace(fields=[]),get_doc=lambda *a:SimpleNamespace(insert=fail))
    context=functions('signed_pack.py',['_ensure_assessment_document_template'],{'frappe':f,'_assessment_template':lambda:None,'ASSESSMENT_TEMPLATE_VALUES':{'name':'TEST'}})
    with pytest.raises(ValueError,match='synthetic template'):context['_ensure_assessment_document_template']()


def test_setup_does_not_commit_partial_print_normalization():
    source=(ROOT/'signed_pack.py').read_text();node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='normalize_customer_print_formats')
    assert not any(isinstance(n,ast.Attribute) and n.attr=='commit' for n in ast.walk(node))


def test_native_install_reference_digests():
    for filename,details in json.loads((ROOT/'tests/reference/install_sequence_provenance.json').read_text()).items():
        assert hashlib.sha256((ROOT/'tests/reference'/filename).read_bytes()).hexdigest()==details['extract_sha256']


def test_all_hook_callables_and_browser_assets_exist():
    tree=ast.parse((ROOT/'hooks.py').read_text())
    def leaves(value):
        if isinstance(value,str):yield value
        elif isinstance(value,dict):
            for x in value.values():yield from leaves(x)
        elif isinstance(value,(list,tuple)):
            for x in value:yield from leaves(x)
    for name in ['before_install','before_migrate','after_sync','after_migrate','doc_events','scheduler_events','extend_doctype_class','override_doctype_dashboards']:
        for value in leaves(assignment(name)):
            module,attr=value.rsplit('.',1)
            path=ROOT.parent/Path(*module.split('.')).with_suffix('.py')
            assert path.is_file(),value
            assert any(isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name==attr for n in ast.parse(path.read_text()).body),value
    for name in ['app_include_js','web_include_js','doctype_js']:
        for value in leaves(assignment(name)):
            if value.startswith('/assets/comfort_homes/'):path=ROOT/'public'/value.removeprefix('/assets/comfort_homes/')
            else:path=ROOT/value
            assert path.is_file(),value


def test_standard_report_and_all_print_document_assets_are_present():
    # 7.1: HP Customer Statement retired (HP Statement of Account print format replaces it)
    assert not (ROOT/'comfort_homes/report/hp_customer_statement').exists()
    path=ROOT/'comfort_homes/report/portfolio_control'
    assert all((path/f'portfolio_control.{ext}').is_file() for ext in ['py','js','json'])
    formats={r['name'] for _,r in records() if r['doctype']=='Print Format'}
    assert len(formats)==10  # dev27: HP Statement of Account; 7.1.6: HP Bank PPT Authority
    for _,row in records():
        if row['doctype']=='Loan Document Template' and row.get('print_format'):
            assert row['print_format'] in formats
    codes={r['document_code'] for _,r in records() if r['doctype']=='Loan Document Template'}
    assert len(codes)==8


def test_individual_cira_is_never_printed_as_the_witness_signature():
    formats={r['name']:r for _,r in records() if r['doctype']=='Print Format'}
    assert 'custom_cira_authority_signature' not in formats['Application Print WIP']['html']
    assert not (ROOT/'print_formats').exists()  # 7.1: stale HTML copies removed; fixtures are the source
    assert 'custom_cira_authority_signature' in formats['CIRA Authority Form']['html']


def test_release_verifier_detects_missing_changed_and_stale_overlay_files(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('release_verifier',ROOT.parent/'tools/verify_release.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    app=tmp_path/'comfort_homes';app.mkdir();file=app/'test.py';file.write_text('pass\n')
    (tmp_path/'RELEASE_MANIFEST.json').write_text(json.dumps({'version':'TEST','files':{'comfort_homes/test.py':hashlib.sha256(file.read_bytes()).hexdigest()}}))
    assert m.verify(tmp_path)['ok']
    file.write_text('changed\n');assert any('Different:' in x for x in m.verify(tmp_path)['problems'])
    file.unlink();assert any('Missing:' in x for x in m.verify(tmp_path)['problems'])
    (app/'stale.json').write_text('[]');assert any('Unexpected app file:' in x for x in m.verify(tmp_path)['problems'])


def test_all_shipped_print_templates_parse_without_jinja_syntax_errors():
    from jinja2 import Environment
    env=Environment()
    for _,r in records():
        if r['doctype']=='Print Format' and r.get('html'):env.parse(r['html'])
    for path in (ROOT/'print_formats').glob('*.html'):env.parse(path.read_text())


def test_schema_fields_have_no_duplicates_or_hidden_required_without_defaults():
    for _,d in records():
        if d['doctype']!='DocType':continue
        names=[f['fieldname'] for f in d['fields']]
        assert len(names)==len(set(names)),d['name']
        assert not any(f.get('hidden') and f.get('reqd') and not f.get('default') for f in d['fields'])


def test_every_bundled_server_script_parses_as_python():
    for _,r in records():
        if r['doctype']=='Server Script':compile(r.get('script',''),r['name'],'exec')
