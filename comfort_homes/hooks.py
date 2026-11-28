app_name = "comfort_homes"
app_title = "Comfort Homes"
app_publisher = "Comfort Home Furnishing"
app_description = "Comfort Homes lending customisations"
app_email = "support@comforthomefurnishing.com"
app_license = "mit"

# The Loan Application Web Form is database-backed, so its client script is
# installed idempotently by ``webform_setup.inject_loan_application_duplicate_check``.
# Do not also load that calculation bridge as a Web Form/website asset: multiple
# copies register competing Currency input listeners and can corrupt a value
# while a customer is typing it.
app_include_js = ["/assets/comfort_homes/js/hp_origination_math.js"]
web_include_js = ["/assets/comfort_homes/js/hp_origination_math.js", "/assets/comfort_homes/js/loan_application_branch_filter.js"]

doctype_js = {
	"Loan": ["public/js/loan_temporary_repayment_tenure.js", "public/js/hp_service_fees.js", "public/js/hp_loan_settlement.js",
		"public/js/hp_ppt.js"],
	"Loan Repayment": "public/js/loan_repayment_store.js",
    "Sales Invoice": "public/js/hp_service_fees.js",
	"Co-Applicant": ["public/js/co_applicant_profile.js", "public/js/document_evidence.js", "public/js/hp_document_status.js", "public/js/co_applicant_back_link.js"],
	"Loan Application": [
		"public/js/loan_application_branch_filter.js",
		"public/js/loan_application_co_applicant.js",
		"public/js/loan_application_workflow_notes.js",
		"public/js/loan_application_signed_pack.js",
		"public/js/document_evidence.js",
        "public/js/hp_workflow_handoff.js",
        "public/js/hp_document_status.js",
        "public/js/loan_application_co_applicants_panel.js",
        "public/js/hp_application_controls.js",
	]
}
extend_doctype_class = {
	"Loan": ["comfort_homes.hp_lending.loan_extension.GrossHPLoanMixin"],
}

override_doctype_dashboards = {
	"Loan": "comfort_homes.loan_mapping.get_loan_dashboard_data",
	"Loan Application": "comfort_homes.loan_mapping.get_loan_application_dashboard_data",
}
# Dependent setup belongs after fixture import, not after_install: Frappe 16
# invokes after_install before sync_fixtures. Migration has its own final hook.
after_migrate = "comfort_homes.webform_setup.setup_after_sync"
after_sync = "comfort_homes.webform_setup.setup_after_sync"

# Customer-document Jinja helpers.  They expose linked Co-Applicant details
# only where a print format explicitly requests them.
jinja = {
	"methods": "comfort_homes.print_helpers",
}

# Apps
# ------------------

required_apps = ["frappe", "erpnext", "lending"]

# ---------------------------------------------------------------------------
# Architecture: Gross Hire Purchase on native Frappe Lending (see docs/)
# Loan:       carries the balance after deposit at 0%; the contract rate is on the product.
# Schedule:   native (equal instalments, last absorbs rounding).
# Booking:    one journal at disbursement (hp_lifecycle) moves the contract to its gross form.
# Interest:   monthly straight-line release from Deferred Interest (hp_interest).
# Penalty:    scheduler posts a Debit Loan Balance Adjustment (hp_penalties); income on closure.
# Settlement: Rule of 78 rebate as a Credit Loan Balance Adjustment (hp_settlement).
# Deposit:    Payment Entry linked on the application; no Sales Invoice.
# ---------------------------------------------------------------------------

