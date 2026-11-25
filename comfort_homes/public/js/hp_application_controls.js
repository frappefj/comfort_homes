// dev27: deposit refund for withdrawn or rejected applications.
frappe.ui.form.on("Loan Application", {
	refresh(frm) {
		const roles = ["Finance Manager", "Accounts Manager", "System Manager"];
		if (!["Withdrawn", "Rejected"].includes(frm.doc.workflow_state) || !frm.doc.custom_hp_deposit_payment_entry) return;
		if (!roles.some((r) => frappe.user.has_role(r))) return;
		frm.add_custom_button(__("Refund Deposit"), () => {
			frappe.confirm(__("Create a draft refund of deposit {0} to the customer?", [frm.doc.custom_hp_deposit_payment_entry]), () => {
				frappe.call({ method: "comfort_homes.hp_lending.hp_approval.create_deposit_refund", args: { application: frm.doc.name }, freeze: true }).then((r) => {
					const pe = r.message.payment_entry;
					frappe.msgprint(__("{0} refund Payment Entry {1}. Check the bank details and submit it once paid.", [r.message.existing ? __("Existing") : __("Draft"), `<a href="/app/payment-entry/${pe}">${pe}</a>`]));
				});
			});
		}, __("HP"));
	},
});
