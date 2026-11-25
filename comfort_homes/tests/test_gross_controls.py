"""Pure/mocked source regressions. No site, migration or GL acceptance claimed."""
import ast
from datetime import date, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from comfort_homes.hp_lending.hp_controls import deposit_issues

ROOT = Path(__file__).resolve().parents[1]
COMPANY = 'Comfort Home Furnishing PTE Limited'


class Row(dict):
    def __getattr__(self, key):
        return self.get(key)
    __setattr__ = dict.__setitem__
    def set(self, key, value):
        self[key] = value


def fail(message, *args, **kwargs):
    raise ValueError(message)


@pytest.fixture
def env(monkeypatch):
    f = ModuleType('frappe')
    f._ = lambda message: message
    f.whitelist = lambda *a, **k: lambda function: function
    f.only_for = Mock()
    f.has_permission = Mock(return_value=True)
    f.throw = fail
    f.db = SimpleNamespace(sql=Mock(return_value=[]), get_value=Mock(), exists=Mock(return_value=False),
                           commit=Mock(), savepoint=Mock(), rollback=Mock())
    f.get_doc, f.get_all = Mock(), Mock(return_value=[])
    f.flags = SimpleNamespace(in_web_form=False)
    f.clear_cache = Mock()
    f.parse_json = lambda value: json.loads(value) if isinstance(value, str) else value
    u = ModuleType('frappe.utils')
    u.flt = lambda value=None, precision=None: round(float(value or 0), precision) if precision is not None else float(value or 0)
    u.cint = lambda value=None: int(value or 0)
    u.getdate = lambda value=None: value if isinstance(value, date) else date.fromisoformat(str(value or '2026-11-17')[:10])
    u.today = lambda: '2026-11-17'
    u.add_days = lambda value, days: u.getdate(value) + timedelta(days=days)
    u.get_first_day = lambda value: u.getdate(value).replace(day=1)
    f.utils = u
    monkeypatch.setitem(sys.modules, 'frappe', f)
    monkeypatch.setitem(sys.modules, 'frappe.utils', u)
    def load(relative):
        name = 'unit_' + relative.replace('/', '_').replace('.', '_')
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return f, load


def receipt():
    application = Row(name='APP-TEST', company=COMPANY, applicant='CUSTOMER-TEST', custom_flexi_deposit_amount=170)
    payment = Row(docstatus=1, payment_type='Receive', party_type='Customer', party='CUSTOMER-TEST',
                  company=COMPANY, paid_from='2010', paid_to='1201', paid_from_account_currency='FJD',
                  paid_to_account_currency='FJD', paid_amount=170, received_amount=170,
                  total_allocated_amount=0, references=[], source_exchange_rate=1, target_exchange_rate=1)
    return application, payment


def test_correct_deposit_binding():
    app, pe = receipt()
    assert deposit_issues(app, pe, '2010') == []


@pytest.mark.parametrize('field,value', [('docstatus', 0), ('docstatus', 2), ('payment_type', 'Pay'),
    ('party_type', 'Supplier'), ('party', 'OTHER'), ('company', 'OTHER'), ('paid_from', '1120'),
    ('paid_from_account_currency', 'USD'), ('paid_to_account_currency', 'USD'), ('paid_amount', 169.99),
    ('received_amount', 169.99), ('total_allocated_amount', 170), ('references', [{'reference_name': 'INV'}]),
    ('source_exchange_rate', 2), ('target_exchange_rate', 2)])
def test_wrong_deposit_is_rejected(field, value):
    app, pe = receipt()
    pe[field] = value
    assert deposit_issues(app, pe, '2010')


def test_draft_receipt_is_only_allowed_when_opening_the_draft():
    app, pe = receipt()
    pe.docstatus = 0
    assert deposit_issues(app, pe, '2010', False) == []
    assert deposit_issues(app, pe, '2010', True)


def native_rate_method(frappe):
    source = (ROOT / 'tests/reference/lending_16_8_1_set_missing_fields.py.txt').read_text()
    namespace = {'frappe': frappe, 'erpnext': SimpleNamespace(get_default_company=lambda: COMPANY),
                 'nowdate': lambda: '2026-11-17'}
    exec(compile(source, '<pinned-native-Loan.set_missing_fields>', 'exec'), namespace)
    return namespace['set_missing_fields']


