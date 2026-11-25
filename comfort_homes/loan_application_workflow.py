"""Workflow safeguards and authoritative calculations for Flexi applications."""

from math import ceil

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from comfort_homes.api import _existing_customer_match
from comfort_homes.hp_lending import hp_math


CONTRACT_STATES = {"Contract Signing", "Awaiting Customer Signature"}
POST_CONTRACT_STATES = {"Ready for Disbursement", "Signed Contract Pending Compliance"}


def _money(value):
	return round(flt(value), 2)


def _monthly_factor(frequency):
	return {
		"Weekly": 52 / 12,
		"Fortnightly": 26 / 12,
		"Bi-monthly": 2,
		"Monthly": 1,
	}.get(frequency, 1)


def _set(doc, fieldname, value):
	"""Only write fields that exist on this site's customised Loan Application."""
	if doc.meta.has_field(fieldname):
		doc.set(fieldname, value)


def _set_employment_period(doc):
	"""Calculate the existing Employment Period from Employment Start Date."""
	start_date = doc.get("custom_flexi_employment_start_date")
	if not start_date:
		return
	start = getdate(start_date)
	current = getdate(today())
	if start > current:
		frappe.throw(_("Employment Start Date cannot be in the future."))
	months = (current.year - start.year) * 12 + current.month - start.month
	if current.day < start.day:
		months -= 1
	months = max(months, 0)
	years, remaining_months = divmod(months, 12)
	period = _("{0} {1}").format(years, _("year") if years == 1 else _("years"))
	if remaining_months:
		period += _(" {0} {1}").format(
			remaining_months, _("month") if remaining_months == 1 else _("months")
		)
	_set(doc, "custom_flexi_employment_period", period)


def calculate_financials(doc, method=None):
	"""Preserved origination inputs and post-native-validation finalisation."""
	from comfort_homes.origination import apply_application
	return apply_application(doc, method)


def validate_public_application_not_existing_customer(doc, method=None):
	"""Public applications must not create a duplicate customer application."""
	if not frappe.flags.in_web_form:
		return
	if doc.get("applicant"):
		frappe.throw(_("Existing customers must apply from the backend."))
	match = _existing_customer_match(doc.get("custom_tin_number"), doc.get("applicant_email_address"))
	from comfort_homes.co_applicant import find_by_tin

	if find_by_tin(doc.get("custom_tin_number")):
		frappe.throw(
			_("A matching Co-Applicant already exists. Please apply from the backend."),
			title=_("Existing Co-Applicant"),
		)
	if match["exists"]:
		matched_by = match.get("matched_by") or []
		identifier = matched_by[0] if len(matched_by) == 1 else _("TIN Number or Email Address")
		frappe.throw(
		_("A customer already exists with this {0}. Please apply from the backend.").format(identifier),
			title=_("Existing Customer"),
		)


def validate_branch_location(doc, method=None):
	"""A branch/location must be a selectable child Cost Center, never a group."""
	branch_location = doc.get("custom_branchlocation")
	if not branch_location:
		return

	row = frappe.db.get_value("Cost Center", branch_location, ["is_group", "company"], as_dict=True)
	if not row or row.is_group or row.company != doc.company:
		frappe.throw(
			_("Select a leaf branch/location Cost Center for this company."),
			title=_("Child Cost Center Required"),
		)
