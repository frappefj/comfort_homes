"""Confirmed document process; no legal-validity or handwriting verification claims."""
from datetime import date
import hashlib
import json

LEGAL_DOCUMENT_CODES = ('HP-AGREEMENT', 'SCHEDULE-1', 'FORM-1', 'FORM-2', 'FINANCIAL-OBLIGATIONS', 'CONSENT-FORM')
DIGITAL_CONTRACT_SIGNING_ENABLED = False  # Await lawyer's advice and explicit change approval.


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def consent_complete(record):
    return (str(record.get('custom_cira_authority_acknowledged') or '0') == '1'
            and bool(record.get('custom_cira_authority_signature'))
            and bool(str(record.get('custom_cira_authority_name') or '').strip())
            and bool(record.get('custom_hp_cira_signed_on')))


def scan_issues(row, expected_fingerprint, content_hash, today, manager_required):
    """Check declared dates/identity of evidence, not whether a human signed it."""
    errors = []
    if row.get('custom_hp_contract_fingerprint') != expected_fingerprint:
        errors.append('The contract inputs changed after this scan was recorded')
    if row.get('custom_hp_signed_scan_hash') != content_hash:
        errors.append('The recorded PDF content no longer matches')
    if not row.get('custom_hp_execution_attested'):
        errors.append('Confirm every required individual signature is present and customer-first')
    def get_date(name):
        raw = row.get(name)
        try:
            return date.fromisoformat(str(raw)[:10]) if raw else None
        except ValueError:
            return None
    customer = get_date('custom_hp_customer_signed_date')
    manager = get_date('custom_hp_manager_signed_date')
    if not customer or customer > today:
        errors.append('A valid, non-future customer/co-hirer signing date is required')
    if manager_required and (not manager or manager > today or (customer and manager < customer)):
        errors.append('A valid manager signing date on or after customer signing is required')
    if manager_required and not str(row.get('director_name') or '').strip():
        errors.append('Record the manager signatory name shown on the scan')
    return errors


def pack_order_issues(rows, manager_required_codes):
    customers = [str(row.get('custom_hp_customer_signed_date'))[:10] for row in rows]
    managers = [str(row.get('custom_hp_manager_signed_date'))[:10] for row in rows
                if row.get('document_code') in manager_required_codes]
    if customers and managers and min(managers) < max(customers):
        return ['All required customer/co-hirer signings must precede manager signing across the pack']
    return []