def test_native_zero_default_is_reproduced_and_scoped_extension_fixes_it(env):
    f, load = env
    f.get_cached_value = Mock(return_value=22)
    method = native_rate_method(f)
    class Native(Row):
        set_missing_fields = method
        def get_doc_before_save(self):
            return self.get('_previous')
    plain = Native(company=COMPANY, posting_date='2026-11-17', loan_product='HP-STD', rate_of_interest=0)
    plain.set_missing_fields()
    assert plain.rate_of_interest == 22  # observed execution of the pinned native method
    module = load('hp_lending/loan_extension.py')
    class Extended(module.GrossHPLoanMixin, Native):
        pass
    hp = Extended(company=COMPANY, posting_date='2026-11-17', loan_product='HP-STD',
                  loan_application='APP', custom_hp_contract_rate=22, rate_of_interest=0)
    hp.set_missing_fields()
    assert hp.rate_of_interest == 0
    assert hp.custom_hp_contract_rate == 22
    assert f.get_cached_value.return_value == 22  # product itself is not changed


@pytest.mark.parametrize('extra', [{'company': 'OTHER'}, {'is_imported': 1}, {'loan_application': None}, {'custom_hp_contract_rate': 0}])
def test_zero_extension_does_not_change_unmanaged_loans(env, extra):
    f, load = env
    f.get_cached_value = Mock(return_value=22)
    module = load('hp_lending/loan_extension.py')
    method = native_rate_method(f)
    class Native(Row):
        set_missing_fields = method
        def get_doc_before_save(self): return None
    class Extended(module.GrossHPLoanMixin, Native): pass
    values = dict(company=COMPANY, posting_date='2026-11-17', loan_product='HP-STD',
                  loan_application='APP', custom_hp_contract_rate=22, rate_of_interest=0)
    values.update(extra)
    obj = Extended(values)
    obj.set_missing_fields()
    assert obj.rate_of_interest == 22


def test_posted_nonzero_hp_loan_is_not_silently_rewritten(env):
    f, load = env
    module = load('hp_lending/loan_extension.py')
    class Native(Row):
        def set_missing_fields(self): pass
        def get_doc_before_save(self): return Row(docstatus=1, rate_of_interest=22)
    class Extended(module.GrossHPLoanMixin, Native): pass
    loan = Extended(company=COMPANY, loan_application='APP', custom_hp_contract_rate=22, rate_of_interest=22)
    with pytest.raises(ValueError, match='Existing HP loan'):
        loan.set_missing_fields()
    assert loan.rate_of_interest == 22


def test_grace_query_runs_against_real_sqlite_rows(env):
    f, load = env
    module = load('hp_lending/hp_penalties.py')
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE `tabLoan Demand` (loan TEXT,docstatus INT,demand_type TEXT,outstanding_amount NUMERIC,demand_date TEXT)')
    con.executemany('INSERT INTO `tabLoan Demand` VALUES (?,?,?,?,?)', [('L',1,'EMI',73.30,'2026-01-01'),
        ('L',1,'Penalty',999,'2026-01-01'),('L',0,'EMI',999,'2026-01-01'),('OTHER',1,'EMI',999,'2026-01-01')])
    queries = []
    def sql(query, params):
        queries.append(query)
        return con.execute(query.replace('%s','?'), tuple(str(p) if isinstance(p,date) else p for p in params)).fetchall()
    f.db.sql = sql
    assert module.overdue_base('L','2026-01-08',7) == 0
    assert module.overdue_base('L','2026-01-09',7) == 73.30
    assert module.overdue_base('L','2026-02-01',7) == 73.30  # old arrears remain eligible
    assert 'demand_date < %s' in queries[-1]
    con.close()


def test_reused_deposit_rejected_after_receipt_lock(env, monkeypatch):
    f, load = env
    module = load('hp_lending/hp_lifecycle.py')
    app, pe = receipt(); app.custom_hp_deposit_payment_entry = 'PAYMENT'
    f.get_doc.return_value = pe
    f.db.get_value.return_value = Row(company=COMPANY, account_type='Bank', disabled=0, is_group=0)
    f.db.exists.return_value = 'OTHER-APP'
    monkeypatch.setattr(module, 's', SimpleNamespace(account=lambda *args: '2010'))
    with pytest.raises(ValueError, match='another application'):
        module.check_deposit(app)
    assert 'FOR UPDATE' in f.db.sql.call_args_list[0].args[0]


