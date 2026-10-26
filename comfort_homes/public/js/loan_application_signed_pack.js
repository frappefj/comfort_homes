/* Printable Customer Documents — with co-applicant support.
 *
 * Phase 4 of the co-applicant integration.
 *
 * The application child table is the only source of the print-format list.
 * When a co-applicant exists (custom_existing_co_applicant is set), the
 * signing dialogs include co-hirer signature fields.  The API methods in
 * agreement_signing.py handle saving those signatures before rendering.
 */

function comfort_printable_customer_documents(frm) {
	return (frm.doc.custom_customer_documents || []).filter((row) =>
		row.document_code !== "REQ" && row.document_template !== "LDT-00007"
	);
}

function comfort_document_title(row) {
	if (row.document_name) return row.document_name;
	const titles = {
		"HP-AGREEMENT": __("Hire Purchase Agreement"),
		"SCHEDULE-1": __("Contents of Credit Contract"),
		"FORM-1": __("Information Statement"),
		"FORM-2": __("Form 2"),
		"FINANCIAL-OBLIGATIONS": __("Summary of Financial Obligations"),
		"CONSENT-FORM": __("Consent Form"),
	};
	if (titles[row.document_code]) return titles[row.document_code];
	return row.document_code || __("Customer Document");
}

function comfort_open_print_format(frm, row) {
	if (!row.document_template) {
		frappe.msgprint(__("This customer-document row is not linked to a document template."));
		return;
	}
	frappe.call({
		method: "comfort_homes.signed_pack.get_document_print_url",
		args: { loan_application: frm.doc.name, document_template: row.document_template },
		freeze: true,
		freeze_message: __("Preparing document…"),
		callback(r) {
			if (r.message?.url) window.open(r.message.url, "_blank");
		},
	});
}

// ---------------------------------------------------------------------------
// Co-applicant helpers for signing dialogs (Phase 4)
// ---------------------------------------------------------------------------

/**
 * Check whether the current Loan Application has a linked co-applicant.
 * Returns true when custom_existing_co_applicant is populated.
 */
function comfort_has_co_applicant(frm) {
	return Boolean(frm.doc.custom_existing_co_applicant);
}

/**
 * Fetch the co-applicant name asynchronously and call back with it.
 * Falls back to empty string if the fetch fails.
 */
function comfort_fetch_co_applicant_name(frm, callback) {
	if (!comfort_has_co_applicant(frm)) return callback("");
	frappe.db.get_value(
		"Co-Applicant",
		frm.doc.custom_existing_co_applicant,
		"applicant_name"
	).then((r) => {
		callback((r && r.message && r.message.applicant_name) || "");
	}).catch(() => callback(""));
}

/**
 * Build signing dialog fields.  When a co-applicant exists, adds
 * co-hirer name (read-only) and co-hirer signature fields.
 *
 * @param {object} frm - The current Frappe form instance
 * @param {string} co_applicant_name - Pre-fetched co-applicant name
 * @returns {Array} Frappe dialog field definitions
 */
function comfort_signing_dialog_fields(frm, co_applicant_name) {
	const fields = [
		{ fieldtype: "Section Break", label: __("Hirer Signature") },
		{ fieldname: "hirer_name", label: __("Hirer Name"), fieldtype: "Data",
		  read_only: 1, default: frm.doc.applicant_name || "" },
		{ fieldname: "signature", label: __("Hirer Signature"), fieldtype: "Signature", reqd: 1 },
	];

	if (comfort_has_co_applicant(frm)) {
		fields.push(
			{ fieldtype: "Section Break", label: __("Co-Hirer Signature") },
			{ fieldname: "co_hirer_name", label: __("Co-Hirer Name"), fieldtype: "Data",
			  read_only: 1, default: co_applicant_name || "" },
			{ fieldname: "co_hirer_signature", label: __("Co-Hirer Signature"),
			  fieldtype: "Signature",
			  description: __("Leave blank if the co-hirer is not present to sign.") }
		);
	}

	return fields;
}

