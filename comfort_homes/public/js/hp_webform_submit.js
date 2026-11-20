/* One submit owner. Uses native save/confirmation, never recursive button.click(). */
(() => {
    if (window.__flexiSubmitCoordinatorLoaded) return;
    window.__flexiSubmitCoordinatorLoaded = true;
    const accept = "frappe.website.doctype.web_form.web_form.accept";
    let phase = "idle", revision = 0, timer, currentForm;
    const text = v => String(v ?? "").trim();
    const numeric = v => Number(String(v ?? "").replace(/[^0-9.-]/g, ""));
    const fail = message => { throw new Error(message); };
    function notice(message) {
        frappe.msgprint({title: __("Application submission"), message: __(message), indicator: "orange"});
    }
    function status(value) {
        phase = value;
        document.querySelectorAll(".submit-btn").forEach(b => {
            b.disabled = !["idle", "rejected"].includes(value);
            b.setAttribute("aria-busy", ["checking", "saving"].includes(value) ? "true" : "false");
        });
    }
    function rows(form, name) { return form.fields_dict[name]?.grid?.get_data() || form.get_value(name) || []; }
    function replaceRows(form, name, values) {
        const control = form.fields_dict[name], grid = control?.grid;
        if (!grid) return;
        values.forEach((row, i) => { row.idx = i + 1; });
        // Frappe Grid.get_data(): frm.doc wins over df.data. Keep row identity.
        if (grid.frm) grid.frm.doc[grid.df.fieldname] = values;
        else grid.df.data = values;
        control.df.data = values;
        grid.refresh();
    }
    function cleanup(form) {
        const tables = {
            custom_flexi_purchase_items: r => text(r.product_description) || numeric(r.unit_price) !== 0,
            custom_flexi_application_contacts: r => [r.full_name,r.mobile_number,r.home_phone,r.employer_contact].some(text),
            custom_flexi_income_entries: r => numeric(r.amount) !== 0 || text(r.description),
            custom_flexi_living_expenses: r => numeric(r.expense_amount) !== 0 || text(r.description),
            custom_flexi_finance_obligations: r => numeric(r.amount) !== 0 || text(r.provider) || text(r.description),
            documents: r => text(r.file) || text(r.description)
        };
        for (const [name, meaningful] of Object.entries(tables)) {
            if (!form.fields_dict[name]) continue;
            const metadata = new Set(["doctype","name","idx","parent","parentfield","parenttype","owner","creation","modified","modified_by","docstatus","__islocal","__unsaved","monthly_amount","total_amount","income_type","category","obligation_type","frequency","frequency_per_month","quantity","document_type","contact_type","status"]);
            const all = rows(form, name), kept = all.filter(r => meaningful(r) || Object.entries(r).some(([key,value]) => !metadata.has(key) && (typeof value === "number" ? value !== 0 : Boolean(text(value)))));
            if (name === "custom_flexi_purchase_items") kept.forEach(r => {
                if (!text(r.product_description) || !Number.isFinite(numeric(r.quantity)) || numeric(r.quantity) <= 0 ||
                    !Number.isFinite(numeric(r.unit_price)) || numeric(r.unit_price) < 0) fail("Complete each purchase row; zero-priced goods are allowed.");
            });
            if (name === "documents") kept.forEach(r => {
                if (!text(r.file) || !text(r.document_type)) fail("Complete the document type and attachment on each entered document row.");
            });
            if (kept.length !== all.length) replaceRows(form, name, kept);
        }
    }
    async function prepare(form) {
        document.activeElement?.blur?.();
        await new Promise(resolve => window.setTimeout(resolve, 0));
        if (Object.values(form.fields_dict).some(f => f.uploading || f.file_upload?.uploading))
            fail("Wait for all attachments to finish uploading before submitting.");
        cleanup(form);
        if (typeof normalizeFijiPhoneFields === "function") normalizeFijiPhoneFields();
        if (typeof syncStandardAddressFields === "function") await syncStandardAddressFields();
        if (typeof calculateIncomeAndExpenses === "function") calculateIncomeAndExpenses();
        if (typeof calculateFinanceSummary === "function") calculateFinanceSummary();
        if (typeof flexiValidateFinalFields !== "function") fail("Application validation did not initialise. Keep this page open and contact staff; do not refresh your entered application.");
        flexiValidateFinalFields();
    }
    async function submit(form) {
        if (!["idle", "rejected"].includes(phase)) {
            if (phase === "uncertain") notice("The previous save result is uncertain. Keep your data here and ask staff to check whether it saved before retrying.");
            return false;
        }
        status("checking");
        try {
            await prepare(form);
            const value = key => text(form.get_value(key));
            const identity = [value("custom_tin_number"),value("applicant_email_address")];
            const checkedRevision = revision;
            const response = await frappe.call({method:"comfort_homes.api.check_existing_customer", args:{tin_number:identity[0],email:identity[1]}});
            if (!response || response.exc || !response.message || typeof response.message.exists === "undefined") fail("Could not verify duplicate details. Your application has not been sent; please retry.");
            if (checkedRevision !== revision || identity[0] !== value("custom_tin_number") || identity[1] !== value("applicant_email_address"))
                fail("Your application changed during validation. Review it and submit again.");
            if (response.message.exists) fail("A matching customer or co-applicant already exists. Please contact staff; no new application was sent.");
            flexiValidateFinalFields();
            status("saving");
            // Native save returns false both on early validation failure and after starting XHR.
            form.__flexiNativeSave.call(form);
            if (phase === "saving" && !window.saving) {
                status("rejected");
                notice("No save request started. Correct the validation errors shown above and retry; your entries remain here.");
            } else if (phase === "saving") {
                timer = window.setTimeout(() => {
                    if (phase === "saving") { status("uncertain"); notice("The save response is taking too long. Do not submit again. Keep this page open while staff check whether the application saved."); }
                }, 60000);
            }
        } catch (error) {
            if (phase !== "saving") { status("rejected"); notice(error.message || "Validation failed. Your entries are retained; please retry."); }
            else { status("uncertain"); notice("A save error occurred. Staff must check whether it saved before retrying. Keep this page open."); }
        }
        return false;
    }
    function isAccept(settings) {
        if ((settings?.url || "").includes(accept)) return true;
        const data = settings?.data;
        return (typeof data === "string" ? new URLSearchParams(data).get("cmd") : data?.cmd) === accept;
    }
    function install() {
        const form = window.frappe?.web_form;
        if (!location.pathname.startsWith("/loan-application") || !form?.save || form.__flexiNativeSave) return;
        currentForm = form;
        form.__flexiNativeSave = form.save;
        form.save = () => { void submit(form); return false; };
        const nativeSuccess = form.handle_success;
        form.handle_success = function(data) {
            window.clearTimeout(timer); status("saved");
            try { return nativeSuccess.call(this, data); }
            catch (_error) { notice("The application saved, but its confirmation could not display. Ask staff to open the saved application; do not submit again."); }
        };
        // Scoped observation only: never replace frappe.call or native request callbacks.
        $(document).on("ajaxComplete.flexiSubmit", (_event, xhr, settings) => {
            if (!isAccept(settings) || !["saving", "uncertain"].includes(phase)) return;
            window.clearTimeout(timer);
            if (xhr.responseJSON?.message?.name && !xhr.responseJSON?.exc) {
                status("saved");
                notice("The server returned a saved application. If confirmation is missing, ask staff to open it; do not submit again.");
            } else if (xhr.status >= 400 && xhr.status < 500 && xhr.status !== 408) {
                status("rejected"); notice("The server rejected this application. Correct the displayed error and retry; your entries remain here.");
            } else {
                status("uncertain"); notice("The save result is uncertain. Keep your entries and ask staff to check saved applications before any retry.");
            }
        });
    }
    // Capture on the form, before the native jQuery submit handler (also covers Enter).
    document.addEventListener("submit", event => {
        if (!event.target?.matches?.(".web-form") || !location.pathname.startsWith("/loan-application")) return;
        event.preventDefault(); event.stopImmediatePropagation();
        install();
        if (currentForm) void submit(currentForm);
        else notice("The form is not ready. Keep your data here and contact staff.");
    }, true);
    document.addEventListener("input", () => { revision += 1; }, true);
    document.addEventListener("change", () => { revision += 1; }, true);
    function ready() {
        if (!location.pathname.startsWith("/loan-application")) return;
        if (!window.frappe?.web_form?.events) { window.setTimeout(ready, 150); return; }
        frappe.web_form.events.on("after_load", install);
        install();
    }
    // Expose pure helpers for executable regression tests; no persistence or API bypass.
    window.flexiSubmit = {cleanup, replaceRows, submit, phase:() => phase};
    ready();
})();
