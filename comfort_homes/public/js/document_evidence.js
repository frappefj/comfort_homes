/* Native Desk evidence actions. No digital HP contract execution. */
(() => {
    if (window.__comfortDocumentEvidenceRegistered) return;
    window.__comfortDocumentEvidenceRegistered = true;
    const legal = ["HP-AGREEMENT", "SCHEDULE-1", "FORM-1", "FORM-2", "FINANCIAL-OBLIGATIONS", "CONSENT-FORM"];
    const isManager = () => frappe.session.user === "Administrator" || frappe.user_roles.some(role => ["Finance Manager", "Director"].includes(role));
    function saved(frm) {
        if (!frm.is_dirty()) return true;
        frappe.msgprint(__("Save your changes before recording document evidence."));
        return false;
    }
    function call(frm, action, args) {
        return frappe.call({method: "comfort_homes.document_controls." + action,
            type: "POST", args, freeze: true}).then(r => {
            if (r.message) {
                frappe.show_alert({message: __("Document evidence recorded."), indicator: "green"});
                return frm.reload_doc();
            }
        });
    }
    function upload(frm, callback) {
        new frappe.ui.FileUploader({doctype: frm.doctype, docname: frm.doc.name,
            make_attachments_public: false, allow_toggle_private: false, allow_multiple: false,
            allow_web_link: false, allow_google_drive: false, allow_take_photo: false, disable_file_browser: true,
            restrictions: {allowed_file_types: [".pdf"], max_file_size: 20 * 1024 * 1024},
            on_success: file => {
                if (!file.is_private) { frappe.msgprint(__("Use a private PDF attachment.")); return; }
                callback(file.file_url);
            }});
    }
    function recordScan(frm) {
        if (!saved(frm)) return;
        const rows = (frm.doc.custom_customer_documents || []).filter(r => legal.includes(r.document_code));
        if (!rows.length) { frappe.msgprint(__("Configure the six legal document rows first.")); return; }
        frappe.prompt([{fieldname: "row", label: __("Legal document"), fieldtype: "Select", reqd: 1,
            options: rows.map(r => ({value: r.name, label: r.document_name || r.document_code}))}], choice => {
            const row = rows.find(r => r.name === choice.row);
            if (row.status === "Completed" && !isManager()) {
                frappe.msgprint(__("A manager must explicitly supersede verified evidence.")); return;
            }
            frappe.db.get_value("Loan Document Template", row.document_template, "requires_director_signature").then(result => {
                const managerRequired = Number(result.message.requires_director_signature) === 1;
                upload(frm, file_url => {
                    const fields = [
                        {fieldname: "customer_signed_date", label: __("Last customer/co-hirer signing date"), fieldtype: "Date", reqd: 1},
                        {fieldname: "manager_signed_date", label: __("Manager ink-signing date"), fieldtype: "Date", reqd: managerRequired, hidden: !managerRequired},
                        {fieldname: "manager_name", label: __("Manager signatory shown on scan"), fieldtype: "Data", reqd: managerRequired, hidden: !managerRequired},
                        {fieldname: "confirm_execution", label: __("I checked all required individual signatures; customers signed before the manager"), fieldtype: "Check", reqd: 1},
                    ];
                    if (row.status === "Completed") fields.push(
                        {fieldname: "supersede", label: __("Explicitly supersede verified evidence; retain earlier files"), fieldtype: "Check", reqd: 1},
                        {fieldname: "reason", label: __("Reason for supersession"), fieldtype: "Small Text", reqd: 1});
                    frappe.prompt(fields, values => call(frm, "record_signed_scan", {
                        ...values, loan_application: frm.doc.name, agreement_row: row.name, file_url,
                    }), __("Record wet-signed scan"), __("Record for manager review"));
                });
            });
        }, __("Select signed document"), __("Upload private PDF"));
    }
    function recordCombinedPack(frm) {
        if (!saved(frm)) return;
        const completed = (frm.doc.custom_customer_documents || []).some(r => legal.includes(r.document_code) && r.status === "Completed");
        if (completed && !isManager()) return frappe.msgprint(__("A manager must supersede a verified pack."));
        upload(frm, file_url => {
            const fields = [
                {fieldname:"customer_signed_date", label:__("Last customer/co-hirer signing date"), fieldtype:"Date", reqd:1},
                {fieldname:"manager_signed_date", label:__("Manager ink-signing date (same day is allowed)"), fieldtype:"Date", reqd:1},
                {fieldname:"manager_name", label:__("Manager signatory shown on pack"), fieldtype:"Data", reqd:1},
                {fieldname:"confirm_all_forms", label:__("This PDF contains all six required forms for this application"), fieldtype:"Check", reqd:1},
                {fieldname:"confirm_execution", label:__("I checked every required signature; all customers signed before the manager, including same-day signing"), fieldtype:"Check", reqd:1},
            ];
            if (completed) fields.push(
                {fieldname:"supersede",label:__("Explicitly supersede verified pack; retain history"),fieldtype:"Check",reqd:1},
                {fieldname:"reason",label:__("Reason for supersession"),fieldtype:"Small Text",reqd:1});
            frappe.prompt(fields, values => call(frm, "record_signed_pack", {
                ...values, loan_application:frm.doc.name, file_url,
            }), __("Record combined signed pack"), __("Record for manager review"));
        });
    }
    function refresh(frm) {
        if (frm.is_new() || frm.doc.docstatus !== 0) return;
        const state = frm.doc.workflow_state || "Draft";
        // Keep intake and contract actions out of intermediate review/approval screens.
        if (frm.doctype === "Loan Application" && !["Draft", "Contract Signing"].includes(state)) return;
        const group = __("Document Evidence");
        frm.add_custom_button(__("Generate Individual CIRA PDF"), () => {
            if (saved(frm)) call(frm, "generate_cira_pdf", {doctype: frm.doctype, name: frm.doc.name});
        }, group);
        if (frm.doctype !== "Loan Application") return;
        if (frm.doc.custom_document_collection_complete !== "Yes") return;
        frm.add_custom_button(__("Generate Assessment PDF"), () => {
            if (saved(frm)) call(frm, "generate_assessment_pdf", {loan_application: frm.doc.name});
        }, group);
        frm.add_custom_button(__("Record Printed Assessment PDF"), () => {
            if (!saved(frm)) return;
            upload(frm, file_url => frappe.confirm(__("Confirm this PDF is the current Application Print WIP generated after KYC."),
                () => call(frm, "record_assessment_pdf", {loan_application: frm.doc.name, file_url, confirm_current: 1})));
        }, group);
        if (frm.doc.workflow_state !== "Contract Signing") return;
        frm.add_custom_button(__("Record Combined Signed Pack"), () => recordCombinedPack(frm), group);
        frm.add_custom_button(__("Record Wet-signed Scan"), () => recordScan(frm), __("Individual Forms (optional)"));
        if (isManager()) frm.add_custom_button(__("Verify Uploaded Contract Pack"), () => {
            if (!saved(frm)) return;
            frappe.confirm(__("I have inspected all six scans, all required individual signatures, and customer-first then manager execution. This records evidence review, not an electronic signature."),
                () => call(frm, "verify_contract_pack", {loan_application: frm.doc.name}));
        }, group);
    }
    frappe.ui.form.on("Loan Application", {refresh});
    frappe.ui.form.on("Co-Applicant", {refresh});
})();
