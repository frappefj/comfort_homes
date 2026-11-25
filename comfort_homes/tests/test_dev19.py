"""dev19: Loan Bank Reconciliation matches the HP account number held on converted customers."""
from unittest.mock import Mock

import pytest

from comfort_homes.tests.test_gross_controls import Row, env  # noqa: F401  (fixture)

CUSTOMERS = {
    ("custom_ch_account_reference_no", "NAM202505"): ["Adi Drodro"],
    ("custom_ch_account_reference_no", "2026-00366"): ["Asinate Bula"],
    ("custom_ch_account_reference_no", "NAM202501"): ["Nazil Khan"],
    ("custom_tin_number", "125054009"): ["Adleen Lal"],
}
LOANS = {"Adi Drodro": ["LOAN-1"], "Asinate Bula": ["LOAN-2"], "Nazil Khan": ["LOAN-3"], "Adleen Lal": ["LOAN-4"]}


def matcher(env, columns=("custom_ch_account_reference_no", "custom_tin_number")):
    f, load = env
    module = load("api.py")
    f.db.has_column = Mock(side_effect=lambda doctype, field: field in columns or doctype == "Loan")

    def get_all(doctype, filters=None, fields=None, **kwargs):
        if doctype == "Customer":
            (field, value), = [(k, v) for k, v in filters.items()]
            return [Row(name=n) for n in CUSTOMERS.get((field, value), [])]
        if doctype == "Loan" and "applicant" in (filters or {}):
            return [Row(name=n) for n in LOANS.get(filters["applicant"], [])]
        return []
    f.get_all = Mock(side_effect=get_all)
    module._customer_name_from_description = Mock(return_value="")
    return module


@pytest.mark.parametrize("narration,customer,loan", [
    ("EFTPOS DEP NAM202505", "Adi Drodro", "LOAN-1"),
    ("NAM202501 NAZIL KHAN", "Nazil Khan", "LOAN-3"),
    ("DIRECT CREDIT 2026-00366 BULA", "Asinate Bula", "LOAN-2"),
    ("DIRECT CREDIT 125054009 ADLEEN LAL", "Adleen Lal", "LOAN-4"),
])
def test_hp_account_number_or_tin_identifies_customer_and_loan(env, narration, customer, loan):
    module = matcher(env)
    card = module.extract_card_id(narration)
    assert module._customer_and_loan(narration, card)[:2] == (customer, loan)


def test_two_different_customers_in_one_narration_is_not_guessed(env):
    module = matcher(env)
    narration = "NAM202505 NAM202501"
    assert module._customer_and_loan(narration, module.extract_card_id(narration)) == ("", "", 0)


def test_site_without_the_reference_field_keeps_previous_behaviour(env):
    module = matcher(env, columns=("custom_tin_number",))
    assert module._customer_and_loan("EFTPOS DEP NAM202505", "NAM202505") == ("", "", 0)


def test_dates_are_not_read_as_hp_accounts(env):
    module = matcher(env)
    assert module.HP_ACCOUNT_PATTERN.findall("2026-11-21 TRANSFER 21/11/2026") == []
