"""Native-document generation model; not a saved Frappe workflow acceptance test."""
from unittest.mock import Mock
import pytest
from comfort_homes.tests.test_gross_controls import env, Row, COMPANY


def application():
    return Row(name='APP',company=COMPANY,applicant='C',docstatus=1,workflow_state='LPO Pending Approval',custom_flexi_cash_price=849,
        custom_flexi_purchase_items=[Row(product_description='Goods',quantity=1,unit_price=849),Row(product_description='Gift',quantity=1,unit_price=0)])


def test_legacy_remarks_only_po_is_not_duplicated_or_relinked(env,monkeypatch):
    f,load=env;m=load('hp_lending/hp_workflow.py');monkeypatch.setattr(m,'_require_disbursed',Mock())
    f.get_all.side_effect=[[],[Row(name='OLD',remarks='Loan Application: APP | Loan: L')]]
    with pytest.raises(ValueError,match='legacy LPO'):m.generate_lpo(application())
    f.get_doc.assert_not_called();f.db.commit.assert_not_called()


def test_fresh_generator_native_insert_keeps_zero_price_row(env,monkeypatch):
    f,load=env;m=load('hp_lending/hp_workflow.py');monkeypatch.setattr(m,'_require_disbursed',Mock());f.get_all.side_effect=[[],[]]
    po=Row(name='PO',items=[],grand_total=849,check_permission=Mock(),insert=Mock())
    def get_doc(values):
        po.update(values)
        po.append=lambda field,value:po[field].append(Row(value))
        return po
    f.get_doc.side_effect=get_doc
    assert m.generate_lpo(application())=='PO'
    assert [r.rate for r in po['items']]==[849,0]
    po.insert.assert_called_once_with();po.check_permission.assert_called_once_with('create')
    f.db.commit.assert_not_called()


def test_generator_role_denial_precedes_document_creation(env,monkeypatch):
    f,load=env;m=load('hp_lending/hp_workflow.py');f.only_for.side_effect=ValueError('role denied')
    with pytest.raises(ValueError,match='role denied'):m.generate_lpo(application())
    f.get_doc.assert_not_called();f.get_all.assert_not_called()