fixtures = [
	{
		# The approved seven customer-document layouts are version controlled,
		# rather than relying on the HTML stored in one site's database.
		"dt": "Print Format",
		"filters": [["name", "in", [
			"Hire Purchase Agreement",
			"Schedule 1",
			"Form 1",
			"Form 2",
			"Summary of Financial Obligations",
			"Consent Form",
			"Application Print WIP",
			"CIRA Authority Form",
			"HP Statement of Account",
			"HP Bank PPT Authority",
		]]],
	},
	{
		"dt": "Custom Field",
		# Flexi extends Lending's standard DocTypes through Custom Field records.
		# Keeping the complete set here makes a fresh installation reproduce the
		# same Loan Application layout and database columns as the working site.
		"filters": [["dt", "in", ["Company", "Loan Application", "Loan", "Loan Product", "Journal Entry", "Loan Disbursement", "Sales Invoice", "Purchase Order", "Purchase Invoice", "Customer", "Contact", "Loan Document Template", "Co-Applicant", "Loan Customer Agreement", "Loan Repayment", "Bank Account"]]],
	},
	{
		"dt": "Property Setter",
		"filters": [["name", "in", [
			"City-main-search_fields",
			"City-main-show_title_field_in_link",
			"City-main-title_field",
			"Province-main-search_fields",
			"Province-main-show_title_field_in_link",
			"Province-main-title_field",
			"Suburb-main-search_fields",
			"Suburb-main-show_title_field_in_link",
			"Suburb-main-title_field",
			"Loan Application-custom_flexi_payment_period-options",
			"Loan Application-applicant_name-label",
			"Loan Application-applicant_name-insert_after",
			"Loan Application-repayment_method-read_only",
			"Loan Application-repayment_method-default",
			"Loan Application-applicant_contact_info_section-label",
			"Loan Application-applicant_details_section-label",
			"Loan Application-applicant_address_details_section-label",
			"Loan Application-section_break_4-label",
			"Loan Application-repayment_info-label",
			"Loan Application-main-field_order",
		]]],
	},
	{
		"dt": "Client Script",
		"filters": [["name", "in", [
			"Flexi Loan Bank Reconciliation",
			"Loan Application",
			"Co-Applicant",
			"Flexi Applicant Name Synchronisation",
			"Flexi Applicant Name Synchronisation - Co-Applicant",
			"Flexi Employment Period Calculation",
			"Flexi Employment Period Calculation - Co-Applicant",
			"Flexi CIRA Authority Desk Flow",
			"Flexi Loan Application Calculations",
		]]],
	},
	{
		"dt": "Loan Document Template",
		"filters": [["document_code", "in", [
			"APPLICATION-ASSESSMENT",
			"FINANCIAL-OBLIGATIONS",
			"CONSENT-FORM",
			"FORM-1",
			"FORM-2",
			"HP-AGREEMENT",
			"REQ",
			"SCHEDULE-1",
		]]],
	},
	{
		# The published public application is an app fixture, not a site-only
		# Web Form.  Fresh sites therefore receive the same five-step form.
		"dt": "Web Form",
		"filters": [["name", "=", "loan-application-new"]],
	},
	{
		"dt": "Role",
		"prefix": "00_00",
		"filters": [["name", "in", ['Loan LOS User', 'Loan Officer', 'Loan Processor', 'Finance Manager', 'Director', 'Disbursement Officer', 'Accounts Officer', 'Accounts Manager', 'Loan Manager', 'Loan Reporter', 'Auditor']]],
	},
	{
		"dt": "Workflow Transition Tasks",
		"filters": [["name", "in", ["Flexi Generate LPO", "Flexi Loan Application Transition Tasks", "Flexi Approve LPO"]]],
	},
	{
		"dt": "Workflow",
		"prefix": "zz",
		"filters": [["name", "in", ["Loan Application Workflow-1", "Co-Applicant Profile Review"]]],
	},
]

