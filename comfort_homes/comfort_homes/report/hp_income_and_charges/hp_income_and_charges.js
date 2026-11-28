frappe.query_reports["HP Income and Charges"] = {
	filters: [
		{fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.month_start()},
		{fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today()},
		{fieldname: "location", label: __("Location"), fieldtype: "Link", options: "Cost Center"},
		{fieldname: "show_all", label: __("Show loans with no amounts"), fieldtype: "Check", default: 0},
	],
	formatter(value, row, column, data, default_formatter) {
		const out = default_formatter(value, row, column, data);
		if (column.fieldname === "loan" && data && data.loan && data.loan.startsWith("ACC-LOAN"))
			return `<a href="/app/loan/${encodeURIComponent(data.loan)}">${frappe.utils.escape_html(data.loan)}</a>`;
		return out;
	},
};
