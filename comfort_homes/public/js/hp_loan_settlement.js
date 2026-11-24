// dev26: customer credit and early settlement on the Loan form (Flexi HP loans only).
frappe.ui.form.on("Loan", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || !frm.doc.loan_application) return;
		const allowed = ["Accounts Manager", "Accounts Officer", "Finance Manager", "Loan Manager", "System Manager"];
		if (!allowed.some((r) => frappe.user.has_role(r))) return;
		const finance = frappe.user.has_role("Finance Manager") || frappe.user.has_role("System Manager");
		const money = (v) => format_currency(v || 0, "FJD");

		frappe.call({ method: "comfort_homes.hp_lending.hp_credit.settlement_position", args: { loan: frm.doc.name } }).then((r) => {
			const p = r.message || {};
			if (p.credit > 0) frm.dashboard.set_headline_alert(`Customer credit held: <b>${money(p.credit)}</b> (pays instalments on their due dates)`, "blue");
		});

		frm.add_custom_button(__("Settlement Quote"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Settlement Quote"),
				fields: [{ fieldname: "as_of", fieldtype: "Date", label: __("Settle On"), default: frappe.datetime.get_today(), reqd: 1 },
					{ fieldname: "out", fieldtype: "HTML" }],
				primary_action_label: __("Calculate"),
				primary_action(v) {
					frappe.call({ method: "comfort_homes.hp_lending.hp_credit.settlement_position", args: { loan: frm.doc.name, as_of: v.as_of } }).then((r) => {
						const p = r.message;
						d.fields_dict.out.$wrapper.html(`<table class="table table-sm">
							<tr><td>Balance</td><td class="text-right">${money(p.balance)}</td></tr>
							<tr><td>Rule of 78 rebate</td><td class="text-right">- ${money(p.rebate)}</td></tr>
							<tr><td>Customer credit held</td><td class="text-right">- ${money(p.credit)}</td></tr>
							<tr><th>To pay to settle</th><th class="text-right">${money(p.to_pay)}</th></tr>
							${p.refund_after_settlement ? `<tr><td>Refund to customer after settlement</td><td class="text-right">${money(p.refund_after_settlement)}</td></tr>` : ""}
						</table>`);
					});
				},
			});
			d.show();
		}, __("HP"));

		if (!finance) return;
		if (["Disbursed", "Partially Disbursed"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Confirm Settlement"), () => {
				frappe.confirm(__("Book the Rule of 78 rebate, settle {0} from the customer credit held and move any surplus to HP Refund Payable?", [frm.doc.name]), () => {
					frappe.call({ method: "comfort_homes.hp_lending.hp_credit.confirm_settlement", args: { loan: frm.doc.name }, freeze: true }).then((r) => {
						const x = r.message;
						frappe.msgprint(__("Rebate {0}; settled {1}; refund owed {2}. Loan status: {3}.", [money(x.rebate), money(x.settled), money(x.refund_owed), x.status]));
						frm.reload_doc();
					});
				});
			}, __("HP"));
		}
		if (frm.doc.status === "Closed") {
			frm.add_custom_button(__("Refund Rebate"), () => {
				frappe.confirm(__("Book the Rule of 78 rebate owed on the early payoff of {0} as a refund to the customer?", [frm.doc.name]), () => {
					frappe.call({ method: "comfort_homes.hp_lending.hp_credit.refund_rebate_after_closure", args: { loan: frm.doc.name }, freeze: true }).then((r) => {
						frappe.msgprint(__("Rebate refund of {0} booked to HP Refund Payable ({1}).", [money(r.message.rebate), r.message.entry]));
						frm.reload_doc();
					});
				});
			}, __("HP"));
		}
	},
});
