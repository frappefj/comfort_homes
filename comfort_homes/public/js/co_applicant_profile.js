/* Linked profile only: no proposed loan, instalments, approval amount or PO. */
(() => {
    function refreshProfile(frm) {
        if (frm.doctype !== "Co-Applicant" || frm.doc.docstatus !== 0) return;
        const fields = window.comfort_hp_math.profile(frm.doc).fields;
        for (const [field, value] of Object.entries(fields)) {
            if (frm.fields_dict[field] && frm.doc[field] !== value) frm.set_value(field, value);
        }
        for (const field of ["custom_flexi_income_entries", "custom_flexi_living_expenses", "custom_flexi_finance_obligations"]) frm.refresh_field(field);
    }
    function updateAge(frm) {
        const dob = frm.doc.custom_flexi_date_of_birth;
        if (!dob) { frm.set_value("custom_flexi_age", 0); return; }
        const birth = frappe.datetime.str_to_obj(dob), today = frappe.datetime.str_to_obj(frappe.datetime.get_today());
        let age = today.getFullYear() - birth.getFullYear();
        if (today.getMonth() < birth.getMonth() || (today.getMonth() === birth.getMonth() && today.getDate() < birth.getDate())) age--;
        frm.set_value("custom_flexi_age", Math.max(age, 0));
    }
    frappe.ui.form.on("Co-Applicant", {custom_flexi_date_of_birth: updateAge, validate: refreshProfile,
        custom_flexi_income_entries_remove: refreshProfile, custom_flexi_living_expenses_remove: refreshProfile,
        custom_flexi_finance_obligations_remove: refreshProfile});
    frappe.ui.form.on("Flexi Income Entry", {amount: refreshProfile, frequency: refreshProfile, income_type: refreshProfile});
    frappe.ui.form.on("Flexi Living Expense", {expense_amount: refreshProfile, frequency_per_month: refreshProfile});
    frappe.ui.form.on("Flexi Finance Obligation", {amount: refreshProfile, frequency: refreshProfile});
})();