def test_read_only_reconciliation_user_cannot_mutate(env):
    f, load = env
    module = load('api.py')
    doc = Row(check_permission=Mock(side_effect=ValueError('denied')), docstatus=0)
    f.get_doc.return_value = doc
    with pytest.raises(ValueError, match='denied'):
        module.loan_reconciliation('RECO','create_repayments')
    f.db.sql.assert_not_called()


def test_repayment_submission_permission_is_checked_before_lock_or_insert(env):
    f, load = env
    module = load('api.py')
    doc = Row(check_permission=Mock(), name='RECO', docstatus=0)
    f.has_permission.side_effect = lambda dt, perm, **kw: fail('no submit') if perm=='submit' else True
    with pytest.raises(ValueError, match='no submit'):
        module.create_repayments(doc)
    f.db.sql.assert_not_called()
    f.get_doc.assert_not_called()


def test_exact_statement_key_is_stable_but_preserves_identical_distinct_rows(env):
    _, load = env
    module = load('api.py')
    assert module._statement_key('BANK','HASH','SHEET',1) == module._statement_key('BANK','HASH','SHEET',1)
    assert module._statement_key('BANK','HASH','SHEET',1) != module._statement_key('BANK','HASH','SHEET',2)
    assert module._statement_key('BANK','HASH','SHEET',1) != module._statement_key('OTHER','HASH','SHEET',1)


def test_payment_account_must_belong_to_reconciliation_company(env):
    f, load = env
    module = load('api.py')
    f.db.get_value.return_value = Row(account='1201', disabled=0, is_company_account=1, company='OTHER')
    with pytest.raises(ValueError, match='different company'):
        module._payment_account('BANK',COMPANY)


def test_setup_uses_native_cents_and_explicit_bank(env, monkeypatch):
    f, load = env
    module = load('hp_lending/hp_setup.py')
    monkeypatch.setattr(module,'_acc',lambda key: key)
    monkeypatch.setattr(module,'_by_number',lambda number: 'ACCOUNT-'+number)
    f.db.get_value.return_value = 'Bank'
    values = module._product_values()
    assert values['emi_rounding_method'] == 'No Rounding'
    assert values['payment_account'] == 'ACCOUNT-1201'
    assert values['rate_of_interest'] == 22
    assert values['penalty_interest_rate'] == 0
    assert values['days_past_due_threshold_for_npa'] == 89
    assert values['custom_hp_penalty_stop_dpd'] == 90
    assert values['custom_hp_penalty_rate_monthly'] == 2.5


def test_setup_checks_no_longer_report_success_without_hp_product_and_fields(env):
    f, load = env
    module = load('hp_lending/hp_settings.py')
    f.db.get_value.return_value = None
    f.db.exists.return_value = False
    result = module.check_setup()
    assert result['ok'] is False
    assert result['native_acceptance_tested'] is False
    assert any('HP-STD' in message for message in result['problems'])
    assert any('custom_hp_deposit_payment_entry' in message for message in result['problems'])
    f.db.commit.assert_not_called()


def test_assessment_permissions_do_not_restore_delete_or_financial_neighbour_access(env):
    _, load = env
    module = load('hp_lending/hp_permissions.py')
    roles = {role for grants in module.MATRIX.values() for role in grants}
    rows = module.desired_rows('Loan Application',roles)
    assert any(row['role']=='Loan Processor' and row['permlevel']==1 and row['write'] for row in rows)
    for dt in module.MATRIX:
        assert all(row['delete']==0 and row['share']==0 for row in module.desired_rows(dt,roles))
    assert 'Disbursement Officer' not in module.MATRIX['Purchase Invoice']
    assert module.MATRIX['Loan Disbursement'].get('Accounts Officer','r') == 'r'


