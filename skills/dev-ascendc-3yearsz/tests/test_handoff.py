"""Offline handoffs distinguish recorded failures from inconsistent declarations."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "check_handoff", Path(__file__).resolve().parents[1] / "scripts/check_handoff.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def record(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class HandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.out = self.root / "handoff"
        self.out.mkdir()
        self.source = self.root / "frozen.asc"
        self.source.write_bytes(b"// frozen baseline\r\nvoid kernel() {}\r\n")
        self.candidate = self.out / "candidate.asc"
        self.candidate.write_bytes(b"// candidate\r\nvoid kernel() {}\r\n")
        self.log = self.out / "check.log"
        self.log.write_text("Local source checks passed; no NPU claim.\n")
        self.task_path = self.root / "task.json"
        self.result_path = self.out / "result.json"
        self.task = {"schema_version": 1, "task_id": "candidate-01", "role": "implement",
                     "objective": "Write a candidate from the frozen baseline",
                     "input_sources": [record(self.source)],
                     "write_scope": [{"path": str(self.out), "kind": "directory"}],
                     "output_path": str(self.result_path),
                     "authorized_actions": ["read", "local_write", "local_test"],
                     "required_artifacts": [str(self.candidate)]}
        self.result = {"schema_version": 1, "task_id": "candidate-01", "role": "implement",
                       "status": "completed", "verdict": "PASS",
                       "input_sources": copy.deepcopy(self.task["input_sources"]),
                       "artifacts": [record(self.candidate), record(self.log)],
                       "actions_taken": ["read", "local_write", "local_test"],
                       "checks": [{"name": "local-source-check", "status": "passed",
                                   "evidence": [str(self.log)]}],
                       "unresolved": [], "inflight_requests": []}

    def run_check(self, expected=0, write_task=True, write_result=True):
        if write_task:
            self.task_path.write_text(json.dumps(self.task))
        if write_result:
            self.result_path.write_text(json.dumps(self.result))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = checker.main(["--task", str(self.task_path), "--result", str(self.result_path)])
        report = json.loads(output.getvalue())
        self.assertEqual(code, expected, report)
        self.assertEqual(report["exit_code"], expected)
        self.assertEqual(report["eligible"], report["ready_for_next_step"])
        if expected == 2:
            self.assertTrue(report["format_errors"])
        return report

    def test_consistent_same_source_evidence(self):
        report = self.run_check()
        self.assertTrue(report["structurally_valid"])
        self.assertTrue(report["ready_for_next_step"])
        self.assertEqual(report["scope"], "declared_handoff_consistency")
        self.assertIs(report["evidence_authenticity_verified"], False)

    def test_stale_input_sha(self):
        self.source.write_bytes(b"changed after dispatch\n")
        report = self.run_check(1)
        self.assertFalse(report["structurally_valid"])
        self.assertTrue(any("Input source SHA" in error for error in report["errors"]))

    def test_review_is_stale_after_candidate_changes(self):
        self.task["role"] = self.result["role"] = "review"
        self.task["input_sources"] = [record(self.candidate)]
        self.result["input_sources"] = copy.deepcopy(self.task["input_sources"])
        self.run_check()
        self.candidate.write_bytes(b"changed candidate\n")
        self.result["artifacts"][0] = record(self.candidate)
        self.run_check(1)

    def test_result_cannot_rebind_changed_input(self):
        self.source.write_bytes(b"new version\n")
        self.result["input_sources"] = [record(self.source)]
        report = self.run_check(1)
        self.assertTrue(any("complete task path/SHA list" in error for error in report["errors"]))

    def test_input_newline_normalization_is_a_change(self):
        self.source.write_bytes(self.source.read_bytes().replace(b"\r\n", b"\n"))
        self.run_check(1)

    def test_wrong_input_path_even_with_identical_bytes(self):
        other = self.root / "other.asc"
        other.write_bytes(self.source.read_bytes())
        self.result["input_sources"] = [record(other)]
        self.run_check(1)

    def test_missing_input_file(self):
        self.source.unlink()
        self.run_check(1)

    def test_incomplete_input_list(self):
        self.result["input_sources"] = []
        self.run_check(1)

    def test_duplicate_input_paths(self):
        self.task["input_sources"] *= 2
        self.result["input_sources"] *= 2
        self.run_check(1)

    def test_input_list_order_does_not_change_binding(self):
        second = self.root / "contract.txt"
        second.write_text("fp32; preserve entry point\n")
        self.task["input_sources"].append(record(second))
        self.result["input_sources"] = list(reversed(self.task["input_sources"]))
        self.run_check()

    def test_wrong_task_identity(self):
        self.result["task_id"] = "different-task"
        self.run_check(1)

    def test_wrong_role(self):
        self.result["role"] = "judge"
        self.run_check(1)

    def test_actions_outside_authorization(self):
        self.task["authorized_actions"].remove("local_test")
        self.run_check(1)

    def test_nonjudge_cannot_take_judge_action(self):
        self.result["actions_taken"].append("judge_get")
        self.run_check(1)

    def test_nonjudge_cannot_receive_judge_authorization(self):
        self.task["authorized_actions"].append("judge_post")
        self.run_check(1)

    def test_judge_may_declare_authorized_platform_actions(self):
        self.task["role"] = self.result["role"] = "judge"
        self.task["authorized_actions"] += ["judge_get", "judge_post"]
        self.result["actions_taken"] += ["judge_get", "judge_post"]
        self.run_check()

    def test_reference_read_is_not_a_judge_action(self):
        self.task["role"] = self.result["role"] = "plan"
        self.result["actions_taken"] = ["read", "local_write"]
        self.run_check()

    def test_result_must_be_at_expected_path(self):
        self.result_path = self.out / "wrong-result.json"
        self.run_check(1)

    def test_result_outside_write_scope(self):
        self.task["write_scope"] = [{"path": str(self.candidate), "kind": "file"},
                                    {"path": str(self.log), "kind": "file"}]
        self.run_check(1)

    def test_precise_file_scopes_allow_declared_outputs(self):
        self.task["write_scope"] = [{"path": str(path), "kind": "file"}
                                    for path in (self.result_path, self.candidate, self.log)]
        self.run_check()

    def test_artifact_outside_write_scope(self):
        outside = self.root / "outside.asc"
        outside.write_bytes(b"outside")
        self.result["artifacts"].append(record(outside))
        self.run_check(1)

    def test_directory_prefix_is_not_a_scope_match(self):
        sibling = self.root / "handoff-extra"
        sibling.mkdir()
        outside = sibling / "outside.asc"
        outside.write_bytes(b"outside")
        self.result["artifacts"].append(record(outside))
        self.run_check(1)

    def test_symlink_cannot_escape_write_scope(self):
        outside = self.root / "outside.asc"
        outside.write_bytes(self.candidate.read_bytes())
        self.candidate.unlink()
        self.candidate.symlink_to(outside)
        self.run_check(1)

    def test_dotdot_cannot_escape_write_scope(self):
        outside = self.root / "outside.asc"
        outside.write_bytes(b"outside")
        self.result["artifacts"].append(record(self.out / ".." / "outside.asc"))
        self.run_check(1)

    def test_implement_artifact_cannot_be_frozen_input(self):
        self.task["write_scope"].append({"path": str(self.source), "kind": "file"})
        self.result["artifacts"].append(record(self.source))
        self.run_check(1)

    def test_hardlink_candidate_cannot_alias_frozen_input(self):
        self.candidate.unlink()
        self.candidate.hardlink_to(self.source)
        self.result["artifacts"][0] = record(self.candidate)
        self.run_check(1)

    def test_missing_required_artifact_declaration(self):
        self.result["artifacts"] = [record(self.log)]
        self.run_check(1)

    def test_missing_artifact_file(self):
        self.candidate.unlink()
        self.run_check(1)

    def test_artifact_sha_drift(self):
        self.candidate.write_bytes(b"new candidate")
        self.run_check(1)

    def test_duplicate_artifact_paths(self):
        self.result["artifacts"].append(copy.deepcopy(self.result["artifacts"][0]))
        self.run_check(1)

    def test_pass_rejects_skipped_checks(self):
        self.result["checks"].append({"name": "target-compile", "status": "skipped", "evidence": []})
        self.run_check(1)

    def test_pass_rejects_not_run_checks(self):
        self.result["checks"][0]["status"] = "not_run"
        self.run_check(1)

    def test_pass_rejects_failed_checks(self):
        self.result["checks"][0]["status"] = "failed"
        self.run_check(1)

    def test_pass_rejects_empty_checks(self):
        self.result["checks"] = []
        self.run_check(1)

    def test_duplicate_check_names(self):
        self.result["checks"] *= 2
        self.run_check(1)

    def test_passed_check_requires_evidence(self):
        self.result["checks"][0]["evidence"] = []
        self.run_check(1)

    def test_passed_check_requires_evidence_even_for_fail_verdict(self):
        self.result["verdict"] = "FAIL"
        self.result["checks"][0]["evidence"] = []
        self.run_check(1)

    def test_missing_evidence_file(self):
        self.result["checks"][0]["evidence"] = [str(self.out / "absent.log")]
        self.run_check(1)

    def test_directory_is_not_evidence(self):
        self.result["checks"][0]["evidence"] = [str(self.out)]
        self.run_check(1)

    def test_normal_fail_remains_a_valid_record(self):
        self.result["verdict"] = "FAIL"
        self.result["checks"][0]["status"] = "failed"
        report = self.run_check(1)
        self.assertTrue(report["structurally_valid"])
        self.assertFalse(report["eligible"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["verdict"], "FAIL")
        self.assertTrue(report["issues"])

    def test_concern_can_be_completed_but_not_ready(self):
        self.result["verdict"] = "CONCERN"
        report = self.run_check(1)
        self.assertTrue(report["structurally_valid"])
        self.assertFalse(report["ready_for_next_step"])

    def test_incomplete_preserves_unresolved_and_inflight_requests(self):
        self.result.update(status="incomplete", verdict="CONCERN", unresolved=["Await judge terminal status"],
                           inflight_requests=[{"attempt": "attempt-03", "submission_id": None}])
        report = self.run_check(1)
        self.assertTrue(report["structurally_valid"])
        self.assertEqual(report["unresolved"], self.result["unresolved"])
        self.assertEqual(report["inflight_requests"], self.result["inflight_requests"])

    def test_completed_cannot_claim_unresolved_items(self):
        self.result["unresolved"] = ["Compile has not run"]
        report = self.run_check(1)
        self.assertFalse(report["structurally_valid"])

    def test_not_applicable_does_not_invent_test_requirement(self):
        self.task["allow_not_applicable"] = True
        self.result.update(verdict="NOT_APPLICABLE", checks=[],
                           not_applicable_reason="Documentation-only task has no executable checks")
        report = self.run_check()
        self.assertEqual(report["not_applicable_reason"], self.result["not_applicable_reason"])

    def test_not_applicable_requires_explicit_task_allowance(self):
        self.result.update(verdict="NOT_APPLICABLE", checks=[], not_applicable_reason="Documentation-only task")
        report = self.run_check(1)
        self.assertFalse(report["structurally_valid"])
        self.assertTrue(any("allow_not_applicable=true" in error for error in report["errors"]))

    def test_not_applicable_explicit_false_does_not_allow_bypass(self):
        self.task["allow_not_applicable"] = False
        self.result.update(verdict="NOT_APPLICABLE", checks=[], not_applicable_reason="Documentation-only task")
        self.run_check(1)

    def test_not_applicable_requires_reason(self):
        self.task["allow_not_applicable"] = True
        self.result.update(verdict="NOT_APPLICABLE", checks=[])
        report = self.run_check(1)
        self.assertFalse(report["structurally_valid"])
        self.assertTrue(any("nonempty not_applicable_reason" in error for error in report["errors"]))

    def test_not_applicable_rejects_blank_reason(self):
        self.task["allow_not_applicable"] = True
        self.result.update(verdict="NOT_APPLICABLE", checks=[], not_applicable_reason=" \t\n")
        self.run_check(1)

    def test_not_applicable_cannot_hide_failed_check(self):
        self.task["allow_not_applicable"] = True
        self.result.update(verdict="NOT_APPLICABLE", not_applicable_reason="Documentation-only task")
        self.result["checks"][0]["status"] = "failed"
        report = self.run_check(1)
        self.assertFalse(report["structurally_valid"])
        self.assertTrue(any("cannot contain failed checks" in error for error in report["errors"]))

    def test_not_applicable_can_preserve_skipped_and_not_run_checks(self):
        self.task["allow_not_applicable"] = True
        self.result.update(verdict="NOT_APPLICABLE", not_applicable_reason="Target checks outside the documentation task",
                           checks=[{"name": "target-compile", "status": "skipped", "evidence": []},
                                   {"name": "target-run", "status": "not_run", "evidence": []}])
        self.run_check()

    def test_not_applicable_allowance_requires_boolean_type(self):
        self.task["allow_not_applicable"] = 1
        self.run_check(2)

    def test_not_applicable_reason_requires_string_type(self):
        self.task["allow_not_applicable"] = True
        self.result.update(verdict="NOT_APPLICABLE", checks=[], not_applicable_reason=None)
        self.run_check(2)

    def test_device_limitations_do_not_block_static_handoff(self):
        self.result["limitations"] = ["Static review only; no NPU is available"]
        report = self.run_check()
        self.assertEqual(report["limitations"], self.result["limitations"])

    def test_limitations_must_be_string_array(self):
        self.result["limitations"] = [None]
        self.run_check(2)

    def test_inflight_declarations_are_preserved_without_live_verification(self):
        self.result["inflight_requests"] = [{"attempt": "attempt-04", "state": "historical"}]
        report = self.run_check()
        self.assertEqual(report["inflight_requests"], self.result["inflight_requests"])
        self.assertTrue(any("live state is not verified" in issue for issue in report["issues"]))

    def test_uppercase_hex_hashes_are_valid(self):
        for item in self.result["input_sources"] + self.result["artifacts"]:
            item["sha256"] = item["sha256"].upper()
        self.run_check()

    def test_bad_json(self):
        self.result_path.write_text("{")
        self.run_check(2, write_result=False)

    def test_duplicate_json_keys(self):
        self.result_path.write_text('{"schema_version": 1, "schema_version": 2}')
        self.run_check(2, write_result=False)

    def test_nonfinite_json_constant(self):
        self.result["inflight_requests"] = [{"value": float("nan")}]
        self.run_check(2)

    def test_missing_result_file_is_unverifiable(self):
        report = self.run_check(1, write_result=False)
        self.assertFalse(report["structurally_valid"])

    def test_missing_schema_field(self):
        del self.result["checks"]
        self.run_check(2)

    def test_bool_is_not_schema_version(self):
        self.task["schema_version"] = True
        self.run_check(2)

    def test_relative_input_path_is_invalid(self):
        self.task["input_sources"][0]["path"] = "frozen.asc"
        self.run_check(2)

    def test_malformed_sha(self):
        self.result["artifacts"][0]["sha256"] = "z" * 64
        self.run_check(2)

    def test_unknown_action_is_schema_error(self):
        self.result["actions_taken"].append("network_everything")
        self.run_check(2)

    def test_inflight_requests_must_be_objects(self):
        self.result["inflight_requests"] = ["pending"]
        self.run_check(2)

    def test_verification_does_not_execute_artifact(self):
        marker = self.root / "must-not-exist"
        self.candidate.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        self.candidate.chmod(0o700)
        self.result["artifacts"][0] = record(self.candidate)
        before = self.candidate.read_bytes()
        self.run_check()
        self.assertEqual(self.candidate.read_bytes(), before)
        self.assertFalse(marker.exists())

    def test_missing_cli_arguments_emit_json(self):
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as raised:
            checker.main([])
        self.assertEqual(raised.exception.code, 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report["exit_code"], 2)
        self.assertFalse(report["structurally_valid"])


if __name__ == "__main__":
    unittest.main()
