/* Read-only panel: every co-applicant tied to this application, with a link to review it. */
frappe.ui.form.on("Loan Application", {
	refresh(frm) {
		if (frm.is_new() || !frm.fields_dict.custom_existing_co_applicant) return;
		frappe.call({
			method: "comfort_homes.document_controls.co_applicant_status",
			args: { application: frm.doc.name },
		}).then((r) => {
			const rows = r.message || [];
			frm.dashboard.clear_headline && frm.dashboard.clear_headline();
			if (!rows.length) return;
			const yes = (v) => (v ? __("Yes") : __("No"));
			const html = rows.map((c) =>
				`<a href="/app/co-applicant/${encodeURIComponent(c.name)}">${frappe.utils.escape_html(c.applicant_name || c.name)}</a>`
				+ ` &mdash; ${frappe.utils.escape_html(c.workflow_state || "")}`
				+ ` | CIRA signed: ${yes(c.cira_signed)} | KYC complete: ${yes(c.kyc_complete)}`).join("<br>");
			const pending = rows.some((c) => c.workflow_state !== "Approved");
			frm.dashboard.set_headline(`<b>${__("Co-applicants")}</b><br>${html}`, pending ? "orange" : "green");
		});
	},
});