doc_events = {
    "Sales Invoice": {
        "before_validate": "comfort_homes.hp_lending.hp_service_fees.defaults",
        "validate": "comfort_homes.hp_lending.hp_service_fees.validate",
        "before_submit": "comfort_homes.hp_lending.hp_service_fees.before_submit",
    },
    "Payment Entry": {"before_submit": "comfort_homes.hp_lending.hp_service_fees.validate_receipt"},
	"Loan Customer Agreement": {
		"before_save": "comfort_homes.document_controls.validate_agreement",
	},
	"Loan": {
		"before_validate": [
			"comfort_homes.loan_mapping.apply_loan_application_values",
			"comfort_homes.loan_mapping.apply_temporary_repayment_tenure",
			"comfort_homes.flexi_events.copy_hp_items_to_loan",  # 7.1: was a Server Script
		],
		# dev14 MYOB conversion: opening position of an imported, converted HP loan.
		"on_submit": "comfort_homes.hp_lending.hp_conversion.on_loan_submit",
		"before_update_after_submit": "comfort_homes.hp_lending.hp_ppt.on_loan_update",  # 7.1.6: Bank PPT Authority
	},
	"Loan Repayment": {
		"before_validate": "comfort_homes.flexi_events.default_repayment_mode",  # 7.1: was a Server Script
		"validate": [
			"comfort_homes.hp_lending.hp_collections.counter_receipt_account",  # 7.1.3: in-store receipt -> store till
			"comfort_homes.hp_lending.hp_conversion.guard_repayment_account",
		],
	},
	"Loan Disbursement": {
		"validate": "comfort_homes.hp_lending.hp_lifecycle.enforce_clearing_account",
		"before_submit": "comfort_homes.hp_lending.hp_lifecycle.validate_disbursement",
		"on_submit": "comfort_homes.hp_lending.hp_lifecycle.on_disbursement_submit",
		"before_validate": [
			"comfort_homes.loan_mapping.apply_loan_values_to_disbursement",
			"comfort_homes.loan_mapping.apply_temporary_repayment_tenure_to_disbursement",
			"comfort_homes.flexi_events.default_disbursement_mode",  # 7.1: was a Server Script
		]
	},
	"Loan Write Off": {
		"on_submit": "comfort_homes.hp_lending.hp_settlement.clear_deferred_on_write_off",
	},
	"Purchase Order": {
		"before_save": "comfort_homes.loan_mapping.apply_loan_application_purchase_values",
	},
	"Purchase Invoice": {
		"before_validate": "comfort_homes.loan_mapping.apply_purchase_invoice_loan_application",
	},
	"Loan Application": {
		"before_update_after_submit": ["comfort_homes.hp_lending.hp_workflow.validate_progress", "comfort_homes.hp_lending.hp_account_number.validate"],
		"before_save": ["comfort_homes.loan_application_workflow.calculate_financials", "comfort_homes.document_controls.validate_application", "comfort_homes.hp_lending.hp_workflow.validate_progress", "comfort_homes.hp_lending.hp_approval.bind_approval"],
		"before_submit": ["comfort_homes.loan_application_workflow.calculate_financials", "comfort_homes.document_controls.validate_application", "comfort_homes.hp_lending.hp_workflow.validate_progress", "comfort_homes.hp_lending.hp_approval.bind_approval"],
		"before_insert": "comfort_homes.loan_application_workflow.validate_public_application_not_existing_customer",
		"before_validate": [
			"comfort_homes.loan_mapping.enforce_repayment_method",
			"comfort_homes.origination.resolve_loan_purpose",
			"comfort_homes.loan_application_workflow.calculate_financials",
			"comfort_homes.co_applicant.validate_existing_co_applicant",
			"comfort_homes.loan_application_workflow.validate_branch_location",
            "comfort_homes.hp_lending.hp_intake.validate_locations",
			"comfort_homes.hp_lending.hp_lifecycle.validate_deposit_payment",
			"comfort_homes.hp_lending.hp_workflow.require_deposit_before_contract",
			"comfort_homes.hp_lending.hp_branch_number.assign_on_contract_signing",  # 7.1.6: branch HP number
			# 7.1: former Server Scripts, last as before (Frappe ran server scripts after app hooks)
			"comfort_homes.flexi_events.create_customer",
			"comfort_homes.flexi_events.prepare_loan_amount",
		],
		# 7.1: former Server Scripts "Before Save" (= validate) and "After Save" (= on_update)
		"validate": ["comfort_homes.flexi_events.default_customer_agreements", "comfort_homes.flexi_events.status_display",
			"comfort_homes.hp_lending.hp_account_number.validate"],  # 7.1.4: HP account number guard
		"on_update": "comfort_homes.flexi_events.link_documents",
	},
	"ToDo": {
		"on_update": "comfort_homes.flexi_events.sync_prepared_by",  # 7.1: was a Server Script
	},
	# 7.1.6: readable reconciliation IDs, BREC-BSP-2026-0001
	"Loan Bank Reconciliation": {
		"autoname": "comfort_homes.hp_lending.hp_reconciliation_setup.autoname",
	},
	"Co-Applicant": {
		"before_validate": "comfort_homes.origination.apply_profile",
		"before_save": ["comfort_homes.origination.apply_profile", "comfort_homes.hp_lending.hp_intake.validate_locations", "comfort_homes.document_controls.validate_profile"],
		"after_insert": "comfort_homes.co_applicant.sync_linked_loan_application",
		"on_update": "comfort_homes.co_applicant.sync_linked_loan_application",
	},
}

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "comfort_homes",
# 		"logo": "/assets/comfort_homes/logo.png",
# 		"title": "Comfort Homes",
# 		"route": "/comfort_homes",
# 		"has_permission": "comfort_homes.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/comfort_homes/css/comfort_homes.css"
# app_include_js = "/assets/comfort_homes/js/comfort_homes.js"

