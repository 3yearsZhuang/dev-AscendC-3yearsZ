"""Offline fixtures exercising evidence drift and incomplete judge responses."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("verify_evidence", Path(__file__).resolve().parents[1] / "scripts/verify_evidence.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "kernel.asc"
        self.record_path = self.root / "verified-result.json"
        self.source.write_bytes(b"void kernel() {}\n")
        self.record = {"source_sha256": self.sha(), "submission_id": "a" * 24,
                       "status": "Pass", "passed_testcases": 1, "result": "result.json"}
        self.result = {"_id": "a" * 24, "status": "Pass", "result": [{"testcase_status": "Pass"}],
                       "files": [{"path": "kernel.asc", "content": self.source.read_bytes().decode()}]}

    def sha(self):
        return hashlib.sha256(self.source.read_bytes()).hexdigest()

    def run_check(self, expected=0, write_result=True, repo_root=False):
        self.record_path.write_text(json.dumps(self.record))
        if write_result:
            (self.root / "result.json").write_text(json.dumps(self.result))
        argv = ["--source", str(self.source), "--record", str(self.record_path)]
        if repo_root:
            argv.extend(["--repo-root", str(self.root)])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = verifier.main(argv)
        report = json.loads(output.getvalue())
        self.assertEqual(code, expected, report)
        self.assertEqual(report["exit_code"], expected)
        return report

    def test_consistent_raw_evidence(self):
        report = self.run_check()
        self.assertTrue(report["server_source_verified"])
        self.assertEqual(report["visible_passed_testcases"], 1)

    def test_source_sha_drift(self):
        self.source.write_bytes(b"void changed() {}\n")
        report = self.run_check(1)
        self.assertTrue(any("Current raw source" in x for x in report["errors"]))

    def test_wrong_submission_id(self):
        self.result["_id"] = "b" * 24
        report = self.run_check(1)
        self.assertTrue(any("submission ID" in x for x in report["errors"]))

    def test_missing_result_file(self):
        report = self.run_check(1, write_result=False)
        self.assertTrue(report["unverified"])

    def test_missing_result_path(self):
        del self.record["result"]
        self.run_check(1)

    def test_five_visible_passes_do_not_prove_fifteen(self):
        self.record["passed_testcases"] = 15
        self.result["result"] = [{"testcase_status": "Pass"}] * 5 + [{"testcase_status": "Hidden"}] * 10
        report = self.run_check(1)
        self.assertEqual(report["visible_passed_testcases"], 5)
        self.assertEqual(report["hidden_testcases"], 10)
        self.assertFalse(report["observed_statuses_complete"])

    def test_partial_failure_does_not_inherit_recorded_pass(self):
        self.result.update(status="Wrong Answer", result=[{"testcase_status": "Wrong Answer"}])
        report = self.run_check(1)
        self.assertEqual(report["raw_status"], "Wrong Answer")
        self.assertEqual(report["visible_passed_testcases"], 0)

    def test_fail_is_a_recognized_terminal_and_point_status(self):
        self.result.update(status="Fail", result=[{"testcase_status": "Fail"}])
        self.record.update(status="Fail", passed_testcases=0)
        report = self.run_check()
        self.assertEqual(report["failed_testcases"], 1)
        self.assertEqual(report["unknown_testcases"], 0)

    def test_crlf_preserved_exactly(self):
        self.source.write_bytes(b"void kernel() {}\r\n\r\n")
        self.record["source_sha256"] = self.sha()
        self.result["files"][0]["content"] = self.source.read_bytes().decode()
        self.run_check()

    def test_server_lf_does_not_equal_local_crlf(self):
        self.source.write_bytes(b"void kernel() {}\r\n")
        self.record["source_sha256"] = self.sha()
        report = self.run_check(1)
        self.assertFalse(report["server_source_verified"])

    def test_commented_snapshot_requires_explicit_binding(self):
        (self.root / "snapshot.asc").write_bytes(b"// original\nvoid kernel() {}\n")
        self.record["source_snapshot"] = "snapshot.asc"
        self.run_check(1)

    def test_explicit_snapshot_transform_binding(self):
        snapshot = b"// original\nvoid kernel() {}\n"
        (self.root / "snapshot.asc").write_bytes(snapshot)
        self.record.update(source_snapshot="snapshot.asc", source_transform={
            "input_sha256": hashlib.sha256(snapshot).hexdigest(), "output_sha256": self.sha(),
            "method": "Previously validated source transformation"})
        report = self.run_check()
        self.assertTrue(report["notes"])
        self.assertEqual(report["semantic_verification"], "not_performed")

    def test_reversed_snapshot_transform_is_rejected(self):
        snapshot = b"// original\nvoid kernel() {}\n"
        (self.root / "snapshot.asc").write_bytes(snapshot)
        self.record.update(source_snapshot="snapshot.asc", source_transform={
            "input_sha256": self.sha(), "output_sha256": hashlib.sha256(snapshot).hexdigest(),
            "method": "Reversed binding"})
        report = self.run_check(1)
        self.assertEqual(report["submitted_source_sha256"], self.sha())
        self.assertTrue(any("Snapshot SHA differs" in error for error in report["errors"]))

    def test_missing_snapshot_is_unverified(self):
        self.record["source_snapshot"] = "missing.asc"
        self.run_check(1)

    def test_duplicate_ids_cannot_inflate_coverage(self):
        self.result["result"] = [{"testcase_id": "same", "testcase_status": "Pass"}] * 2
        self.record["passed_testcases"] = 2
        self.run_check(1)

    def test_missing_server_source_is_null(self):
        del self.result["files"]
        report = self.run_check()
        self.assertIsNone(report["server_source_verified"])

    def test_old_server_match_claim_requires_evidence(self):
        del self.result["files"]
        self.record["source_matches_server"] = True
        report = self.run_check(1)
        self.assertIsNone(report["server_source_verified"])

    def test_kernel_cpp_and_files_must_agree(self):
        self.result["kernel_cpp"] = "void different() {}\n"
        self.run_check(1)

    def test_unknown_overall_and_point_status_are_unverified(self):
        self.result.update(status="FutureStatus", result=[{"testcase_status": "FutureStatus"}])
        self.record.update(status="FutureStatus", passed_testcases=0)
        report = self.run_check(1)
        self.assertEqual(report["unknown_testcases"], 1)

    def test_bad_json_is_format_error(self):
        (self.root / "result.json").write_text("{")
        self.run_check(2, write_result=False)

    def test_bad_pass_count_is_format_error(self):
        self.record["passed_testcases"] = True
        self.run_check(2)

    def test_repo_root_and_api_data_wrapper(self):
        self.record_path = self.root / "nested" / "verified-result.json"
        self.record_path.parent.mkdir()
        self.result = {"code": 0, "data": self.result}
        self.run_check(repo_root=True)

    def test_missing_cli_argument_is_json_format_error(self):
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as raised:
            verifier.main([])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue())["exit_code"], 2)


if __name__ == "__main__":
    unittest.main()
