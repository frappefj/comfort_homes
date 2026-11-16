# Flexi HP on Frappe Lending v16: build specification (draft for review)

Status: DRAFT. Code on branch `rebuild/gross-hp`. Nothing here has been run on a
site yet. Section 6 lists what the prototype must confirm before this is trusted.

## 1. Model (decided)

Gross Hire Purchase. The whole contract is booked at disbursement. No IFRS 9 / EIR.
Fees: only the documentation fee ($25 VAT-inclusive) carries VAT, at 12.5%, booked
at disbursement. Admin fee (2.5% of goods less deposit), deposit and interest are VAT-exempt.

Worked example (goods 849, deposit 170, 22% flat, 12 months):

| Item | Amount |
|---|---|
| Value after deposit | 679.00 |
| Admin fee 2.5% | 16.98 |
| Documentation fee (22.22 net + 2.78 VAT) | 25.00 |
| Net financed | 720.98 |
| Term charges 22% x 1 yr | 158.61 |
| **Loan amount (balance after deposit)** | **879.59** |
| Gross contract value (incl. deposit) | 1,049.59 |
| Instalments | 73.30 x 11 + 73.29 / 33.83 x 25 + 33.84 / 16.92 x 51 + 16.67 |

## 2. How each step is done (native first)

| Step | Method | Custom? |
|---|---|---|
| Application, DSR, fees, VAT split | `loan_application_workflow.calculate_financials` (existing, extended) | extended |
| Deposit | Payment Entry (Receive) linked on the application; gate in `hp_lifecycle.validate_deposit_payment` | gate only |
| Loan | Created by Lending's own `create_loan`; `loan_mapping` sets amount = total repayment, rate 0%, frequency, term | mapping |
| Schedule | Native. Product schedule type "Monthly as per repayment start date" (supports weekly/fortnightly). Equal instalments, last absorbs rounding | none |
| Disbursement | Native: Dr HP Receivable / Cr Clearing 879.59 | none |
| Gross booking | ONE added journal entry: Dr Deposits 170.00, Dr Clearing 30.59 / Cr Deferred Interest 158.61, Admin 16.98, Doc fee 22.22, VAT 2.78 | `hp_lifecycle` |
| LPO / CHD invoice | Existing PO workflow; non-stock item HP-GOODS to Clearing | existing |
| Repayments | Native Loan Repayment | none |
| Interest earned | Monthly job, straight-line, current loans only, catch-up on cure | `hp_interest` |
| Late charges | Daily job: rate/period x overdue instalments past 7-day grace, posted as Loan Balance Adjustment to Deferred Penalty | `hp_penalties` |
| Penalty income | Recognised when loan closes | `hp_penalties` |
| Early settlement | Rule of 78 rebate (`hp_math.rule_of_78_unearned`); posting not built yet | pending |

Ledger after disbursement: Receivable 879.59, Clearing Cr 849.00 (owed to CHD),
Deferred Interest Cr 158.61, Admin 16.98, Doc fee 22.22, VAT 2.78, Deposits 0.

## 3. Settings (fields, not code)

On Loan Product: HP Penalty Rate (% per month, default 2.5), Stop Penalties at DPD
(default 90), native Grace Period Days (7), native Penalty Interest Rate **0**.
Per-period rate = monthly x 12 / periods per year (monthly 2.5%, fortnightly 1.1538%, weekly 0.5769%).
Company: Days Past Due Threshold 90 (NPA). Run `hp_settings.check_setup` after importing the Chart of Accounts.

## 4. Removed or disabled

Removed: `penalty.py` (used an invalid adjustment type), `interest_override.py`
(flat daily rate, not needed at 0% loan interest), Sales Invoice deposit gate.
Disabled in fixtures: server scripts "Calculations" and "Flexi Flat Repayment Schedule Sync".
Still to remove after confirmation: `api.create_deposit_sales_invoice` and its client script.

## 5. Permissions: proposed minimal matrix (DRAFT)

Principle: least privilege, no delete anywhere in accounting, maker and checker are different roles.

| Role | Can do |
|---|---|
| Loan Origination (LOS) | Create/edit Loan Application and Co-Applicant until submission. Read customers. No approval. |
| Loan Assessor | Read/write assessment sections; recommend. |
| Finance Manager | Approve applications under $3,000; approve LPO. Not disbursement. |
| Director | Approve $3,000 and over; sign. |
| Disbursement Officer | Create and submit Loan and Loan Disbursement. Cannot approve or raise supplier invoices. |
| Accounts Officer | Deposit and repayment Payment Entries, Loan Repayment, bank reconciliation, supplier payments. Cannot disburse. |
| Accounts Manager | Journal Entry, period close, write-offs, waivers. |
| Auditor | Read, export, print everywhere. No write. |
| System Manager | Administration only. |

Remove: Guest and All write/delete (keep Guest create on the public web form only); Employee on lending forms.
Cancel only, never delete, on Loan Disbursement, Loan Write Off and Payment Entry.

## 6. Prototype checklist (must pass before building further)

1. A 0% loan at 879.59 gives 73.30 x 11 + 73.29 (monthly), 33.83 x 25 + 33.84 (fortnightly), 16.92 x 51 + 16.67 (weekly).
2. Loan product schedule type options accept weekly and fortnightly.
3. Deposit Payment Entry posts to Customer HP Deposits, and the reclass journal clears it against the same customer. Check the advance-payment company setting and the account type 2010 needs.
4. Does a Loan Balance Adjustment (Debit) added to the loan get collected by Loan Repayment, increase the amount due, and close correctly? If not, use native daily penalty accrual with a per-loan rate instead.
5. Lending accepts the Deferred Penalty liability as the adjustment account.
6. `Loan.days_past_due` and NPA flag at 90.
7. Loan Balance report equals the HP Receivable ledger.
8. End-to-end ledger test against the example: Receivable 806.29 after one instalment; Deferred Interest 145.39 after one release.

## 7. Open items

Early-settlement posting (Rule of 78), write-off and repossession entries, CHD purchase invoice
on delivery (LPO must be submitted first), permissions fixtures, Chart of Accounts import file,
print formats reading the contract rate from the Loan Product.
