/* Tell the reviewer which main application this co-applicant belongs to. */
frappe.ui.form.on("Co-Applicant", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.loan_application) return;
		const app = frm.doc.loan_application;
		frm.dashboard.set_headline(
			`${__("Co-applicant on")} <a href="/app/loan-application/${encodeURIComponent(app)}">${frappe.utils.escape_html(app)}</a>`
			+ ` &mdash; ${__("approve or reject here before the main application can proceed")}`, "blue");
	},
});
