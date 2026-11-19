/* Read-only financial status and native-form links; no auto submit or permission bypass. */
(() => {
    const depositStages = ["Approved", "Contract Signing", "Awaiting Customer Signature"];
    const later = ["Ready for Disbursement", "Loan Activated", "LPO Pending Approval", "LPO Approved",
        "LPO Sent to Supplier", "Delivered - Awaiting Documents", "Active Loan Account", "Closed"];
    frappe.ui.form.on("Loan Application", {
        refresh(frm) {
            if (frm.is_new()) return;
            const state = frm.doc.workflow_state;
            if (!depositStages.includes(state) && !later.includes(state)) return;
            const group = __("Financial Handoff");
            const requestedName = frm.doc.name;
            if (depositStages.includes(state) && !frm.doc.custom_hp_deposit_payment_entry &&
                frappe.model.can_create("Payment Entry")) {
                frm.add_custom_button(__("Record Deposit Payment"), () => {
                    if (frm.is_dirty()) return frappe.msgprint(__("Save before recording the deposit."));
                    frappe.call({method:"comfort_homes.api.create_deposit_payment_entry", type:"POST",
                        args:{loan_application:frm.doc.name}, freeze:true}).then(r => {
                        if (r.message?.name) frappe.set_route("Form", "Payment Entry", r.message.name);
                    });
                }, group);
            }
            frappe.call({method:"comfort_homes.hp_lending.hp_workflow.financial_handoff",
                args:{loan_application:frm.doc.name}}).then(r => {
                if (!r.message || frm.doc.name !== requestedName) return;
                const d = r.message;
                if (d.deposit) {
                    const label = d.deposit.docstatus === 1 ? __("View Submitted Deposit") : __("Complete Draft Deposit");
                    frm.add_custom_button(label, () => frappe.set_route("Form", "Payment Entry", d.deposit.name), group);
                }
                (d.loans || []).forEach(loan => {
                    const label = loan.docstatus === 0 ? __("Open Draft Loan — Submit Here") : __("Open Loan — Disbursement");
                    frm.add_custom_button(label, () => frappe.set_route("Form", "Loan", loan.name), group);
                    if (loan.docstatus === 0 || !loan.disbursed_amount) {
                        frm.dashboard.set_headline_alert(__("Loan not financially disbursed. Open the native Loan, submit it, then Create → Loan Disbursement."), "orange");
                    }
                });
                (d.disbursements || []).forEach(v => frm.add_custom_button(
                    v.docstatus === 1 ? __("View Submitted Disbursement") : __("Complete Draft Disbursement"),
                    () => frappe.set_route("Form", "Loan Disbursement", v.name), group));
                (d.purchase_orders || []).forEach(v => frm.add_custom_button(__("Open LPO"),
                    () => frappe.set_route("Form", "Purchase Order", v.name), group));
            });
        },
    });
})();
