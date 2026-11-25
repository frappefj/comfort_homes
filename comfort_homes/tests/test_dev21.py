"""dev21: Living Expenses table full width on the Loan Application (Desk only)."""
import json
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
FIXTURES = APP / "fixtures"
LA = "Loan Application"


def _json(name):
	return json.loads((FIXTURES / name).read_text())


def _order():
	setter = {x["name"]: x for x in _json("property_setter.json")}[f"{LA}-main-field_order"]
	return json.loads(setter["value"])


def _fields():
	return {x["fieldname"]: x for x in _json("custom_field.json") if x["dt"] == LA}


def test_living_expenses_table_has_its_own_full_width_section():
	order = _order()
	i = order.index("custom_flexi_living_expenses")
	assert order[i - 1] == "custom_flexi_expense_section"
	assert order[i + 1] == "custom_flexi_expense_summary_section"
	fields = _fields()
	# Nothing else (no column break) shares the table's section, so it spans the page.
	assert fields["custom_flexi_expense_summary_section"]["fieldtype"] == "Section Break"
	assert fields["custom_flexi_expense_summary_section"]["insert_after"] == "custom_flexi_living_expenses"


def test_totals_net_income_and_cira_stay_together_below_the_table():
	order = _order()
	start = order.index("custom_flexi_expense_summary_section")
	assert order[start:start + 5] == [
		"custom_flexi_expense_summary_section",
		"custom_flexi_total_monthly_expenses",
		"custom_flexi_expense_column",
		"custom_flexi_net_monthly_income",
		"custom_flexi_income_basis",
	]
	assert order[start + 5] == "custom_flexi_f3_tab"
	assert _fields()["custom_flexi_total_monthly_expenses"]["insert_after"] == "custom_flexi_expense_summary_section"


def test_web_form_and_co_applicant_are_unchanged():
	web = _json("web_form.json")[0]
	assert "custom_flexi_expense_summary_section" not in {f["fieldname"] for f in web["web_form_fields"]}
	co = _json("00_co_applicant_schema.json")[0]
	assert "custom_flexi_expense_summary_section" not in {f.get("fieldname") for f in co.get("fields", [])}
