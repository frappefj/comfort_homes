"""Install-fixture regressions; isolated native validation is not a site install."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
FLAGS = {'r': 'read', 'w': 'write', 'c': 'create', 's': 'submit', 'x': 'cancel', 'a': 'amend'}


class Row(dict):
    def __getattr__(self, key):
        return self.get(key, 0)

    def __setattr__(self, key, value):
        self[key] = value

    def set(self, key, value):
        self[key] = value


def profile():
    return json.loads((ROOT / 'fixtures/00_co_applicant_schema.json').read_text())[0]


def native_validator():
    source = (ROOT / 'tests/reference/frappe_16_56_0_validate_permissions.py.txt').read_text()
    def throw(message, **kwargs):
        raise ValueError(message)
    context = {'frappe': SimpleNamespace(throw=throw, session=SimpleNamespace(user='Administrator')),
               '_': lambda x: x, 'cint': lambda x: int(x or 0),
               'ALL_USER_ROLE': 'All', 'SYSTEM_USER_ROLE': 'Desk User'}
    exec(compile(source, '<pinned-frappe-permission-validator>', 'exec'), context)
    return context['validate_permissions']


def native_doc(data):
    result = Row(data)
    result.permissions = [Row(p, parent=data['name'], idx=i) for i, p in enumerate(data.get('permissions', []), 1)]
    return result


def test_profile_fixture_matches_both_approved_permission_levels():
    tree = ast.parse((ROOT / 'hp_lending/hp_permissions.py').read_text())
    matrices = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
                if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                and n.targets[0].id in ('MATRIX', 'ASSESSMENT')}
    expected = {(role, level): letters for level, name in [(0, 'MATRIX'), (1, 'ASSESSMENT')]
                for role, letters in matrices[name]['Co-Applicant'].items()}
    rows = profile()['permissions']
    assert len(rows) == len(expected) == 18
    assert {(p['role'], p['permlevel']) for p in rows} == set(expected)
    for row in rows:
        letters = expected[row['role'], row['permlevel']]
        assert all(row.get(field, 0) == int(letter in letters) for letter, field in FLAGS.items())
        assert not any(row.get(k) for k in ('delete', 'share', 'email', 'import', 'submit', 'cancel', 'amend'))
        assert row['export'] == int(row['role'] in ('Auditor', 'Loan Reporter', 'Accounts Manager', 'Finance Manager'))
    assert 'Employee' not in {p['role'] for p in rows}


def test_all_non_submittable_schema_fixtures_have_no_submission_rights():
    for path in (ROOT / 'fixtures').glob('*.json'):
        for record in json.loads(path.read_text()):
            if record.get('doctype') == 'DocType' and not record.get('is_submittable'):
                for row in record.get('permissions', []):
                    assert not any(row.get(k) for k in ('submit', 'cancel', 'amend')), (path.name, record['name'], row['role'])


def test_corrected_profile_passes_pinned_native_permission_validation():
    data = profile()
    assert data['is_submittable'] == 0
    native_validator()(native_doc(data))


@pytest.mark.parametrize('role,flag,message', [
    ('Loan Manager', 'submit', 'Cannot set Assign Submit'),
    ('Employee', 'submit', 'Cannot set Assign Submit'),
    ('Loan LOS User', 'submit', 'Cannot set Assign Submit'),
    ('Loan Manager', 'amend', 'Cannot set Assign Amend'),
    ('Employee', 'amend', 'Cannot set Assign Amend'),
])
def test_native_validator_detects_each_original_incompatible_grant(role, flag, message):
    data = profile()
    data['permissions'] = [dict(role=role, permlevel=0, read=1, write=1, create=1, **{flag: 1})]
    with pytest.raises(ValueError, match=message):
        native_validator()(native_doc(data))


def test_pinned_native_reference_matches_recorded_digest():
    path = ROOT / 'tests/reference/frappe_16_56_0_validate_permissions'
    provenance = json.loads(path.with_suffix('.provenance.json').read_text())
    assert hashlib.sha256(path.with_suffix('.py.txt').read_bytes()).hexdigest() == provenance['extract_sha256']


def test_each_workflow_is_imported_once_after_its_dependencies():
    paths = sorted((ROOT / 'fixtures').glob('*.json'))
    workflows = [(path.name, record['name']) for path in paths for record in json.loads(path.read_text())
                 if record.get('doctype') == 'Workflow']
    assert len(workflows) == 2
    assert {name for _, name in workflows} == {'Loan Application Workflow-1', 'Co-Applicant Profile Review'}
    assert all(filename == 'zz_workflow.json' for filename, _ in workflows)
