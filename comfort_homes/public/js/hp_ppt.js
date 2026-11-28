// 7.1.6: Bank PPT Authority on the Loan (HP menu): prepare -> verify (second person) -> print -> stamped copy back.
frappe.ui.form.on("Loan", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || !frm.doc.loan_application) return;
		const has = (roles) => roles.some((r) => frappe.user.has_role(r));
		const prepare = ["Loan Officer", "Loan Manager", "Disbursement Officer", "Accounts Officer", "Accounts Manager", "Finance Manager", "System Manager"];
		const verify = ["Disbursement Officer", "Accounts Officer", "Accounts Manager", "Finance Manager", "System Manager"];
		const status = frm.doc.custom_hp_ppt_status || "";
		const call = (method, args, done) => frappe.call({ method, args, freeze: true }).then(() => { frm.reload_doc(); if (done) done(); });

		if (has(prepare)) {
			frm.add_custom_button(status ? __("Refresh PPT Authority") : __("Prepare PPT Authority"), () => {
				const run = (reset) => call("comfort_homes.hp_lending.hp_ppt.prepare", { loan: frm.doc.name, reset: reset ? 1 : 0 }, () => {
					frappe.show_alert({ message: __("Bank PPT Authority prepared. Check the details in the Bank PPT Authority section, then ask a second person to verify."), indicator: "blue" });
					frm.scroll_to_field("custom_hp_ppt_customer_bank");
				});
				if (!status) return run(false);
				frappe.confirm(__("Refill every detail from the loan? Choose No to fill only blank details."), () => run(true), () => run(false));
			}, __("HP"));
		}
		if (status === "Prepared" && has(verify) && frm.doc.custom_hp_ppt_prepared_by !== frappe.session.user) {
			frm.add_custom_button(__("Verify PPT Authority"), () => {
				frappe.confirm(__("Confirm the customer's bank, account number, amount, frequency, dates and narration ({0}) match the customer's bank documents?", [frm.doc.custom_hp_ppt_reference]),
					() => call("comfort_homes.hp_lending.hp_ppt.verify", { loan: frm.doc.name }));
			}, __("HP"));
		}
		if (status) {
			frm.add_custom_button(__("Print PPT Authority"), () => {
				const q = new URLSearchParams({ doctype: "Loan", name: frm.doc.name, format: "HP Bank PPT Authority", no_letterhead: 1 });
				window.open(`/printview?${q}`);
			}, __("HP"));
			const colour = { Prepared: "orange", Verified: "blue", "Bank Confirmed": "green" }[status];
			frm.dashboard.add_indicator(__("Bank PPT: {0}", [__(status)]), colour);
		}
	},
});