# include js, css files in header of web template
# web_include_css = "/assets/comfort_homes/css/comfort_homes.css"
# web_include_js = "/assets/comfort_homes/js/comfort_homes.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "comfort_homes/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "comfort_homes/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "comfort_homes.utils.jinja_methods",
# 	"filters": "comfort_homes.utils.jinja_filters"
# }

# Installation
# ------------

before_install = "comfort_homes.webform_setup.ensure_install_schema"
# Existing sites run `migrate`, not `install_app`.  This must execute before
# fixture sync so data fixtures never reference an absent custom DocType.
before_migrate = "comfort_homes.webform_setup.ensure_install_schema"

# Uninstallation
# ------------

# before_uninstall = "comfort_homes.uninstall.before_uninstall"
# after_uninstall = "comfort_homes.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "comfort_homes.utils.before_app_install"
# after_app_install = "comfort_homes.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "comfort_homes.utils.before_app_uninstall"
# after_app_uninstall = "comfort_homes.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "comfort_homes.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "comfort_homes.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------
# HP late-payment charge: the Loan Product rate (2.5% a month) on instalments overdue past the
# 7-day grace period, once per repayment date, stopping at 90 days past due.
scheduler_events = {
	"daily_long": [
		"comfort_homes.hp_lending.hp_penalties.assess_penalties",
		"comfort_homes.hp_lending.hp_penalties.realise_closed_loan_penalties",
		# dev22: interest still deferred on a closed loan is released (after any settlement rebate).
		"comfort_homes.hp_lending.hp_closure.release_on_closure",
		"comfort_homes.hp_lending.hp_conversion.assess_converted_penalties",
	],
	# 02:30 on the 1st: release deferred interest for the month that has just ended.
	"cron": {
		# dev26, 01:30 daily, after Lending raises the day's demands: pay due instalments from customer credit,
		# then re-test recent default charges against receipts posted since.
		"30 1 * * *": [
			"comfort_homes.hp_lending.hp_credit.apply_credit",
			"comfort_homes.hp_lending.hp_penalty_retest.retest_penalties",
		],
		# dev27: quarterly statements for the quarter just ended.
		"30 6 1 1,4,7,10 *": [
			"comfort_homes.hp_lending.hp_statement.quarterly_statements",
		],
		"30 2 1 * *": [
			"comfort_homes.hp_lending.hp_interest.release_deferred_interest",
			"comfort_homes.hp_lending.hp_conversion.release_converted_interest",
		],
	},
}

# Testing
# -------

# before_tests = "comfort_homes.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "comfort_homes.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "comfort_homes.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "comfort_homes.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["comfort_homes.utils.before_request"]
# after_request = ["comfort_homes.utils.after_request"]

# Job Events
# ----------
# before_job = ["comfort_homes.utils.before_job"]
# after_job = ["comfort_homes.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"comfort_homes.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

# Native workflow task; no direct status or GL edits.
workflow_methods = [{"name": "Flexi Loan Application Task", "method": "comfort_homes.flexi_events.application_transition"}, {"name": "Flexi Submit Approved LPO", "method": "comfort_homes.hp_lending.hp_workflow.submit_approved_lpo"}, {"name": "Flexi Generate Draft LPO", "method": "comfort_homes.hp_lending.hp_workflow.generate_lpo"}]
