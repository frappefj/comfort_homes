"""Narrow native extension: gross HP interest is already in the Loan balance."""

import frappe

from comfort_homes.hp_lending.hp_settings import COMPANY


class GrossHPLoanMixin:
    def set_missing_fields(self):
        # Lending 16.8.1 treats zero as missing and defaults from Loan Product.
        # Keep all native defaults, then restore zero before subsequent native
        # validation and schedule generation. No posted record is edited here.
        super().set_missing_fields()
        if (self.get("company") == COMPANY and self.get("loan_application")
                and not self.get("is_imported") and self.get("custom_hp_contract_rate")):
            previous = self.get_doc_before_save()
            if previous and previous.docstatus == 1 and previous.rate_of_interest:
                frappe.throw("Existing HP loan has non-zero servicing interest; review it before changing financial records.")
            self.rate_of_interest = 0
