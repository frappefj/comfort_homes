"""Digital CIRA intake, assessment PDFs and verified wet-signature evidence.

Uses native Workflow/File/Version and the existing agreement child table. It
never approves a loan, captures a digital contract signature, or posts a ledger.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from io import BytesIO
import hashlib
import base64
import binascii

import frappe
from frappe.utils import cint, getdate, now_datetime, today

from comfort_homes.hp_lending.document_policy import (
    LEGAL_DOCUMENT_CODES, consent_complete, fingerprint, scan_issues, pack_order_issues,
)
from comfort_homes.hp_lending.hp_settings import COMPANY

_ACTION = ContextVar('flexi_document_action', default=None)
ASSESSMENT_FIELDS = ('custom_hp_assessment_pdf', 'custom_hp_assessment_fingerprint', 'custom_hp_assessment_source', 'custom_hp_assessment_content_hash')
WET_FIELDS = ('final_signed_file', 'status', 'completed_on', 'director_name',
              'custom_hp_customer_signed_date', 'custom_hp_manager_signed_date',
              'custom_hp_signed_scan_hash', 'custom_hp_contract_fingerprint',
              'custom_hp_execution_attested', 'custom_hp_verified_by', 'custom_hp_verified_on')
IDENTITY = ('applicant_name', 'custom_first_name', 'custom_middle_name', 'custom_surname',
            'custom_tin_number', 'address_line_1', 'address_line_2', 'custom_new_city',
            'custom_suburb', 'custom_province', 'country', 'zip_code', 'city', 'state',
            'applicant_email_address', 'applicant_phone_number', 'custom_flexi_date_of_birth',
            'custom_flexi_marital_status', 'custom_flexi_number_of_dependents',
            'custom_flexi_housing_situation', 'custom_flexi_employer_name',
            'custom_flexi_occupation', 'custom_flexi_employment_start_date',
            'custom_flexi_employer_contact', 'custom_flexi_employer_address')
TERMS = ('company', 'loan_product', 'custom_branchlocation', 'custom_flexi_cash_price',
         'custom_flexi_deposit_amount', 'custom_flexi_admin_fee', 'custom_flexi_documentation_fee',
         'custom_flexi_doc_fee_net', 'custom_flexi_doc_fee_vat', 'custom_flexi_net_amount_financed',
         'custom_flexi_term_charges', 'custom_flexi_total_receivable', 'custom_flexi_gross_contract_value',
         'custom_flexi_term_months', 'custom_flexi_interest_rate', 'custom_flexi_payment_period',
         'custom_flexi_payment_method', 'custom_flexi_first_instalment_date',
         'custom_flexi_surcharge_rate', 'custom_flexi_stamp_duty_fee', 'custom_flexi_other_fee',
         'custom_flexi_form1_dated_at', 'custom_flexi_form1_location', 'custom_mode_of_payment')
PROFILE_TABLES = {'custom_flexi_income_entries': ('income_type', 'description', 'amount', 'frequency', 'status'),
                  'custom_flexi_living_expenses': ('category', 'description', 'expense_amount', 'frequency_per_month', 'status'),
                  'custom_flexi_finance_obligations': ('obligation_type', 'provider', 'amount', 'frequency', 'status')}


def _fields(doc, names):
    return {name: doc.get(name) for name in names}


def _table(doc, name, fields):
    # Include all child input columns; ignore Frappe row bookkeeping only.
    excluded = {'name', 'owner', 'creation', 'modified', 'modified_by', 'docstatus',
                'idx', 'parent', 'parentfield', 'parenttype', 'doctype'}
    return [{k: v for k, v in row.as_dict().items() if k not in excluded}
            for row in doc.get(name) or []]


def _profiles(application):
    from comfort_homes.print_helpers import get_co_applicants
    return get_co_applicants(application)


def _profile_data(doc):
    return {'identity': _fields(doc, IDENTITY), 'tables': {name: _table(doc, name, fields)
            for name, fields in PROFILE_TABLES.items()}, 'consent': doc.get('custom_hp_cira_fingerprint'),
            'employment_history': _table(doc, 'custom_flexi_employment_history', ()),
            'contacts': _table(doc, 'custom_flexi_application_contacts', ())}


def _contract_hash(doc):
    return fingerprint({'identity': _fields(doc, IDENTITY), 'terms': _fields(doc, TERMS),
        'items': _table(doc, 'custom_flexi_purchase_items', ('product_description', 'quantity', 'unit_price', 'total_amount')),
        'co_applicants': [{'name': p.name, **_fields(p, IDENTITY)} for p in _profiles(doc)]})


def _row_hash(doc, template):
    print_format = frappe.get_doc('Print Format', template.print_format)
    return fingerprint({'contract': _contract_hash(doc), 'code': template.document_code,
                        'template': template.name, 'version': template.get('version'),
                        'manager_required': template.requires_director_signature,
                        'print_format': template.print_format, 'html': print_format.get('html'),
                        'format_data': print_format.get('format_data')})


def _assessment_hash(doc):
    return fingerprint({'contract': _contract_hash(doc), 'primary': _profile_data(doc),
                        'selected_dsr': doc.get('custom_flexi_selected_dsr'),
                        'co_applicants': [{'name': p.name, **_profile_data(p)} for p in _profiles(doc)]})


def _set(doc, field, value):
    if doc.meta.has_field(field):
        doc.set(field, value)


def _capture_cira(doc):
    """Timestamp a saved consent capture, never a print-time date fallback."""
    previous = doc.get_doc_before_save()
    complete_inputs = cint(doc.get('custom_cira_authority_acknowledged')) and doc.get('custom_cira_authority_signature')
    if not complete_inputs:
        _set(doc, 'custom_hp_cira_signed_on', None)
        _set(doc, 'custom_hp_cira_fingerprint', None)
        _set(doc, 'custom_cira_authority_pdf', None)
        return
    signature = str(doc.get('custom_cira_authority_signature') or '')
    try:
        prefix, encoded = signature.split(',', 1)
        image = base64.b64decode(encoded, validate=True)
        if prefix != 'data:image/png;base64' or not image.startswith(b'\x89PNG\r\n\x1a\n') or len(image) > 1024 * 1024:
            raise ValueError('Invalid signature image')
    except (ValueError, binascii.Error):
        frappe.throw('CIRA signature must be a captured PNG signature of at most 1 MB')
    name = str(doc.get('custom_cira_authority_name') or doc.get('applicant_name') or '').strip()
    _set(doc, 'custom_cira_authority_name', name)
    field = frappe.get_meta(doc.doctype).get_field('custom_cira_authority_text')
    digest = fingerprint({'name': name, 'signature': doc.get('custom_cira_authority_signature'),
                          'acknowledged': 1, 'wording': field.options if field else ''})
    if previous and previous.get('custom_cira_authority_signature') == doc.get('custom_cira_authority_signature') and cint(previous.get('custom_cira_authority_acknowledged')) and not previous.get('custom_hp_cira_signed_on'):
        frappe.throw('Historical CIRA signing time is unknown: obtain a fresh consent capture rather than inventing a date')
    if previous and previous.get('custom_hp_cira_fingerprint') == digest:
        _set(doc, 'custom_hp_cira_signed_on', previous.get('custom_hp_cira_signed_on'))
    else:
        if previous and previous.get('custom_hp_cira_fingerprint') and previous.get('custom_cira_authority_signature') == doc.get('custom_cira_authority_signature'):
            frappe.throw('CIRA name or wording changed: obtain fresh client consent; do not reuse the old signature')
        _set(doc, 'custom_hp_cira_signed_on', now_datetime())
        _set(doc, 'custom_cira_authority_pdf', None)
    _set(doc, 'custom_hp_cira_fingerprint', digest)


def _require_cira(doc):
    if not consent_complete(doc):
        frappe.throw('Record the individual client CIRA acknowledgement and digital signature before KYC initiation')
    if ' '.join(str(doc.get('custom_cira_authority_name') or '').lower().split()) != ' '.join(str(doc.get('applicant_name') or '').lower().split()):
        frappe.throw('CIRA signer name must match the individual profile; obtain corrected client consent')


def _require_profiles(doc):
    for profile in _profiles(doc):
        if profile.company != doc.company: frappe.throw('Linked co-applicant must belong to the same company')
        _require_cira(profile)
        if profile.workflow_state != 'Approved' or profile.get('custom_hp_profile_review_fingerprint') != fingerprint(_profile_data(profile)):
            frappe.throw('Complete or renew the linked co-applicant profile review before facility approval')


def _require_assessment(doc):
    if not doc.get('custom_hp_assessment_pdf') or doc.get('custom_hp_assessment_fingerprint') != _assessment_hash(doc):
        frappe.throw('Generate and attach the current Application Print WIP after KYC; reprint after any source-data change')
    content = _load_pdf(doc, doc.custom_hp_assessment_pdf)
    if doc.get('custom_hp_assessment_content_hash') != hashlib.sha256(content).hexdigest():
        frappe.throw('Assessment PDF content changed: generate or explicitly record the current PDF again')


def _restore_protected(doc):
    """Read-only UI fields are not security: only our scoped server action may change proof."""
    previous = doc.get_doc_before_save()
    action = _ACTION.get()
    if action != (doc.name, 'assessment', None):
        for field in ASSESSMENT_FIELDS:
            _set(doc, field, previous.get(field) if previous else None)
    if action != (doc.name, 'cira_pdf', None):
        _set(doc, 'custom_cira_authority_pdf', previous.get('custom_cira_authority_pdf') if previous else None)
    old_rows = {row.name: row for row in previous.get('custom_customer_documents') or []} if previous else {}
    current_rows = doc.get('custom_customer_documents') or []
    current_names = {row.name for row in current_rows}
    if any(old.get('final_signed_file') and name not in current_names for name, old in old_rows.items()):
        frappe.throw('Keep recorded signature rows and earlier files; use explicit evidence supersession')
    for row in current_rows:
        if action in ((doc.name, 'scan', row.name), (doc.name, 'verify', None), (doc.name, 'pack', None)):
            continue
        old = old_rows.get(row.name)
        if old and old.get('final_signed_file') and any(row.get(k) != old.get(k) for k in ('document_template', 'document_code')):
            frappe.throw('Do not reassign recorded signature evidence to a different legal document')
        for field in WET_FIELDS:
            row.set(field, old.get(field) if old else ('Pending' if field == 'status' else None))


def validate_application(doc, method=None):
    if doc.company != COMPANY:
        return
    _restore_protected(doc)
    _capture_cira(doc)
    previous = doc.get_doc_before_save()
    old = previous.workflow_state if previous else 'Draft'
    state = doc.get('workflow_state') or 'Draft'
    if getattr(frappe.flags, 'in_web_form', False):
        _require_cira(doc)
    if old == 'Draft' and state == 'Initiated':
        _require_cira(doc)
        for profile in _profiles(doc): _require_cira(profile)
    if old != state and state in ('Pending Approval', 'Approved', 'Ready for Disbursement', 'Loan Activated'):
        if doc.get('custom_document_collection_complete') != 'Yes':
            frappe.throw('Complete KYC/document collection before assessment handoff')
        _require_cira(doc)
        _require_profiles(doc)
        _require_assessment(doc)
    if old != state and state in ('Ready for Disbursement', 'Loan Activated'):
        validate_signed_pack(doc)


def validate_profile(doc, method=None):
    if doc.company != COMPANY:
        return
    _restore_protected(doc)
    _capture_cira(doc)
    previous = doc.get_doc_before_save()
    old = previous.workflow_state if previous else 'Draft'
    state = doc.get('workflow_state') or 'Draft'
    if old != state and state in ('Initiated', 'Pending Approval', 'Approved'):
        _require_cira(doc)
    if old != state and state in ('Pending Approval', 'Approved') and doc.get('custom_document_collection_complete') != 'Yes':
        frappe.throw('Complete the co-applicant KYC/document review before approval')
    if old != state and state == 'Approved':
        frappe.only_for(('Finance Manager', 'Director'))
        _set(doc, 'custom_hp_profile_review_fingerprint', fingerprint(_profile_data(doc)))
    elif previous:
        _set(doc, 'custom_hp_profile_review_fingerprint', previous.get('custom_hp_profile_review_fingerprint') if state == 'Approved' else None)
    else:
        _set(doc, 'custom_hp_profile_review_fingerprint', None)


def _application(name, manager=False):
    if manager: frappe.only_for(('Finance Manager', 'Director'))
    frappe.db.sql('SELECT name FROM `tabLoan Application` WHERE name = %s FOR UPDATE', (name,))
    doc = frappe.get_doc('Loan Application', name)
    doc.check_permission('read' if manager else 'write')
    if doc.company != COMPANY or doc.docstatus != 0:
        frappe.throw('Document recording is restricted to draft Flexi applications')
    return doc


@contextmanager
def _action(doc, kind, row=None):
    token = _ACTION.set((doc.name, kind, row))
    try: yield
    finally: _ACTION.reset(token)


def _save(doc, kind, row=None, manager=False):
    with _action(doc, kind, row):
        # Manager verification is a narrow role-authorised action, not general
        # permission to edit the officer's form or any financial document.
        doc.save(ignore_permissions=manager)
    if kind == 'assessment':
        persisted = frappe.db.get_value(doc.doctype, doc.name, list(ASSESSMENT_FIELDS), as_dict=True)
        if not persisted or any(persisted.get(field) != doc.get(field) for field in ASSESSMENT_FIELDS):
            frappe.throw('Assessment PDF was created but evidence was not saved; stop and report this error, do not repeatedly generate copies')


def _load_pdf(doc, url):
    if not str(url or '').startswith('/private/files/'):
        frappe.throw('Use a private uploaded PDF attached to this record')
    name = frappe.db.get_value('File', {'file_url': url, 'attached_to_doctype': doc.doctype,
                                      'attached_to_name': doc.name, 'is_private': 1}, 'name')
    if not name: frappe.throw('PDF is not privately attached to this record')
    file = frappe.get_doc('File', name)
    file.check_permission('read')
    content = file.get_content()
    if not isinstance(content, bytes) or not content.startswith(b'%PDF-') or len(content) > 20 * 1024 * 1024:
        frappe.throw('Upload a readable PDF of at most 20 MB; no HTML or remote-file fallback is accepted')
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader
    try:
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 200:
            raise ValueError('Encrypted, empty or oversized page count')
    except Exception:
        frappe.throw('Upload a readable, unencrypted PDF; the original file is not modified')
    return content


def _attach(doc, name, content):
    # Reuse the existing immutable content-versioned attachment implementation.
    from comfort_homes.signed_pack import _attach as attach
    return attach(doc, name, content)


@frappe.whitelist(methods=['POST'])
def generate_assessment_pdf(loan_application):
    frappe.only_for(('Loan Officer', 'Loan Processor', 'Finance Manager', 'Director', 'Loan Manager'))
    doc = _application(loan_application)
    if doc.get('custom_document_collection_complete') != 'Yes': frappe.throw('Complete KYC before generating the assessment PDF')
    _require_cira(doc)
    # Native printview marks its document in_print; never render on the saved instance.
    render_doc = frappe.get_doc('Loan Application', doc.name)
    content = frappe.get_print('Loan Application', doc.name, print_format='Application Print WIP', doc=render_doc, as_pdf=True, no_letterhead=1)
    if not isinstance(content, bytes) or not content.startswith(b'%PDF-'):
        frappe.throw('Assessment PDF generation failed; do not attach HTML as a PDF')
    file = _attach(doc, 'Application Assessment - ' + doc.name + '.pdf', content)
    _set(doc, 'custom_hp_assessment_pdf', file.file_url)
    _set(doc, 'custom_hp_assessment_fingerprint', _assessment_hash(doc))
    _set(doc, 'custom_hp_assessment_content_hash', hashlib.sha256(content).hexdigest())
    _set(doc, 'custom_hp_assessment_source', 'Server generated by ' + frappe.session.user + ' at ' + str(now_datetime()))
    _save(doc, 'assessment')
    return {'file_url': file.file_url}


@frappe.whitelist(methods=['POST'])
def record_assessment_pdf(loan_application, file_url, confirm_current=0):
    frappe.only_for(('Loan Officer', 'Loan Processor', 'Finance Manager', 'Director', 'Loan Manager'))
    doc = _application(loan_application)
    if doc.get('custom_document_collection_complete') != 'Yes' or not cint(confirm_current):
        frappe.throw('Complete KYC and attest that this is the current Application Print WIP')
    _require_cira(doc)
    content = _load_pdf(doc, file_url)
    _set(doc, 'custom_hp_assessment_pdf', file_url)
    _set(doc, 'custom_hp_assessment_fingerprint', _assessment_hash(doc))
    _set(doc, 'custom_hp_assessment_content_hash', hashlib.sha256(content).hexdigest())
    _set(doc, 'custom_hp_assessment_source', 'Manual PDF upload attested by ' + frappe.session.user + ' at ' + str(now_datetime()))
    _save(doc, 'assessment')
    return {'file_url': file_url, 'manual_attestation': True}


def _legal_rows(doc):
    result = {}
    for row in doc.get('custom_customer_documents') or []:
        template = frappe.get_doc('Loan Document Template', row.document_template)
        code = template.document_code
        if code not in LEGAL_DOCUMENT_CODES: continue
        if code in result: frappe.throw('Duplicate legal document rows require review')
        if row.document_code != code: frappe.throw('Agreement row and template code do not match')
        result[code] = (row, template)
    if set(result) != set(LEGAL_DOCUMENT_CODES):
        frappe.throw('All six required legal document rows must be configured')
    return result


@frappe.whitelist(methods=['POST'])
def record_signed_scan(loan_application, agreement_row, file_url, customer_signed_date,
                       manager_signed_date=None, manager_name=None, confirm_execution=0,
                       supersede=0, reason=None):
    doc = _application(loan_application, manager=bool(cint(supersede)))
    if doc.workflow_state != 'Contract Signing': frappe.throw('Record signed scans during Contract Signing')
    pairs = _legal_rows(doc)
    pair = next((pair for pair in pairs.values() if pair[0].name == agreement_row), None)
    if not pair: frappe.throw('Select a required legal document belonging to this application')
    row, template = pair
    if row.status == 'Completed':
        frappe.only_for(('Finance Manager', 'Director'))
        if not cint(supersede) or not str(reason or '').strip():
            frappe.throw('A manager must explicitly supersede verified evidence with a reason; earlier files are retained')
    content = _load_pdf(doc, file_url)
    updates = {'final_signed_file': file_url, 'custom_hp_customer_signed_date': customer_signed_date,
               'custom_hp_manager_signed_date': manager_signed_date if template.requires_director_signature else None,
               'director_name': manager_name if template.requires_director_signature else None,
               'custom_hp_signed_scan_hash': hashlib.sha256(content).hexdigest(),
               'custom_hp_contract_fingerprint': _row_hash(doc, template), 'custom_hp_execution_attested': cint(confirm_execution),
               'custom_hp_verified_by': None, 'custom_hp_verified_on': None, 'completed_on': None, 'status': 'Ready'}
    trial = {**dict(row.as_dict()), **updates}
    errors = scan_issues(trial, _row_hash(doc, template), updates['custom_hp_signed_scan_hash'], getdate(today()), bool(template.requires_director_signature))
    if errors: frappe.throw('; '.join(errors))
    for field, value in updates.items(): row.set(field, value)
    if cint(supersede): doc.add_comment('Comment', 'Signed scan supersession requested: ' + str(reason))
    _save(doc, 'scan', row.name, manager=bool(cint(supersede)))
    return {'status': 'Ready', 'file_url': file_url, 'requires_manager_verification': True}


@frappe.whitelist(methods=['POST'])
def record_signed_pack(loan_application, file_url, customer_signed_date,
                       manager_signed_date, manager_name, confirm_all_forms=0,
                       confirm_execution=0, supersede=0, reason=None):
    """One private six-form paper pack, one atomic save, separate manager review."""
    doc = _application(loan_application, manager=bool(cint(supersede)))
    if doc.workflow_state != 'Contract Signing':
        frappe.throw('Record the combined signed pack during Contract Signing')
    if not cint(confirm_all_forms):
        frappe.throw('Confirm this PDF contains all six required legal forms for this application')
    pairs = _legal_rows(doc)
    if any(row.status == 'Completed' for row, _ in pairs.values()):
        frappe.only_for(('Finance Manager', 'Director'))
        if not cint(supersede) or not str(reason or '').strip():
            frappe.throw('A manager must explicitly supersede verified evidence with a reason; earlier files are retained')
    content = _load_pdf(doc, file_url)
    digest = hashlib.sha256(content).hexdigest()
    staged, manager_codes = [], set()
    for code, (row, template) in pairs.items():
        required = bool(template.requires_director_signature)
        values = dict(final_signed_file=file_url, custom_hp_customer_signed_date=customer_signed_date,
            custom_hp_manager_signed_date=manager_signed_date if required else None,
            director_name=manager_name if required else None, custom_hp_signed_scan_hash=digest,
            custom_hp_contract_fingerprint=_row_hash(doc, template),
            custom_hp_execution_attested=cint(confirm_execution), custom_hp_verified_by=None,
            custom_hp_verified_on=None, completed_on=None, status='Ready')
        trial = {**row.as_dict(), **values}
        errors = scan_issues(trial, values['custom_hp_contract_fingerprint'], digest,
                             getdate(today()), required)
        if errors: frappe.throw(code + ': ' + '; '.join(errors))
        staged.append((row, values, trial))
        if required: manager_codes.add(code)
    errors = pack_order_issues([trial for _, _, trial in staged], manager_codes)
    if errors: frappe.throw('; '.join(errors))
    for row, values, _ in staged:
        for field, value in values.items(): row.set(field, value)
    if cint(supersede):
        doc.add_comment('Comment', 'Combined signed-pack supersession requested: ' + str(reason))
    _save(doc, 'pack', manager=bool(cint(supersede)))
    return dict(recorded=len(staged), file_url=file_url, requires_manager_verification=True)


def _check_scans(doc, require_verified):
    import hashlib
    rows, manager_codes = [], set()
    pairs = _legal_rows(doc)
    missing = [code for code, (row, _) in pairs.items() if not row.get('final_signed_file')]
    if missing:
        frappe.throw('Signed scan not recorded for: ' + ', '.join(missing) + '. Use Record Combined Signed Pack or Record Wet-signed Scan; an attachment alone is not a recorded legal document.')
    for code, (row, template) in pairs.items():
        content = _load_pdf(doc, row.final_signed_file)
        errors = scan_issues(row, _row_hash(doc, template), hashlib.sha256(content).hexdigest(), getdate(today()), bool(template.requires_director_signature))
        if require_verified and (row.status != 'Completed' or not row.get('custom_hp_verified_by') or not row.get('custom_hp_verified_on')):
            errors.append('Manager verification of the uploaded scan is required')
        if errors: frappe.throw(code + ': ' + '; '.join(errors))
        rows.append(row)
        if template.requires_director_signature: manager_codes.add(code)
    errors = pack_order_issues(rows, manager_codes)
    if errors: frappe.throw('; '.join(errors))
    return rows


@frappe.whitelist(methods=['POST'])
def verify_contract_pack(loan_application):
    doc = _application(loan_application, manager=True)
    if doc.workflow_state != 'Contract Signing': frappe.throw('Verify signed scans during Contract Signing')
    _require_cira(doc); _require_profiles(doc); _require_assessment(doc)
    rows = _check_scans(doc, require_verified=False)
    if all(row.status == 'Completed' and row.get('custom_hp_verified_by') for row in rows):
        return {'verified': len(rows), 'existing': True}
    for row in rows:
        row.set('status', 'Completed')
        row.set('custom_hp_verified_by', frappe.session.user)
        row.set('custom_hp_verified_on', now_datetime())
        row.set('completed_on', now_datetime())  # record-completion time, not an ink-signing timestamp
    _save(doc, 'verify', manager=True)
    return {'verified': len(rows), 'existing': False}


def validate_signed_pack(doc):
    """Called again at compliance and disbursement; no paper image authenticity claim."""
    _require_cira(doc); _require_profiles(doc); _require_assessment(doc)
    return _check_scans(doc, require_verified=True)


@frappe.whitelist(methods=['POST'])
def generate_cira_pdf(doctype, name):
    """Retain individual captured consent using native rendering and private File.

    Explicit action: a missing PDF engine must not silently turn HTML into PDF,
    or destroy an otherwise saved consent. No anonymous print/file endpoint.
    """
    if doctype not in ('Loan Application', 'Co-Applicant'):
        frappe.throw('CIRA PDF is restricted to the individual application/profile')
    frappe.db.sql('SELECT name FROM `tab' + doctype + '` WHERE name = %s FOR UPDATE', (name,))
    doc = frappe.get_doc(doctype, name)
    doc.check_permission('write')
    if doc.company != COMPANY or doc.docstatus != 0:
        frappe.throw('Generate the CIRA PDF on a draft Flexi application/profile')
    _require_cira(doc)
    # The CIRA format is individual, not the joint-contract print helpers.
    template = frappe.get_doc('Print Format', 'CIRA Authority Form')
    from frappe.utils.pdf import get_pdf
    content = get_pdf(frappe.render_template(template.html, {'doc': doc}))
    if not isinstance(content, bytes) or not content.startswith(b'%PDF-'):
        frappe.throw('CIRA PDF generation failed; saved consent remains unchanged')
    if doctype == 'Loan Application':
        file = _attach(doc, 'CIRA Authority - ' + doc.name + '.pdf', content)
    else:
        file_name = 'CIRA-' + doc.name + '-' + hashlib.sha256(content).hexdigest()[:16] + '.pdf'
        existing = frappe.db.exists('File', {'attached_to_doctype': doctype,
            'attached_to_name': name, 'file_name': file_name, 'is_private': 1})
        if existing:
            file = frappe.get_doc('File', existing)
            file.check_permission('read')
            if file.get_content() != content: frappe.throw('Existing CIRA evidence has conflicting content')
        else:
            file = frappe.get_doc({'doctype': 'File', 'file_name': file_name, 'content': content,
                'is_private': 1, 'attached_to_doctype': doctype, 'attached_to_name': name}).insert(ignore_permissions=True)
    _set(doc, 'custom_cira_authority_pdf', file.file_url)
    _save(doc, 'cira_pdf')
    return {'file_url': file.file_url}


def validate_agreement(doc, method=None):
    """Direct child REST saves must not bypass the parent evidence actions."""
    previous = doc.get_doc_before_save()
    for field in WET_FIELDS:
        expected = previous.get(field) if previous else ('Pending' if field == 'status' else None)
        value = doc.get(field)
        if field == 'status' and not value: value = 'Pending'
        if field != 'status' and not value and not expected: continue
        if value != expected:
            frappe.throw('Record or verify signature evidence through the application document actions, not a direct child edit')
    if previous and previous.get('final_signed_file') and any(doc.get(k) != previous.get(k) for k in ('parent', 'parenttype', 'parentfield', 'document_template', 'document_code')):
        frappe.throw('Recorded signature evidence cannot be reassigned')


@frappe.whitelist()
def evidence_status(doctype, name):
    """Read-only UI hints; does not render, save, repair or download files."""
    if doctype not in ("Loan Application", "Co-Applicant"):
        frappe.throw("Unsupported document")
    doc=frappe.get_doc(doctype,name)
    doc.check_permission("read")
    if doc.company != COMPANY:
        frappe.throw("Select a Flexi record")
    field=frappe.get_meta(doctype).get_field("custom_cira_authority_text")
    expected=fingerprint({"name":str(doc.get("custom_cira_authority_name") or doc.get("applicant_name") or "").strip(),
        "signature":doc.get("custom_cira_authority_signature"),"acknowledged":1,"wording":field.options if field else ""})
    same_name=" ".join(str(doc.get("custom_cira_authority_name") or "").lower().split()) == " ".join(str(doc.get("applicant_name") or "").lower().split())
    return dict(consent_current=bool(consent_complete(doc) and same_name and expected==doc.get("custom_hp_cira_fingerprint")),
        assessment_present=bool(doc.get("custom_hp_assessment_pdf")),
        assessment_source_current=bool(doctype=="Loan Application" and doc.get("custom_hp_assessment_pdf") and doc.get("custom_hp_assessment_fingerprint")==_assessment_hash(doc)))
