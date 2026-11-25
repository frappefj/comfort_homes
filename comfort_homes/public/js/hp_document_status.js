/* Read-only consent/evidence status. Server gates remain authoritative. */
(() => {
    if (window.__flexiDocumentStatusRegistered) return;
    window.__flexiDocumentStatusRegistered = true;
    function refresh(frm) {
        if (frm.is_new() || frm.is_dirty()) return;
        const name=frm.doc.name, modified=frm.doc.modified;
        frappe.call({method:"comfort_homes.document_controls.evidence_status",args:{doctype:frm.doctype,name}}).then(r=>{
            if (!r.message || frm.doc.name!==name || frm.doc.modified!==modified || frm.is_dirty()) return;
            if (r.message.consent_current) frm.remove_custom_button(__("Read & Sign CIRA Authority Form"));
            if (r.message.assessment_present && !r.message.assessment_source_current)
                frm.dashboard.set_headline_alert(__("Assessment is stale. Generate or record current Application Print WIP before progressing."),"orange");
        });
    }
    frappe.ui.form.on("Loan Application",{refresh});
    frappe.ui.form.on("Co-Applicant",{refresh});
})();