// The database Client Script from earlier releases defines these global helpers
// for electronic signing. Override them so old installations become print-only.
window.flexi_open_customer_document = comfort_open_print_format;
window.flexi_add_document_buttons = function (frm) {
	comfort_printable_customer_documents(frm).forEach((row) => {
		frm.add_custom_button(
			__("Print {0}", [comfort_document_title(row)]),
			() => comfort_open_print_format(frm, row),
			__("Customer Documents")
		);
	});
};

frappe.ui.form.on("Loan Application", {
	refresh(frm) {
		if (frm.is_new()) return;
		// Remove legacy signing and signed/blank-pack buttons before replacing
		// them with the exact configured customer-document list.
		frm.clear_custom_buttons(__("Customer Documents"));
		window.flexi_add_document_buttons(frm);
		frm.add_custom_button(
			__("Download All Documents"),
			() => comfort_contract_pdf(frm, "comfort_homes.signed_pack.make_document_pack", __("Customer Documents")),
			__("Customer Documents")
		);
	},
});

// Keep the deposit action with this confirmed Loan Application Desk asset.
// The same per-form guard is used by the workflow-notes script, so it will
// never add a duplicate action on sites that load both scripts.
frappe.ui.form.on("Loan Application", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (!["Approved", "Contract Signing", "Awaiting Customer Signature"].includes(frm.doc.workflow_state)) return;
		if (frm.__comfortDepositInvoiceActionV2Registered) return;

		const addButton = () => {
			frm.add_custom_button(__("Create Deposit Sales Invoice"), () => {
				frappe.call({
					method: "comfort_homes.api.create_deposit_sales_invoice",
					args: { loan_application: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating draft deposit invoice..."),
					callback(response) {
						if (response.message?.name) frappe.set_route("Form", "Sales Invoice", response.message.name);
					},
				});
			});
			frm.__comfortDepositInvoiceActionV2Registered = true;
		};

		const invoice = frm.doc.custom_deposit_sales_invoice;
		if (!invoice) return addButton();
		frappe.db.get_value("Sales Invoice", invoice, "docstatus").then((result) => {
			const status = result?.message?.docstatus;
			if (status === 2 || status === null || status === undefined) addButton();
		});
	},
});

function comfort_print_pack_html(win, title, documents) {
	if (!win) {
		frappe.msgprint(__("Allow pop-ups for this site to open the contract pack."));
		return;
	}
	win.document.open();
	win.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${frappe.utils.escape_html(title)}</title>
		<style>
			@page { size: A4; margin: 10mm; }
			html, body { margin: 0; padding: 0; background: #fff; }
			.pack-doc + .pack-doc { break-before: page; page-break-before: always; }
		</style></head><body></body></html>`);
	win.document.close();
	documents.forEach((d) => {
		const host = win.document.createElement("div");
		host.className = "pack-doc";
		win.document.body.appendChild(host);
		host.attachShadow({ mode: "open" }).innerHTML = d.html;
	});
	setTimeout(() => { win.focus(); win.print(); }, 800);
}

function comfort_contract_pdf(frm, method, label) {
	// Open the tab inside the click so pop-up blockers allow it.
	const win = window.open("", "_blank");
	frappe.call({
		method,
		args: { loan_application: frm.doc.name },
		freeze: true,
		freeze_message: __("Preparing {0}…", [label]),
		callback(r) {
			const result = r.message;
			if (!result) {
				if (win) win.close();
				return;
			}
			if (result.mode === "html") {
				// No PDF engine on the server: show the documents together for printing.
				comfort_print_pack_html(win, `${label} - ${frm.doc.name}`, result.documents);
				frappe.show_alert({
					message: __("PDF generation is unavailable on this server. Use Print → Save as PDF to keep one file."),
					indicator: "orange",
				}, 10);
				return;
			}
			if (win) win.location = result.file_url;
			else window.open(result.file_url, "_blank");
			frappe.show_alert({
				message: result.pages
					? __("{0} created: {1} documents, {2} pages.", [label, result.documents.length, result.pages])
					: __("{0} ready: {1} documents.", [label, result.documents.length]),
				indicator: "green",
			}, 7);
			frm.reload_doc();
		},
		error() {
			if (win) win.close();
		},
	});
}
