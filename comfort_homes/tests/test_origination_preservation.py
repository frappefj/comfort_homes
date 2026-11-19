"""Pure/mocked preservation tests. These do not connect to or certify a site."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from comfort_homes.hp_lending import hp_math
from comfort_homes.hp_lending.origination_math import assessment_values, contract_values, number, profile_values
ROOT = Path(__file__).resolve().parents[1]

class Model(dict):

    def __getattr__(self, name):
        return self.get(name)

    def __setattr__(self, name, value):
        self[name] = value

    def set(self, name, value):
        self[name] = value

    def is_new(self):
        return self.get('_new', True)

    @property
    def meta(self):
        return SimpleNamespace(has_field=lambda name: True)

def application(frequency='Monthly', income=1500, rate='22', months=12):
    return Model(docstatus=0, applicant_type='Customer', applicant=None, custom_flexi_purchase_items=[Model(quantity=1, unit_price=849)], custom_flexi_deposit_amount=170, custom_flexi_documentation_fee=25, custom_flexi_admin_fee_rate=2.5, custom_flexi_term_months=months, custom_flexi_interest_rate=rate, custom_flexi_selected_dsr=35, custom_flexi_payment_period='Monthly', custom_flexi_income_entries=[Model(income_type='Net Salary / Wages', frequency=frequency, amount=income)], custom_flexi_living_expenses=[], custom_flexi_finance_obligations=[])

def blocked(*args, **kwargs):
    raise AssertionError('Network/database work is not allowed in the local calculation reference')

def old_uat_calculation(doc):
    source = (ROOT / 'tests/reference/uat_calculations.py.txt').read_text()
    expected = json.loads((ROOT / 'tests/reference/manifest.json').read_text())['script_sha256']
    assert hashlib.sha256(source.replace('\r\n', '\n').strip().encode()).hexdigest() == expected
    assert not any((isinstance(n, (ast.Import, ast.ImportFrom)) for n in ast.walk(ast.parse(source))))
    frappe = SimpleNamespace(utils=SimpleNamespace(flt=lambda x=None: float(x or 0)), db=SimpleNamespace(exists=blocked), get_doc=blocked, throw=blocked)
    exec(compile(source, '<offline-UAT-calculation-reference>', 'exec'), {'frappe': frappe, 'doc': doc})
    return doc

def new_values(doc):
    profile = profile_values(doc.custom_flexi_income_entries, doc.custom_flexi_living_expenses, doc.custom_flexi_finance_obligations)
    contract = contract_values(doc, doc.custom_flexi_purchase_items)
    return {**profile['fields'], **contract['fields'], **assessment_values(profile, contract, doc.custom_flexi_selected_dsr, doc.custom_flexi_payment_period)}
import unittest
from unittest.mock import patch

class TestOriginationPreservation(unittest.TestCase):

    def setUp(self):
        fake = SimpleNamespace(throw=lambda message, **kw: (_ for _ in ()).throw(ValueError(message)), get_doc=blocked, flags=SimpleNamespace(in_web_form=False))
        self.modules = patch.dict(sys.modules, {'frappe': fake, 'comfort_homes.loan_application_workflow': SimpleNamespace(_set_employment_period=lambda doc: None)})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location('origination_adapter_under_test', ROOT / 'origination.py')
        self.adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.adapter)

    def test_preserves_observed_uat_saved_financial_results(self):
        for frequency, income in [('Monthly', 1500), ('Weekly', 350), ('Fortnightly', 700), ('Bi-monthly', 750)]:
            for payment_period in ['Monthly', 'Weekly', 'Fortnightly']:
                with self.subTest(frequency=frequency, income=income, payment_period=payment_period):
                    old_doc, new_doc = (application(frequency, income), application(frequency, income))
                    old_doc.custom_flexi_payment_period = new_doc.custom_flexi_payment_period = payment_period
                    old_uat_calculation(old_doc)
                    result = new_values(new_doc)
                    for key, value in result.items():
                        if key in old_doc:
                            assert value == old_doc[key], key

    def test_all_existing_rate_choices_keep_origination_rounding(self):
        for rate in ['12', '19', '22', '25', '27']:
            with self.subTest(rate=rate):
                old = old_uat_calculation(application(rate=rate))
                actual = new_values(application(rate=rate))
                assert actual['custom_flexi_term_charges'] == old.custom_flexi_term_charges
                assert actual['custom_flexi_total_receivable'] == old.custom_flexi_total_receivable
                assert actual['loan_amount'] == old.loan_amount

    def test_origination_example_and_original_reference_are_distinct(self):
        fields = new_values(application())
        assert fields['loan_amount'] == 720.98
        assert fields['custom_flexi_admin_fee'] == 16.98
        assert fields['custom_flexi_term_charges'] == 158.62
        assert fields['custom_flexi_total_receivable'] == 879.6
        assert fields['custom_flexi_gross_contract_value'] == 1049.6
        assert (fields['custom_flexi_doc_fee_net'], fields['custom_flexi_doc_fee_vat']) == (22.22, 2.78)
        assert sum(hp_math.instalment_schedule('879.59', 12)) == hp_math.d('879.59')
        assert fields['custom_flexi_total_receivable'] != 879.59

    def test_origination_derived_repayment_targets(self):
        for periods, each, last in [(12, '73.30', '73.30'), (26, '33.83', '33.85'), (52, '16.92', '16.68')]:
            with self.subTest(periods=periods, each=each, last=last):
                rows = hp_math.instalment_schedule('879.60', periods)
                assert rows[0] == hp_math.d(each) and rows[-1] == hp_math.d(last)
                assert sum(rows) == hp_math.d('879.60')

    def test_origination_derived_reclass_balances_without_rounding_journal(self):
        fields = new_values(application())
        rows = hp_math.reclass_entry(loan_amount=fields['custom_flexi_total_receivable'], deposit=170, goods_value=849, term_charges=fields['custom_flexi_term_charges'], admin_fee=fields['custom_flexi_admin_fee'], doc_fee_net=fields['custom_flexi_doc_fee_net'], doc_fee_vat=fields['custom_flexi_doc_fee_vat'])
        assert rows['debit']['clearing'] == hp_math.d('30.60')
        assert sum(rows['debit'].values()) == sum(rows['credit'].values())
        assert hp_math.rule_of_78_unearned('158.62', 12, 6) == hp_math.d('42.71')

    def test_monthly_assessment_divisor_is_not_silently_changed(self):
        assert new_values(application())['custom_flexi_weekly_eligibility'] == 131.25
        assert round(1500 * 0.35 * 12 / 52, 2) == 121.15

    def test_mixed_frequency_and_salary_deduction_precision(self):
        old, new = (application(), application())
        income = [Model(income_type='Net Salary / Wages', frequency='Monthly', amount=1000.01), Model(income_type='Salary Deductions', frequency='Weekly', amount=-3.33), Model(income_type='Other Income', frequency='Weekly', amount=0.01)]
        debts = [Model(amount=0.01, frequency='Weekly'), Model(amount=0.01, frequency='Weekly')]
        old.custom_flexi_income_entries = [Model(r) for r in income]
        new.custom_flexi_income_entries = [Model(r) for r in income]
        old.custom_flexi_finance_obligations = [Model(r) for r in debts]
        new.custom_flexi_finance_obligations = [Model(r) for r in debts]
        old_uat_calculation(old)
        result = new_values(new)
        for key in ['custom_flexi_total_monthly_income', 'custom_flexi_total_finance_obligations', 'custom_flexi_weekly_eligibility', 'custom_flexi_post_dsr', 'custom_flexi_eligibility_status']:
            assert result[key] == old[key], key
        assert profile_values(income)['assessment_frequency'] == 'Weekly'

    def test_nonpositive_income_is_not_assessed(self):
        for income in [0, -10]:
            with self.subTest(income=income):
                assert new_values(application(income=income))['custom_flexi_eligibility_status'] == 'Not Assessed'

    def test_application_approval_basis_stays_net_financed(self):
        for goods, expected, manager_allowed in [(2902.43, 2999.99, True), (2902.44, 3000.0, False), (2707.32, 2800.0, True)]:
            with self.subTest(goods=goods, expected=expected, manager_allowed=manager_allowed):
                doc = application()
                doc.custom_flexi_purchase_items = [Model(quantity=1, unit_price=goods)]
                doc.custom_flexi_deposit_amount = 0
                result = new_values(doc)
                assert result['loan_amount'] == result['custom_flexi_net_amount_financed'] == expected
                assert (result['loan_amount'] < 3000) is manager_allowed
                assert result['custom_flexi_total_receivable'] > 3000

    def test_eligibility_compares_unrounded_instalment(self):
        profile = profile_values([{'amount': 400, 'frequency': 'Monthly'}])
        contract = {'amounts_exact': {'weekly': 35.004, 'fortnightly': 70.008, 'bimonthly': 70.008, 'monthly': 100}, 'fields': {'custom_flexi_total_receivable': 1820.208}}
        fields = assessment_values(profile, contract, 35, 'Monthly')
        assert fields['custom_flexi_weekly_eligibility'] == 35
        assert fields['custom_flexi_eligibility_status'] == 'Not Achieved'

    def test_percentage_only_deposit_safeguard_and_zero_item(self):
        doc = application()
        doc.custom_flexi_deposit_amount = 0
        doc.custom_flexi_deposit_percent = 10
        doc.custom_flexi_purchase_items.append(Model(quantity=1, unit_price=0))
        result = contract_values(doc, doc.custom_flexi_purchase_items)
        assert result['fields']['custom_flexi_deposit_amount'] == 85
        assert result['line_totals'][-1] == 0

    def test_nonfinite_inputs_rejected(self):
        for value in [float('nan'), float('inf'), float('-inf')]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    number(value)

    def test_finalisation_restores_native_overwritten_preview(self):
        adapter = self.adapter
        doc = application()
        adapter.apply_application(doc)
        assert doc.loan_amount == 720.98
        doc.total_payable_amount, doc.total_payable_interest, doc.repayment_amount = (999, 999, 999)
        adapter.apply_application(doc)
        assert (doc.total_payable_amount, doc.total_payable_interest, doc.repayment_amount) == (879.6, 158.62, 73.3)

    def test_profile_does_not_create_or_overwrite_a_facility(self):
        adapter = self.adapter
        doc = application()
        doc.loan_amount = 123
        adapter.apply_profile(doc)
        assert doc.custom_flexi_total_monthly_income == 1500
        assert doc.loan_amount == 123
        assert 'custom_flexi_total_receivable' not in doc

    def test_submitted_contract_is_not_repriced(self):
        adapter = self.adapter
        doc = application()
        adapter.apply_application(doc)
        doc.docstatus, doc._new = (1, False)
        doc.custom_flexi_interest_rate = '27'
        adapter.apply_application(doc)
        assert doc.custom_flexi_term_charges == 158.62

    def test_phone_helper(self):
        adapter = self.adapter
        assert adapter._phone('+679679-1234567') == '+679-1234567'
        assert adapter._phone('1234567') == '+679-1234567'

    def test_task_group_fixture_has_native_synchronous_dependencies(self):
        groups = json.loads((ROOT / 'fixtures/workflow_transition_tasks.json').read_text())
        assert {g['name'] for g in groups} == {'Flexi Generate LPO', 'Flexi Loan Application Transition Tasks', 'Flexi Approve LPO'}
        assert all((g['tasks'][0]['enabled'] == 1 and g['tasks'][0]['asynchronous'] == 0 for g in groups))
        assert all(g['tasks'][0]['task'] == ('Flexi Submit Approved LPO' if g['name'] == 'Flexi Approve LPO' else 'Server Script') for g in groups)

    def test_existing_customer_prefill_keeps_saved_identity_and_customer_unchanged(self):
        customer = Model(customer_name='Sample X Borrower', customer_primary_contact='CONTACT-TEST',
                         customer_primary_address='ADDRESS-TEST', custom_tin_number='TEST-ID',
                         email_id='example@example.invalid', mobile_no='1234567')
        contact = Model(first_name='Sample', middle_name='X', last_name='Borrower', full_name='Sample X Borrower')
        address = Model(address_line1='Test address', city='Test city', state='Test province', country='Fiji')
        documents = {'Customer': customer, 'Contact': contact, 'Address': address}
        customer.check_permission = lambda permission: None
        before = dict(customer)
        self.adapter.frappe.get_doc = lambda doctype, name: documents[doctype]
        doc = application()
        doc.applicant = 'CUSTOMER-TEST'
        doc.custom_first_name = 'Preserved'
        self.adapter.apply_application(doc)
        assert doc.custom_first_name == 'Preserved'
        assert doc.applicant_name == 'Preserved X Borrower'
        assert doc.city == 'Test city'
        assert doc.applicant_phone_number == '+679-1234567'
        assert customer == before

    def test_selected_dsr_is_not_written_by_a_default_calculation(self):
        doc = application()
        doc.custom_flexi_selected_dsr = None
        values = new_values(doc)
        assert 'custom_flexi_selected_dsr' not in values
