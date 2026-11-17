/* Public Loan Application guard: existing customers are handled by staff. */
(function () {
	if (window.__comfortHomesDuplicateCheckLoaded) return;
	window.__comfortHomesDuplicateCheckLoaded = true;

	let duplicateCheckBound = false;
	let submissionApproved = false;
	let checkingDuplicate = false;
	let duplicateFound = false;
	let fieldChecksBound = false;
	let fieldInputChecksBound = false;
	let documentInputChecksBound = false;
	let lookupTimer;
	let lookupSequence = 0;
	let duplicateMatch = [];

	function duplicateMessage(matchedBy = duplicateMatch) {
		if (window.__comfortHomesExistingCoApplicant) {
			return __("A matching Co-Applicant already exists. Please apply from the backend.");
		}
		const identifier = matchedBy.length === 1
			? matchedBy[0]
			: "TIN Number or Email Address";
		return __(
			"A customer already exists with this {0}. Please apply from the backend.",
			[identifier]
		);
	}

	function updateProgressAccess() {
		const blocked = duplicateFound;
		document.querySelectorAll(".btn-next, .submit-btn").forEach((button) => {
			button.disabled = blocked;
			button.setAttribute("aria-disabled", blocked ? "true" : "false");
			button.classList.toggle("disabled", blocked);
		});
	}

	function valueFromInput(fieldname) {
		const input = frappe.web_form.fields_dict[fieldname]?.wrapper?.querySelector("input");
		return String(input?.value ?? frappe.web_form.get_value(fieldname) ?? "").trim();
	}

	function checkExistingCustomer(showMessage) {
		if (!isLoanApplicationWebForm()) return Promise.resolve(false);
		const sequence = ++lookupSequence;
		return frappe.call({
			method: "comfort_homes.api.check_existing_customer",
			args: {
				tin_number: valueFromInput("custom_tin_number"),
				email: valueFromInput("applicant_email_address"),
			},
		}).then((response) => {
			if (sequence !== lookupSequence) return duplicateFound;
			const match = response.message || {};
			duplicateFound = Boolean(match.exists);
			window.__comfortHomesExistingCoApplicant = Boolean(match.co_applicant_exists);
			duplicateMatch = match.matched_by || [];
			updateProgressAccess();
			if (duplicateFound && showMessage) {
				frappe.msgprint({
					title: __("Existing customer"),
					message: duplicateMessage(),
					indicator: "orange",
				});
			}
			return duplicateFound;
		});
	}

	function isLoanApplicationWebForm() {
		return window.location.pathname.replace(/\/$/, "").startsWith("/loan-application") && window.frappe?.web_form;
	}

	function bindDuplicateCheck() {
		if (!isLoanApplicationWebForm() || duplicateCheckBound) return;
		const button = document.querySelector(".submit-btn");
		if (!button) {
			window.setTimeout(bindDuplicateCheck, 150);
			return;
		}
		duplicateCheckBound = true;
		button.addEventListener("click", (event) => {
			if (submissionApproved) {
				submissionApproved = false;
				return;
			}
			if (checkingDuplicate) {
				event.preventDefault();
				event.stopImmediatePropagation();
				return;
			}

			event.preventDefault();
			event.stopImmediatePropagation();
			checkingDuplicate = true;
			checkExistingCustomer(false).then((exists) => {
					checkingDuplicate = false;
					if (exists) {
						frappe.msgprint({
							title: __("Existing customer"),
							message: duplicateMessage(),
							indicator: "orange",
						});
						return;
					}
					submissionApproved = true;
					button.click();
				}).catch(() => {
					checkingDuplicate = false;
					frappe.msgprint(__("We could not validate existing customer details. Please try again."));
			});
		}, true);
	}

	function bindFieldChecks() {
		if (!isLoanApplicationWebForm() || fieldChecksBound) return;
		fieldChecksBound = true;
		["custom_tin_number", "applicant_email_address"].forEach((fieldname) => {
			frappe.web_form.on(fieldname, () => checkExistingCustomer(true));
		});
	}

	function bindImmediateInputChecks() {
		if (!isLoanApplicationWebForm() || fieldInputChecksBound) return;
		const inputs = ["custom_tin_number", "applicant_email_address"]
			.map((fieldname) => frappe.web_form.fields_dict[fieldname]?.wrapper?.querySelector("input"))
			.filter(Boolean);
		if (inputs.length !== 2) {
			window.setTimeout(bindImmediateInputChecks, 150);
			return;
		}
		fieldInputChecksBound = true;
		inputs.forEach((input) => {
			input.addEventListener("input", () => {
				window.clearTimeout(lookupTimer);
				lookupTimer = window.setTimeout(() => checkExistingCustomer(true), 500);
			});
			input.addEventListener("blur", () => {
				window.clearTimeout(lookupTimer);
				checkExistingCustomer(true);
			});
		});
	}

	function bindDocumentInputChecks() {
		if (documentInputChecksBound) return;
		documentInputChecksBound = true;
		const changed = (event) => {
			const fieldname = event.target?.closest?.("[data-fieldname]")?.dataset?.fieldname;
			if (!["custom_tin_number", "applicant_email_address"].includes(fieldname)) return;
			window.clearTimeout(lookupTimer);
			if (event.type === "change" || event.type === "blur") {
				checkExistingCustomer(true);
			} else {
				lookupTimer = window.setTimeout(() => checkExistingCustomer(true), 500);
			}
		};
		// Capturing listeners work even if another Web Form script fails later.
		document.addEventListener("input", changed, true);
		document.addEventListener("change", changed, true);
	}

	function initialiseWhenReady() {
		if (!window.location.pathname.replace(/\/$/, "").startsWith("/loan-application")) return;
		if (!window.frappe?.web_form?.events) {
			window.setTimeout(initialiseWhenReady, 150);
			return;
		}
		frappe.web_form.events.on("after_load", () => {
			bindDuplicateCheck();
			bindFieldChecks();
			bindImmediateInputChecks();
		});
		// If this asset is injected after the Web Form has already loaded,
		// attach immediately as well.
		window.setTimeout(() => {
			bindDuplicateCheck();
			bindFieldChecks();
			bindImmediateInputChecks();
		}, 300);
	}

	initialiseWhenReady();
	bindDocumentInputChecks();
})();

