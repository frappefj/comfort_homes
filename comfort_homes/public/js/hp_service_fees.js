/* Independent fees: open a native draft only. No messaging, submission or loan changes. */
(() => {
    if (window.__flexiServiceFeesRegistered) return;
    window.__flexiServiceFeesRegistered = true;
    function prepare(frm) {
        if (frm.is_dirty()) return frappe.msgprint(__("Save first. Fee billing is separate from instalments."));
        frappe.prompt([{fieldname:"fee_item",label:__("Recorded service event"),fieldtype:"Select",options:["SMS Charge","Call Charge","Field Visit"],reqd:1}], choice => {
            frappe.call({method:"comfort_homes.hp_lending.hp_service_fees.draft_values",args:{loan:frm.doc.name,fee_item:choice.fee_item}}).then(r => {
                if (!r.message) return;
                frappe.model.with_doctype("Sales Invoice", () => {
                    const doc = frappe.model.get_new_doc("Sales Invoice");
                    Object.assign(doc,r.message,{name:doc.name,__islocal:1});
                    const items = doc.items; doc.items=[];
                    items.forEach(values => Object.assign(frappe.model.add_child(doc,"Sales Invoice Item","items"),values));
                    frappe.set_route("Form","Sales Invoice",doc.name);
                });
            });
        },__("Prepare independent fee invoice"),__("Open draft for review"));
    }
    frappe.ui.form.on("Loan",{refresh(frm){
        if (!frm.is_new() && frm.doc.docstatus===1 && frappe.model.can_create("Sales Invoice"))
            frm.add_custom_button(__("Prepare Independent Fee Invoice"),()=>prepare(frm),__("Servicing"));
    }});
    frappe.ui.form.on("Sales Invoice",{
        posting_date(frm){if(frm.doc.custom_hp_independent_fee && frm.doc.docstatus===0) frm.set_value("value_date",frm.doc.posting_date);},
        refresh(frm){if(frm.doc.custom_hp_independent_fee) frm.dashboard.set_headline_alert(__("Independent debt: use an invoice-allocated Payment Entry, never Loan Repayment. Value Date must equal Invoice Date. Billing requires authorised review."),"blue");}
    });
})();
