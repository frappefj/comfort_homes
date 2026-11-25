"""Integration-source assertions; require a complete checkout, not just the harness."""
import ast
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def assignment(name):
    tree = ast.parse((ROOT / "hooks.py").read_text())
    return ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign)
                                  and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)))


@unittest.skipUnless((ROOT / "hooks.py").is_file(), "Requires complete checkout after version-checked patch staging")
class TestOriginationWiring(unittest.TestCase):
    def test_native_finalisation_hook_is_registered(self):
        events = assignment("doc_events")
        callback = "comfort_homes.loan_application_workflow.calculate_financials"
        self.assertIn(callback, events["Loan Application"]["before_validate"])
        self.assertEqual(events["Loan Application"]["before_save"], [callback, "comfort_homes.document_controls.validate_application", "comfort_homes.hp_lending.hp_workflow.validate_progress", "comfort_homes.hp_lending.hp_approval.bind_approval"])
        self.assertEqual(events["Co-Applicant"]["before_validate"], "comfort_homes.origination.apply_profile")

    def test_dependencies_precede_workflow(self):
        names = sorted(p.name for p in (ROOT / "fixtures").glob("*.json"))
        # Empty overwrite-safe compatibility fixture prevents a stale overlay
        # from reintroducing the old, premature workflow import.
        self.assertEqual(json.loads((ROOT / "fixtures/workflow.json").read_text()), [])
        self.assertLess(names.index("00_00_role.json"), names.index("00_co_applicant_schema.json"))
        self.assertNotIn("server_script.json", names)  # 7.1: server scripts are app code
        self.assertLess(names.index("workflow_transition_tasks.json"), names.index("zz_workflow.json"))

    def test_export_keeps_dependency_records(self):
        fixtures = assignment("fixtures")
        by_type = {r["dt"]: r for r in fixtures if isinstance(r, dict)}
        self.assertEqual(by_type["Workflow"]["prefix"], "zz")
        self.assertEqual(by_type["Role"]["prefix"], "00_00")
        self.assertIn("Workflow Transition Tasks", by_type)
        self.assertIn("Loan Document Template", by_type["Custom Field"]["filters"][0][2])

    def test_workflow_task_references_have_fixtures(self):
        groups = json.loads((ROOT / "fixtures/workflow_transition_tasks.json").read_text())
        group_names = {r["name"] for r in groups}
        workflow = json.loads((ROOT / "fixtures/zz_workflow.json").read_text())[0]
        references = {r["transition_tasks"] for r in workflow["transitions"] if r.get("transition_tasks")}
        self.assertLessEqual(references, group_names)

    def test_replaced_scripts_are_deleted_by_the_7_1_patch(self):
        from comfort_homes.tests.patch_lists import retire_lists; p = retire_lists()
        for name in ("Calculations", "Flexi Flat Repayment Schedule Sync", "Auto Create Draft PO on Loan Activation After Submission"):
            self.assertIn(name, p.SERVER_SCRIPTS)