/*
 * Public loan application calculation refresh.
 *
 * Web Form child tables do not consistently emit `frappe.web_form.on` events
 * while a user is typing.  The application already owns the finance formula
 * (`calculateFinanceSummary`); this small bridge only makes sure that formula
 * is run after the grid has accepted the user's latest value.
 */
(function () {
	if (window.__comfortHomesLoanCalculationRefreshLoaded) return;
	window.__comfortHomesLoanCalculationRefreshLoaded = true;

	let refreshTimer;
	// No source is assumed until the applicant explicitly changes a deposit
	// field.  A form that starts with 10% and zero dollars must calculate the
	// deposit from the percentage once purchase items are entered.
	let depositSource;
	const purchaseTable = "custom_flexi_purchase_items";
	const incomeTables = new Set([
		"custom_flexi_income_entries",
		"custom_flexi_living_expenses",
		"custom_flexi_finance_obligations",
	]);
	const financeFields = new Set([
		"custom_flexi_deposit_amount",
		"custom_flexi_deposit_percent",
		"custom_flexi_surcharge_rate",
		"custom_flexi_documentation_fee",
		"custom_flexi_admin_fee_rate",
		"custom_flexi_stamp_duty_fee",
		"custom_flexi_other_fee",
		"custom_flexi_term_months",
		"custom_flexi_interest_rate",
		"custom_flexi_payment_period",
		"custom_flexi_selected_dsr",
		"loan_product",
	]);

	function isLoanApplicationWebForm() {
		return window.location.pathname.replace(/\/$/, "").startsWith("/loan-application") && window.frappe?.web_form;
	}

	function number(value) {
		const parsed = Number(String(value ?? "").replace(/[^0-9.-]/g, ""));
		return Number.isFinite(parsed) ? parsed : 0;
	}

	function money(value) {
		return window.comfort_hp_math.round(number(value));
	}

	function tableFor(target) {
		return [purchaseTable, ...incomeTables].find((fieldname) =>
			target?.closest?.(`[data-fieldname="${fieldname}"]`)
		);
	}

	function setChildValue(row, fieldname, value) {
		if (money(row[fieldname]) === money(value)) return;
		row[fieldname] = value;
		if (row.doctype && row.name && frappe.model?.set_value) {
			frappe.model.set_value(row.doctype, row.name, fieldname, value);
		}
	}

	function synchronisePurchaseRows() {
		const form = frappe.web_form;
		const rows = form.get_value(purchaseTable) || [];
		const purchaseValue = rows.reduce((total, row) => {
			const lineTotal = money(number(row.quantity) * number(row.unit_price));
			setChildValue(row, "total_amount", lineTotal);
			return total + lineTotal;
		}, 0);
		form.set_value("custom_flexi_cash_price", money(purchaseValue));
		form.fields_dict[purchaseTable]?.refresh?.();
	}

	function triggerFormCalculation(fieldname) {
		const events = frappe.web_form?.events;
		if (events?.trigger) {
			events.trigger(fieldname);
			return true;
		}
		return false;
	}

	function currentDepositSource() {
		if (depositSource) return depositSource;
		const amount = number(frappe.web_form.get_value("custom_flexi_deposit_amount"));
		const percent = number(frappe.web_form.get_value("custom_flexi_deposit_percent"));
		return !amount && percent ? "percent" : "amount";
	}

	function monthlyFactor(frequency) {
		return ({ Weekly: 52 / 12, Fortnightly: 26 / 12, "Bi-monthly": 2, Monthly: 1 })[frequency] || 1;
	}

	function setValue(fieldname, value) {
		if (!frappe.web_form.fields_dict[fieldname]) return;
		const current = frappe.web_form.get_value(fieldname);
		if (typeof value === "number" && money(current) === money(value)) return;
		if (current === value) return;
		frappe.web_form.set_value(fieldname, value);
	}

	function updateHirePurchaseOptionTable(values) {
		const rows = document.querySelectorAll(".flexi-hp-options-table tbody tr");
		if (rows.length < 2) return;
		const keys = ["weekly", "fortnightly", "bimonthly", "monthly"];
		keys.forEach((key, index) => {
			const count = rows[0].children[index + 1];
			const amount = rows[1].children[index + 1];
			if (count) count.textContent = String(values[`${key}_instalments`]);
			if (amount) amount.textContent = `FJD ${money(values[`${key}_instalment_amount`]).toLocaleString(undefined, {
				minimumFractionDigits: 2, maximumFractionDigits: 2,
			})}`;
		});
	}

	function renderDtiMatrix(income, obligations) {
		const field = frappe.web_form.fields_dict.custom_flexi_dti_matrix;
		if (!field?.$wrapper) return;
		const periods = [
			[__("Weekly"), 12 / 52],
			[__("Fortnightly"), 12 / 26],
			[__("Bi-Monthly"), 1 / 2],
			[__("Monthly"), 1],
		];
		const formatCurrency = (value) => `FJD ${money(value).toLocaleString(undefined, {
			minimumFractionDigits: 2,
			maximumFractionDigits: 2,
		})}`;
		const rows = [
			[__("Income Used for DTI"), income],
			[__("Current Obligations"), obligations],
			[__("Income After Current Obligations"), income - obligations],
			[__("Pre-Application DTI"), income ? obligations / income * 100 : 0, true],
		];
		const heading = periods.map(([label]) => `<th>${label}</th>`).join("");
		const body = rows.map(([label, monthly, isPercent]) => {
			const cells = periods.map(([, factor]) => {
				const value = isPercent ? monthly : monthly * factor;
				return `<td>${isPercent ? `${money(value).toFixed(2)}%` : formatCurrency(value)}</td>`;
			}).join("");
			return `<tr><th>${label}</th>${cells}</tr>`;
		}).join("");
		field.$wrapper.html(
			`<div class="flexi-summary-table"><h3>${__("Pre-Application DTI (Debt to Income Ratio)")}</h3>` +
			`<div class="table-responsive"><table><thead><tr><th>${__("Assessment")}</th>${heading}</tr></thead>` +
			`<tbody>${body}</tbody></table></div></div>`
		);
	}

	function currentDtiAssessment() {
		const form = frappe.web_form;
		const income = (form.get_value("custom_flexi_income_entries") || []).reduce((total, row) => {
			const monthly = number(row.amount) * monthlyFactor(row.frequency);
			return total + (row.income_type === "Salary Deductions" ? Math.abs(monthly) : monthly);
		}, 0);
		const obligations = (form.get_value("custom_flexi_finance_obligations") || []).reduce(
			(total, row) => total + number(row.amount) * monthlyFactor(row.frequency), 0
		);
		return { income: money(income), obligations: money(obligations) };
	}

	function calculateIncomeValues() {
		const form = frappe.web_form;
		const incomeRows = form.get_value("custom_flexi_income_entries") || [];
		const expenseRows = form.get_value("custom_flexi_living_expenses") || [];
		const obligationRows = form.get_value("custom_flexi_finance_obligations") || [];
		const income = incomeRows.reduce((sum, row) => {
			const monthlyAmount = number(row.amount) * monthlyFactor(row.frequency);
			setChildValue(row, "monthly_amount", money(monthlyAmount));
			return sum + (row.income_type === "Salary Deductions" ? Math.abs(monthlyAmount) : monthlyAmount);
		}, 0);
		const expenses = expenseRows.reduce((sum, row) => {
			const monthlyAmount = number(row.expense_amount) * number(row.frequency_per_month || 1);
			setChildValue(row, "monthly_amount", money(monthlyAmount));
			return sum + monthlyAmount;
		}, 0);
		const obligations = obligationRows.reduce((sum, row) => {
			const monthlyAmount = number(row.amount) * monthlyFactor(row.frequency);
			setChildValue(row, "monthly_amount", money(monthlyAmount));
			return sum + monthlyAmount;
		}, 0);
		setValue("custom_flexi_total_monthly_income", money(income));
		setValue("custom_flexi_total_annual_income", money(income * 12));
		setValue("custom_flexi_total_monthly_expenses", money(expenses));
		setValue("custom_flexi_net_monthly_income", money(income - expenses));
		setValue("custom_flexi_dsr_total_income", money(income));
		setValue("custom_flexi_total_finance_obligations", money(obligations));
		setValue("custom_flexi_dsr_net_income", money(income - obligations));
		const maxDsr = number(form.get_value("custom_flexi_selected_dsr")) || 35;
		const preDsr = income ? money(obligations / income * 100) : 0;
		setValue("custom_flexi_pre_dsr", preDsr);
		setValue("custom_flexi_dsr_margin", money(Math.max(maxDsr - preDsr, 0)));
		setValue("custom_flexi_weekly_eligibility", money(Math.max((income * maxDsr / 100 - obligations) / window.comfort_hp_math.profile(window.comfort_hp_math.readWeb(form)).weeks, 0)));
		renderDtiMatrix(income, obligations);
		form.fields_dict.custom_flexi_income_entries?.refresh?.();
		form.fields_dict.custom_flexi_living_expenses?.refresh?.();
		form.fields_dict.custom_flexi_finance_obligations?.refresh?.();
		return { income, obligations };
	}

	function calculateFinanceValues(assessment = {}) {
        calculateFinanceSummary(currentDepositSource(), assessment);
    }

	function recalculate(kind) {
		if (!isLoanApplicationWebForm()) return;
		const assessment = kind === "income" ? calculateIncomeValues() : {};
		calculateFinanceValues(assessment);
	}

	function queueRecalculation(kind) {
		window.clearTimeout(refreshTimer);
		// Let Frappe finish its own grid/control change cycle first.  Some
		// Currency controls schedule an older parent calculation for ~100 ms;
		// running after it ensures the current keystroke is the final value.
		refreshTimer = window.setTimeout(() => recalculate(kind), 450);
	}

	function handleChange(event) {
		if (!isLoanApplicationWebForm()) return;
		const target = event.target;
		const fieldname = target?.closest?.("[data-fieldname]")?.dataset?.fieldname;
		const table = tableFor(target);
		if (table === purchaseTable) {
			queueRecalculation("purchase");
			return;
		}
		if (incomeTables.has(table)) {
			queueRecalculation("income");
			return;
		}
		if (!financeFields.has(fieldname)) return;
		if (fieldname === "custom_flexi_deposit_percent") depositSource = "percent";
		if (fieldname === "custom_flexi_deposit_amount") depositSource = "amount";
		queueRecalculation("finance");
	}

	// Capture both input and change: Currency and Data controls use different
	// browser events, and this remains reliable for editable grid rows.
	document.addEventListener("input", handleChange, true);
	document.addEventListener("change", handleChange, true);

	// A user can enter the DTI step after values have already been supplied on
	// the previous page. Draw the matrix from the calculated parent values in
	// that case as well; do not require another edit or a save.
	function renderExistingDtiMatrix() {
		if (!isLoanApplicationWebForm()) return;
		const assessment = currentDtiAssessment();
		renderDtiMatrix(assessment.income, assessment.obligations);
	}
	if (frappe.web_form?.events?.on) frappe.web_form.events.on("after_load", () => window.setTimeout(renderExistingDtiMatrix, 0));
	window.setTimeout(renderExistingDtiMatrix, 700);
	// The legacy form script renders this same HTML field when a page is
	// selected. Run after that handler so an old script cannot replace live
	// figures with its initial zero-value table.
	document.addEventListener("click", (event) => {
		if (!event.target?.closest?.(".slide-step")?.textContent?.includes("DTI")) return;
		window.setTimeout(renderExistingDtiMatrix, 250);
		window.setTimeout(renderExistingDtiMatrix, 900);
	}, true);
})();
