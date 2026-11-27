"""App-owned shortcuts on Lending's standard "Lending" workspace (7.1.4).

Staff had to search for the Loan Bank Reconciliation screen. Lending owns the workspace and may
re-sync it on its own updates, so the shortcut is (re-)added after every migrate rather than
edited by hand. Frappe hides a shortcut from users who cannot read its DocType, so only the
roles with reconciliation access see it.
"""

import json

import frappe

WORKSPACE = "Lending"
SHORTCUTS = (
	{"label": "Loan Bank Reconciliation", "type": "DocType", "link_to": "Loan Bank Reconciliation", "doc_view": "List",
		"color": "Green"},
)


def place_shortcut(blocks, label, col=3):
	"""Pure: content blocks with a shortcut block for ``label`` after the last shortcut (unchanged if present)."""
	blocks = list(blocks or [])
	if any(b.get("type") == "shortcut" and (b.get("data") or {}).get("shortcut_name") == label for b in blocks):
		return blocks, False
	block = {"id": "hp" + "".join(ch for ch in label.lower() if ch.isalnum())[:8],
		"type": "shortcut", "data": {"shortcut_name": label, "col": col}}
	last = max((i for i, b in enumerate(blocks) if b.get("type") == "shortcut"), default=None)
	blocks.insert(0 if last is None else last + 1, block)
	return blocks, True


def ensure_lending_shortcuts():
	"""Idempotent: add the app's shortcuts to the Lending workspace. Called after every migrate."""
	if not frappe.db.exists("Workspace", WORKSPACE):
		return []
	ws = frappe.get_doc("Workspace", WORKSPACE)
	try:
		blocks = json.loads(ws.content or "[]")
	except ValueError:
		blocks = []
	added = []
	for spec in SHORTCUTS:
		if not frappe.db.exists("DocType", spec["link_to"]):
			continue
		if not any(s.label == spec["label"] for s in ws.get("shortcuts") or []):
			ws.append("shortcuts", dict(spec))
			added.append(spec["label"])
		blocks, placed = place_shortcut(blocks, spec["label"])
		if placed and spec["label"] not in added:
			added.append(spec["label"])
	if added:
		ws.content = json.dumps(blocks)
		ws.flags.ignore_permissions = True
		ws.save(ignore_permissions=True)
	return added
