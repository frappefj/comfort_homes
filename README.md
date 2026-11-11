# Comfort Homes

Frappe app for Comfort Home Furnishing PTE Limited's Flexi hire-purchase lending platform.
(Initial Repo from Anuradha145)
## Architecture: Path B (Native-Aligned)

This app works **with** the native Frappe Lending module, adding only the customisations that Flexi's HP agreement requires:

| Feature | Approach |
|---------|----------|
| Interest | Native daily accrual — no custom code |
| Fees | Native Loan Charges — auto Sales Invoice at disbursement |
| VAT on Doc Fee | Item Tax Template (12.5%) — automatic |
| Deposit | Sales Invoice (Loan Deposit → 2010 Advance Payment) |
| Penalty | **Custom** — 2.2% per overdue instalment (`penalty.py`) |
| Customer Statement | **Custom** — gross view report (`hp_customer_statement`) |

## Installation

```bash
bench get-app https://github.com/frappefj/comfort_homes
bench --site your-site.frappe.cloud install-app comfort_homes
bench --site your-site.frappe.cloud migrate
```

## Required Apps

- frappe
- erpnext
- lending

## Configuration

See `CHANGELOG.md` for the complete list of Company, Product, and Account settings required for a new site deployment.

## License

MIT
