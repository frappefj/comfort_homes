"""Read the 7.1 patch's name lists without importing frappe (tests run without a bench)."""
import ast
from pathlib import Path
from types import SimpleNamespace

PATCH = Path(__file__).resolve().parents[1] / "patches" / "v7_1" / "retire_scripts_and_reports.py"


def retire_lists():
	values = {}
	for node in ast.parse(PATCH.read_text()).body:
		if isinstance(node, ast.Assign):
			for target in node.targets:
				if isinstance(target, ast.Name) and target.id.isupper():
					values[target.id] = ast.literal_eval(node.value)
	return SimpleNamespace(**values)