def test_write_endpoints_are_post_only():
    expected = {'api.py':['create_deposit_payment_entry','loan_reconciliation'],
                'signed_pack.py':['make_document_pack','make_signed_pack','add_assessment_document_to_existing_applications'],
                'hp_lending/hp_setup.py':['setup_masters'], 'hp_lending/hp_permissions.py':['apply']}
    for path, names in expected.items():
        tree=ast.parse((ROOT/path).read_text())
        for name in names:
            node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            assert any(isinstance(d,ast.Call) and any(k.arg=='methods' and ast.literal_eval(k.value)==['POST'] for k in d.keywords) for d in node.decorator_list), (path,name)


def test_native_disbursement_mode_overrides_account_and_validate_hook_restores_clearing(env,monkeypatch):
    f,load=env
    source=(ROOT/'tests/reference/lending_16_8_1_disbursement_defaults.py.txt').read_text()
    context={'frappe':f,'erpnext':SimpleNamespace(get_default_cost_center=lambda company:'BRANCH'),
             'nowdate':lambda:'2026-11-17','getdate':f.utils.getdate}
    exec(compile(source,'<pinned-native-disbursement-defaults>','exec'),context)
    def get_value(dt,*args,**kwargs):
        return 'CASH' if dt=='Mode of Payment Account' else ('Monthly as per repayment start date','Monthly',None)
    f.db.get_value.side_effect=get_value
    disbursement=Row(against_loan='LOAN',company=COMPANY,disbursement_date='2026-11-17',cost_center='BRANCH',
                     disbursement_account='CLEARING',mode_of_payment='Cash',repayment_method='Repay Over Number of Periods')
    context['set_missing_values'](disbursement)
    assert disbursement.disbursement_account=='CASH'
    module=load('hp_lending/hp_lifecycle.py')
    monkeypatch.setattr(module,'s',SimpleNamespace(COMPANY=COMPANY,account=lambda *args:'CLEARING'))
    f.get_doc.return_value=Row(company=COMPANY,loan_application='APP')
    module.enforce_clearing_account(disbursement)
    assert disbursement.disbursement_account=='CLEARING'
    assert disbursement.mode_of_payment is None


def test_approved_native_threshold_flags_stored_dpd_90_not_89():
    source=(ROOT/'tests/reference/lending_16_8_1_npa_condition.py.txt').read_text()
    native={'days_past_due':90,'threshold':90,'is_npa':0}
    exec(compile(source,'<pinned-native-NPA-condition>','exec'),native)
    assert native['is_npa']==0
    proposed={'days_past_due':90,'threshold':89,'is_npa':0}
    exec(compile(source,'<pinned-native-NPA-condition>','exec'),proposed)
    assert proposed['is_npa']==1  # approved configuration; real site dates still require acceptance
    before={'days_past_due':89,'threshold':89,'is_npa':0}
    exec(compile(source,'<pinned-native-NPA-condition>','exec'),before)
    assert before['is_npa']==0
    settings=ast.parse((ROOT/'hp_lending/hp_settings.py').read_text())
    blockers=ast.literal_eval(next(n.value for n in settings.body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='RELEASE_BLOCKERS' for t in n.targets)))
    assert blockers == ()
    native_threshold=ast.literal_eval(next(n.value for n in settings.body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='NATIVE_NPA_THRESHOLD' for t in n.targets)))
    assert native_threshold==89


def test_disbursement_honours_any_explicit_future_release_blocker(env,monkeypatch):
    f,load=env
    module=load('hp_lending/hp_lifecycle.py')
    monkeypatch.setattr(module,'s',SimpleNamespace(COMPANY=COMPANY,RELEASE_BLOCKERS=['Synthetic release blocker']))
    f.get_doc.return_value=Row(company=COMPANY,loan_application='APP')
    with pytest.raises(ValueError,match='Synthetic release blocker'):
        module.validate_disbursement(Row(against_loan='LOAN'))
    f.db.sql.assert_not_called()


def test_save_and_submit_both_finalize_flat_application_summaries():
    tree=ast.parse((ROOT/'hooks.py').read_text())
    events=ast.literal_eval(next(n.value for n in tree.body if isinstance(n,ast.Assign)
         and any(isinstance(t,ast.Name) and t.id=='doc_events' for t in n.targets)))
    assert events['Loan Application']['before_save']==events['Loan Application']['before_submit']
    assert events['Loan Disbursement']['validate'].endswith('enforce_clearing_account')
