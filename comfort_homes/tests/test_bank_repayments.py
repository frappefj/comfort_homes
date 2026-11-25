"""Mocked repayment transaction tests; native submit/GL remains a site gate."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from comfort_homes.tests.test_gross_controls import Row, env, COMPANY


def prepared(env,monkeypatch):
    f,load=env
    module=load('api.py')
    row=Row(name='ROW',selected=1,loan='LOAN',suggested_loan='LOAN',loan_repayment=None,
            customer='CUSTOMER',transaction_date='2026-11-17',amount=73.30,
            statement_transaction_key='HP-BANK-TEST',customer_card_id='TEST')
    doc=Row(name='RECO',company=COMPANY,docstatus=0,bank_account='BANK',transactions=[row],
            check_permission=Mock(),reload=Mock(),save=Mock())
    loan=Row(applicant='CUSTOMER',company=COMPANY,check_permission=Mock())
    repayment=Row(name='PAYMENT',insert=Mock(),submit=Mock())
    f.get_doc.side_effect=lambda *args: repayment if isinstance(args[0],dict) else loan
    f.db.get_value.return_value=None
    monkeypatch.setattr(module,'_payment_account',lambda *args:'BANK-LEDGER')
    monkeypatch.setattr(module,'_cost_center_for_loan',lambda *args:'BRANCH')
    # dev26: the receipt is split by hp_credit; here the whole receipt is due (no payoff, no fees, no credit).
    import comfort_homes.hp_lending.hp_credit as credit
    monkeypatch.setattr(credit,'frappe',f)  # the module may have been imported under an earlier test's frappe stub
    monkeypatch.setattr(credit,'payoff_figures',lambda *a:{'balance':credit.m.d(1000),'rebate':credit.m.d(0),'credit':credit.m.d(0),'net':credit.m.d(1000)})
    monkeypatch.setattr(credit,'due_outstanding',lambda *a:credit.m.d('73.30'))
    return f,module,doc,row,repayment


def test_row_submission_uses_native_permissions_and_exact_reference(env,monkeypatch):
    f,module,doc,row,payment=prepared(env,monkeypatch)
    result=module.create_repayments(doc)
    assert result['created']==1 and row.loan_repayment=='PAYMENT'
    payment.insert.assert_called_once_with()
    payment.submit.assert_called_once_with()
    values=next(call.args[0] for call in f.get_doc.call_args_list if isinstance(call.args[0],dict))
    assert values['reference_number']=='HP-BANK-TEST'
    assert values['amount_paid']==73.30 and values['cost_center']=='BRANCH'
    assert any('tabBank Account' in call.args[0] and 'FOR UPDATE' in call.args[0] for call in f.db.sql.call_args_list)


def test_retry_reuses_only_matching_submitted_payment(env,monkeypatch):
    f,module,doc,row,payment=prepared(env,monkeypatch)
    f.db.get_value.return_value=Row(name='EXISTING',against_loan='LOAN',docstatus=1,amount_paid=73.30,value_date='2026-11-17')
    result=module.create_repayments(doc)
    assert result['created']==0 and row.loan_repayment=='EXISTING'
    payment.insert.assert_not_called()
    doc.save.assert_called_once_with()


@pytest.mark.parametrize('change',[{'against_loan':'OTHER'},{'docstatus':0},{'amount_paid':74},{'value_date':'2026-11-16'}])
def test_repeat_reference_mismatch_is_not_reallocated(env,monkeypatch,change):
    f,module,doc,row,payment=prepared(env,monkeypatch)
    previous=Row(name='EXISTING',against_loan='LOAN',docstatus=1,amount_paid=73.30,value_date='2026-11-17')
    previous.update(change);f.db.get_value.return_value=previous
    result=module.create_repayments(doc)
    assert result['created']==0 and row.status=='Error' and result['errors']
    f.db.rollback.assert_called_once_with(save_point='hp_bank_row')
    payment.insert.assert_not_called()


def test_failed_native_submission_rolls_back_the_row(env,monkeypatch):
    f,module,doc,row,payment=prepared(env,monkeypatch)
    payment.submit.side_effect=ValueError('native validation failure')
    result=module.create_repayments(doc)
    assert result['created']==0 and row.loan_repayment is None
    f.db.rollback.assert_called_once_with(save_point='hp_bank_row')
    assert row.status=='Error'


def test_wrong_customer_cannot_receive_another_loan_allocation(env,monkeypatch):
    f,module,doc,row,payment=prepared(env,monkeypatch)
    row.customer='OTHER'
    result=module.create_repayments(doc)
    assert result['skipped']==1
    payment.insert.assert_not_called()


def test_public_legacy_mutation_requires_post_even_when_write_permission_exists(env):
    f,load=env;module=load('api.py')
    f.request=SimpleNamespace(method='GET')
    with pytest.raises(ValueError,match='require POST'):
        module.check_application_access('APP',write=1)
    f.get_doc.assert_not_called()


def test_legacy_mutation_rejects_cross_application_agreement_row(env):
    f,load=env;module=load('api.py')
    f.request=SimpleNamespace(method='POST')
    app=Row(name='APP',company=COMPANY,docstatus=0,check_permission=Mock())
    f.get_doc.side_effect=[app,Row(parent='OTHER',parenttype='Loan Application',parentfield='custom_customer_documents')]
    with pytest.raises(ValueError,match='does not belong'):
        module.check_application_access('APP',write=1,agreement_row='ROW')


def test_legacy_mutation_rejects_submitted_contract_edits(env):
    f,load=env;module=load('api.py');f.request=SimpleNamespace(method='POST')
    f.get_doc.return_value=Row(name='APP',company=COMPANY,docstatus=1,check_permission=Mock())
    with pytest.raises(ValueError,match='Submitted or cancelled'):
        module.check_application_access('APP',write=1)
