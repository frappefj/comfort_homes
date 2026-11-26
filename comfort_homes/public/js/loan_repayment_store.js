// 7.1.3: a repayment taken in store defaults to the user's store; the server books it to that store's
// cash account (hp_collections.counter_receipt_account).  Bank and M-PAiSA receipts come from the
// reconciliation, which sets the bank account instead, so the store is left blank for them.
frappe.ui.form.on("Loan Repayment", {
	onload(frm) {
		if (!frm.is_new() || frm.doc.custom_hp_received_at || frm.doc.bank_account) return;
		const store = frappe.defaults.get_user_default("Cost Center");
		if (store) frm.set_value("custom_hp_received_at", store);
	},
	custom_hp_received_at(frm) {
		if (frm.doc.custom_hp_received_at) {
			frm.set_df_property("payment_account", "description",
				__("Booked to the cash account of {0} when saved.", [frm.doc.custom_hp_received_at]));
		}
	},
});
