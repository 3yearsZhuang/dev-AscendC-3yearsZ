"""Offline regressions: no real credentials, login, HTTP requests, or submissions."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cannjudge.py"
spec = importlib.util.spec_from_file_location("persistent_cannjudge", SCRIPT)
cj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cj)


@pytest.fixture(autouse=True)
def forbid_live_network_and_session(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A test attempted real HTTP or session access")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(cj.sdk, "load_session", forbidden)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "kernel.asc").write_bytes(b"\xef\xbb\xbf// Keep comment\r\nvoid kernel() {}\r\n")
    (root / "judge.asc").write_text("// readonly\n", encoding="utf-8")
    (root / "helper.h").write_text("// helper\n", encoding="utf-8")
    return root


@pytest.fixture
def client():
    client = cj.sdk.CANNJudgeClient(session_file="/does-not-exist/test-only-session")
    client.resolve_problem = Mock(return_value={"_id": "problem-1", "code_template": "npu_kernel_dev"})
    client.get_template = Mock(return_value={"data": {"files": [
        {"path": "kernel.asc", "content": "// template\n", "editable": True},
        {"path": "judge.asc", "content": "// readonly\n", "editable": False},
    ]}})
    def authenticate():
        client.user_id = "user-1"
        return True
    client.load_session = Mock(side_effect=authenticate)
    client.logout = Mock()
    client.session.post = Mock(return_value=Mock(status_code=200, json=lambda: {"data": {"submissionId": "submission-1"}}))
    client.get_submission = Mock(return_value={"_id": "submission-1", "problemId": "problem-1",
                                              "userId": "user-1", "status": "Running", "result": []})
    return client


@pytest.fixture
def attempt(tmp_path, client, project):
    directory = tmp_path / "attempt"
    cj.prepare(client, "problem-1", project, directory, 15)
    return directory


def test_prepare_freezes_sdk_text_without_session_or_comment_stripping(client, project, attempt):
    client.load_session.assert_not_called()
    assert cj.read(attempt / "prepared-payload.json")["userId"] is None
    raw = (attempt / "sources" / "kernel.asc").read_bytes()
    assert raw == b"// Keep comment\nvoid kernel() {}\n"
    assert not (attempt / "sources" / "judge.asc").exists()
    assert (attempt / "sources" / "helper.h").read_bytes() == b"// helper\n"
    (project / "kernel.asc").write_text("changed", encoding="utf-8")
    assert (attempt / "sources" / "kernel.asc").read_bytes() == raw
    state = cj.read(attempt / "state.json")
    assert cj.verify_snapshot(attempt, state)["problemId"] == "problem-1"
    assert state["source_sha256"]["kernel.asc"] == cj.sha(raw)


def test_readonly_template_modification_is_rejected(client, project, tmp_path):
    (project / "judge.asc").write_text("modified", encoding="utf-8")
    with pytest.raises(ValueError, match="只读"):
        cj.prepare(client, "problem-1", project, tmp_path / "bad")
    client.session.post.assert_not_called()


def test_reprepare_cannot_overwrite_attempt(client, project, attempt):
    before = (attempt / "state.json").read_bytes()
    with pytest.raises(cj.Error, match="new empty"):
        cj.prepare(client, "problem-1", project, attempt)
    assert (attempt / "state.json").read_bytes() == before


def test_post_is_once_and_exact_payload_is_durable_before_dispatch(client, project, attempt):
    (project / "kernel.asc").write_text("modified after prepare", encoding="utf-8")
    def post(url, **kwargs):
        state = cj.read(attempt / "state.json")
        assert state["phase"] == "dispatching"
        assert (attempt / "dispatch.json").is_file()
        assert kwargs["data"] == (attempt / "payload.json").read_bytes()
        assert cj.sha(kwargs["data"]) == state["payload_sha256"]
        payload = json.loads(kwargs["data"])
        assert payload["userId"] == "user-1"
        assert "modified after prepare" not in str(payload)
        assert kwargs["allow_redirects"] is False
        return Mock(status_code=200, json=lambda: {"data": {"submissionId": "submission-1"}})
    client.session.post.side_effect = post
    assert cj.submit(client, attempt)["submission_id"] == "submission-1"
    with pytest.raises(cj.Error, match="cannot POST again"):
        cj.submit(client, attempt)
    assert client.session.post.call_count == 1
    assert cj.read(attempt / "receipt.json")["submission_id"] == "submission-1"


def test_timeout_is_unknown_and_never_reposts(client, attempt):
    client.session.post.side_effect = requests.Timeout("do not log server secrets")
    with pytest.raises(cj.Error, match="never retry"):
        cj.submit(client, attempt)
    state = cj.read(attempt / "state.json")
    assert state["phase"] == "unknown"
    assert state["request_identity_confirmed"] is False
    with pytest.raises(cj.Error, match="cannot POST again"):
        cj.submit(client, attempt)
    assert client.session.post.call_count == 1


@pytest.mark.parametrize("status, body", [(200, {}), (200, {"data": {"submissionId": 12}}), (429, {"error": "cooldown"}), (307, {})])
def test_missing_id_or_rejection_still_saves_receipt_and_blocks_replay(client, attempt, status, body):
    client.session.post.return_value = Mock(status_code=status, json=lambda: body)
    with pytest.raises(cj.Error, match="never retry"):
        cj.submit(client, attempt)
    assert cj.read(attempt / "receipt.json")["response"] == body
    assert cj.read(attempt / "state.json")["phase"] == "unknown"
    with pytest.raises(cj.Error):
        cj.submit(client, attempt)
    assert client.session.post.call_count == 1


def test_non_json_receipt_is_preserved(client, attempt):
    response = Mock(status_code=502, text="gateway error", json=Mock(side_effect=ValueError()))
    client.session.post.return_value = response
    with pytest.raises(cj.Error):
        cj.submit(client, attempt)
    assert cj.read(attempt / "receipt.json")["response_text"] == "gateway error"


def test_receipt_disk_failure_saves_id_for_get_recovery_without_repost(client, attempt, monkeypatch):
    original = cj.atomic_write
    def fail_receipt(destination, data):
        if Path(destination).name == "receipt.json":
            raise OSError("disk error")
        return original(destination, data)
    monkeypatch.setattr(cj, "atomic_write", fail_receipt)
    with pytest.raises(cj.Error, match="Known ID: submission-1"):
        cj.submit(client, attempt)
    state = cj.read(attempt / "state.json")
    assert state["phase"] == "unknown"
    assert state["submission_id"] == "submission-1"
    assert cj.query(client, attempt)["raw_status"] == "Running"
    client.get_submission.assert_called_once_with("submission-1")
    with pytest.raises(cj.Error):
        cj.submit(client, attempt)
    assert client.session.post.call_count == 1


def test_crash_with_dispatch_state_and_receipt_recovers_known_id(client, attempt):
    cj.submit(client, attempt)
    state = cj.read(attempt / "state.json")
    state.update(phase="dispatching", request_identity_confirmed=False)
    del state["submission_id"]
    cj.save(attempt / "state.json", state)
    summary = cj.query(client, attempt)
    assert summary["submission_id"] == "submission-1"
    assert summary["request_identity_confirmed"] is True
    with pytest.raises(cj.Error):
        cj.submit(client, attempt)
    assert client.session.post.call_count == 1


def test_interrupt_after_dispatch_cannot_replay(client, attempt):
    client.session.post.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        cj.submit(client, attempt)
    assert cj.read(attempt / "state.json")["phase"] == "unknown"
    with pytest.raises(cj.Error):
        cj.submit(client, attempt)
    assert client.session.post.call_count == 1


def test_state_write_failure_after_marker_never_posts_or_replays(client, attempt, monkeypatch):
    original = cj.save
    def fail_dispatch_state(destination, value):
        if Path(destination).name == "state.json" and value["phase"] == "dispatching":
            raise OSError("write failed")
        return original(destination, value)
    monkeypatch.setattr(cj, "save", fail_dispatch_state)
    with pytest.raises(OSError):
        cj.submit(client, attempt)
    assert (attempt / "dispatch.json").is_file()
    with pytest.raises(cj.Error):
        cj.submit(client, attempt)
    client.session.post.assert_not_called()


def test_get_timeout_can_resume_same_id_and_retains_complete_result(client, attempt):
    cj.submit(client, attempt)
    full_result = {"_id": "submission-1", "problemId": "problem-1", "userId": "user-1",
                   "status": "Pass", "msg": "full log", "score": 1,
                   "result": [{"testcase_status": "Pass", "time": 3.5, "unit": "us", "precision_ratio": 1}]}
    client.get_submission.side_effect = [requests.Timeout(), full_result]
    with pytest.raises(requests.Timeout):
        cj.query(client, attempt)
    summary = cj.query(client, attempt)
    assert cj.read(attempt / "result.json") == full_result
    assert summary["terminal"] is True
    assert summary["all_tests_verified"] is False
    assert [call.args[0] for call in client.get_submission.call_args_list] == ["submission-1", "submission-1"]
    assert client.session.post.call_count == 1


@pytest.mark.parametrize("status", ["Running", "Finish", "Finished", "unknown", None, "UnrecognizedNewStatus", {"new_schema": True}])
def test_unknown_status_is_not_terminal_or_all_pass(status):
    summary = cj.summarize({"status": status, "result": [{"testcase_status": "Pass"}]}, {"expected_testcases": 1})
    assert summary["terminal"] is False
    assert summary["all_tests_verified"] is False


@pytest.mark.parametrize("status", sorted(cj.TERMINAL))
def test_known_terminal_statuses(status):
    assert cj.summarize({"status": status}, {})["terminal"] is True


def test_visible_five_of_fifteen_is_not_all_tests_verified():
    result = {"status": "Pass", "result": [{"testcase_id": i, "testcase_status": "Pass", "time": 1} for i in range(5)]}
    summary = cj.summarize(result, {"expected_testcases": 15})
    assert summary["visible_passed_count"] == summary["visible_testcase_count"] == 5
    assert summary["expected_testcase_count"] == 15
    assert summary["all_tests_verified"] is False
    assert cj.summarize(result, {})["all_tests_verified"] is False
    assert cj.summarize(result, {"expected_testcases": 5})["all_tests_verified"] is True


def test_failed_zero_and_unknown_units_do_not_create_speed_claims():
    summary = cj.summarize({"status": "Fail", "result": [
        {"testcase_status": "RE", "time": 0},
        {"testcase_status": "Pass", "time": 2, "time_unit": "platform-native"},
        {"testcase_status": "Pass", "time": "3 us"},
    ]}, {"expected_testcases": 3})
    assert summary["testcases"][0]["time_raw"] == 0
    assert summary["testcases"][0]["timing_eligible"] is False
    assert summary["testcases"][0]["time_unit_raw"] is None
    assert summary["testcases"][1]["time_unit_raw"] == "platform-native"
    assert summary["testcases"][2]["time_raw"] == "3 us"
    assert summary["all_tests_verified"] is False


def unknown_attempt(client, attempt):
    client.session.post.side_effect = requests.Timeout()
    with pytest.raises(cj.Error):
        cj.submit(client, attempt)
    payload = cj.read(attempt / "payload.json")
    return dict(payload, _id="candidate-1", status="Pass", result=[])


def test_reconcile_matches_source_but_never_claims_request_identity(client, attempt):
    candidate = unknown_attempt(client, attempt)
    client.get_submission.return_value = candidate
    before = (attempt / "state.json").read_bytes()
    association = cj.reconcile(client, attempt, "candidate-1")
    assert association["source_match"] is True
    assert association["request_identity_confirmed"] is False
    assert (attempt / "state.json").read_bytes() == before
    assert cj.read(attempt / "associations" / "candidate-1" / "result.json") == candidate
    assert cj.read(attempt / "associations" / "candidate-1" / "summary.json")["request_identity_confirmed"] is False
    with pytest.raises(cj.Error):
        cj.query(client, attempt)
    with pytest.raises(cj.Error):
        cj.submit(client, attempt)
    assert client.session.post.call_count == 1


@pytest.mark.parametrize("mutation", ["source", "missing_source", "extra_source", "owner", "problem", "missing_owner", "id"])
def test_reconcile_rejects_incomplete_or_mismatched_evidence(client, attempt, mutation):
    candidate = unknown_attempt(client, attempt)
    if mutation == "source":
        candidate["files"][0]["content"] += "// difference\n"
    elif mutation == "missing_source":
        del candidate["files"]
    elif mutation == "extra_source":
        candidate["files"].append({"path": "extra.h", "content": "extra"})
    elif mutation == "owner":
        candidate["userId"] = "other"
    elif mutation == "problem":
        candidate["problemId"] = "other"
    elif mutation == "missing_owner":
        del candidate["userId"]
    else:
        candidate["_id"] = "other"
    client.get_submission.return_value = candidate
    with pytest.raises(cj.Error):
        cj.reconcile(client, attempt, "candidate-1")
    assert not (attempt / "association.json").exists()
    assert cj.read(attempt / "state.json")["phase"] == "unknown"


@pytest.mark.parametrize("artifact", ["prepared-payload.json", "sources/kernel.asc", "problem.json", "template.json"])
def test_tampered_prepared_artifact_blocks_post(client, attempt, artifact):
    with (attempt / artifact).open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(cj.Error, match="hash mismatch"):
        cj.submit(client, attempt)
    client.session.post.assert_not_called()


def test_tampered_final_payload_blocks_get(client, attempt):
    cj.submit(client, attempt)
    with (attempt / "payload.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(cj.Error, match="hash mismatch"):
        cj.query(client, attempt)
    client.get_submission.assert_not_called()


def test_lock_rejects_concurrent_attempt_use(client, attempt):
    with cj.locked(attempt):
        with pytest.raises(cj.Error, match="Another process"):
            cj.submit(client, attempt)
    client.session.post.assert_not_called()


def test_rank_preserves_raw_api_order_and_every_row(client, monkeypatch, capsys):
    rows = [{"status": "Fail", "score": 100}, {"status": "Pass", "score": 10}]
    client.get_rankings = Mock(return_value=rows)
    monkeypatch.setattr(cj.sdk, "CANNJudgeClient", Mock(return_value=client))
    assert cj.main(["rank", "--problem-id", "problem-1"]) == 0
    assert json.loads(capsys.readouterr().out) == rows
    client.load_session.assert_called_once_with()


def test_missing_login_keeps_prepared_attempt_eligible_without_dispatch(client, attempt):
    client.load_session.side_effect = None
    client.load_session.return_value = False
    with pytest.raises(cj.Error, match="Run login"):
        cj.submit(client, attempt)
    assert cj.read(attempt / "state.json")["phase"] == "prepared"
    assert not (attempt / "dispatch.json").exists()
    client.session.post.assert_not_called()


def test_changed_account_cannot_resume_another_owners_attempt(client, attempt):
    cj.submit(client, attempt)
    client.load_session.side_effect = lambda: setattr(client, "user_id", "other-user") or True
    with pytest.raises(cj.Error, match="different account"):
        cj.query(client, attempt)
    client.get_submission.assert_not_called()


def test_cli_resume_only_gets_known_id(client, attempt, monkeypatch, capsys):
    cj.submit(client, attempt)
    monkeypatch.setattr(cj.sdk, "CANNJudgeClient", Mock(return_value=client))
    assert cj.main(["resume", "--attempt-dir", str(attempt)]) == 0
    assert json.loads(capsys.readouterr().out)["submission_id"] == "submission-1"
    client.get_submission.assert_called_once_with("submission-1")
    assert client.session.post.call_count == 1


def test_registry_submission_collects_and_binds_all_four_fields(client, tmp_path):
    project = tmp_path / "registry"
    (project / "op_kernel").mkdir(parents=True)
    (project / "op_host").mkdir()
    (project / "op_kernel" / "op_tiling.h").write_text("// tiling\n", encoding="utf-8")
    (project / "op_kernel" / "op.cpp").write_text("// kernel\n", encoding="utf-8")
    (project / "op_host" / "op.cpp").write_text("// host\n", encoding="utf-8")
    client.resolve_problem.return_value = {"_id": "problem-1", "code_template": "registry"}
    directory = tmp_path / "registry-attempt"
    cj.prepare(client, "problem-1", project, directory)
    payload = cj.read(directory / "prepared-payload.json")
    assert payload["tiling_key_h"] == ""
    assert set(cj.sources(payload)) == cj.REGISTRY
    cj.submit(client, directory)
    candidate = dict(cj.read(directory / "payload.json"), _id="submission-1", status="Pass", files=[])
    client.get_submission.return_value = candidate
    assert cj.reconcile(client, directory, "submission-1")["source_match"] is True
    del candidate["tiling_key_h"]
    with pytest.raises(cj.Error, match="Complete registry"):
        cj.reconcile(client, directory, "submission-1")


def test_fifteen_rows_are_five_visible_and_ten_hidden():
    # Synthetic rows preserve visibility and coverage without contest telemetry.
    identifiers = [f"testcase-{i:02d}" for i in range(15)]
    times = [10.0, 20.0, 30.0, 40.0, 50.0] + [None] * 10
    result = {"status": "Pass", "result": [
        {"testcase_id": identifier, "testcase_status": "Pass" if i < 5 else "Hidden",
         "precision_ratio": 1 if i < 5 else None, "time": times[i]}
        for i, identifier in enumerate(identifiers)]}
    summary = cj.summarize(result, {"expected_testcases": 15})
    assert summary["returned_testcase_count"] == 15
    assert summary["visible_testcase_count"] == summary["visible_passed_count"] == 5
    assert summary["hidden_testcase_count"] == 10
    assert summary["unknown_testcase_count"] == summary["failed_count"] == 0
    assert summary["distinct_testcases_verified"] is True
    assert summary["all_tests_verified"] is False
    assert [case["testcase_id"] for case in summary["testcases"]] == identifiers
    assert summary["testcases"][0]["precision_ratio"] == 1


def test_unknown_withheld_and_failed_cases_have_distinct_counts():
    rows = [{"testcase_id": str(i), "testcase_status": status}
            for i, status in enumerate(["Pass", "WA", "Withheld", "NewStatus", "Running"])]
    summary = cj.summarize({"status": "Fail", "result": rows}, {"expected_testcases": 5})
    assert summary["returned_testcase_count"] == 5
    assert summary["visible_testcase_count"] == 2
    assert summary["visible_passed_count"] == summary["failed_count"] == summary["hidden_testcase_count"] == 1
    assert summary["unknown_testcase_count"] == 2


@pytest.mark.parametrize("ids, distinct", [(["same", "same"], False), ([None, None], None),
                                         (["known", None], None), ([1, "1"], False)])
def test_repeated_or_missing_testcase_ids_never_prove_unique_coverage(ids, distinct):
    result = {"status": "Pass", "result": [{"testcase_id": value, "testcase_status": "Pass"} for value in ids]}
    summary = cj.summarize(result, {"expected_testcases": 2})
    assert summary["distinct_testcases_verified"] is distinct
    assert summary["all_tests_verified"] is False


def valid_result(attempt):
    return dict(cj.read(attempt / "payload.json"), _id="submission-1", status="Pass",
                result=[{"testcase_id": str(i), "testcase_status": "Pass"} for i in range(15)])


@pytest.mark.parametrize("mutation", ["id", "missing_id", "conflicting_id", "owner", "problem", "source", "partial_source"])
def test_query_rejects_binding_errors_but_retains_raw_response(client, attempt, mutation):
    cj.submit(client, attempt)
    result = valid_result(attempt)
    if mutation == "id":
        result["_id"] = "another-id"
    elif mutation == "missing_id":
        del result["_id"]
    elif mutation == "conflicting_id":
        result["submissionId"] = "another-id"
    elif mutation == "owner":
        result["userId"] = "another-user"
    elif mutation == "problem":
        result["problemId"] = "another-problem"
    elif mutation == "source":
        result["files"][0]["content"] += "// changed"
    else:
        result["files"].pop()
    client.get_submission.return_value = result
    with pytest.raises(cj.Error):
        cj.query(client, attempt)
    assert cj.read(attempt / "last-query-result.json") == result
    assert not (attempt / "result.json").exists()
    assert not (attempt / "summary.json").exists()
    assert cj.read(attempt / "state.json")["phase"] == "submitted"


def test_wrong_query_never_overwrites_previously_bound_result(client, attempt):
    cj.submit(client, attempt)
    client.get_submission.return_value = valid_result(attempt)
    cj.query(client, attempt)
    bound = (attempt / "result.json").read_bytes()
    summary = (attempt / "summary.json").read_bytes()
    client.get_submission.return_value = dict(valid_result(attempt), _id="wrong-id")
    with pytest.raises(cj.Error):
        cj.query(client, attempt)
    assert (attempt / "result.json").read_bytes() == bound
    assert (attempt / "summary.json").read_bytes() == summary


@pytest.mark.parametrize("has_source", [True, False])
def test_valid_query_records_source_verification_and_terminal_observation(client, attempt, has_source):
    cj.submit(client, attempt)
    result = valid_result(attempt)
    if not has_source:
        del result["files"]
    client.get_submission.return_value = result
    summary = cj.query(client, attempt)
    assert summary["source_verified"] is (True if has_source else None)
    assert summary["all_tests_verified"] is True
    state = cj.read(attempt / "state.json")
    assert state["phase"] == "finished"
    assert state["terminal"] is True
    assert state["last_status"] == "Pass"
    assert state["last_queried_at"]


def test_running_query_records_inflight_observation(client, attempt):
    cj.submit(client, attempt)
    cj.query(client, attempt)
    state = cj.read(attempt / "state.json")
    assert state["phase"] == "submitted"
    assert state["terminal"] is False
    assert state["last_status"] == "Running"
    assert state["last_queried_at"]


def test_rejudge_running_status_clears_finished_observation(client, attempt):
    cj.submit(client, attempt)
    result = valid_result(attempt)
    client.get_submission.return_value = result
    cj.query(client, attempt)
    client.get_submission.return_value = dict(result, status="Running")
    cj.query(client, attempt)
    state = cj.read(attempt / "state.json")
    assert state["phase"] == "submitted"
    assert state["terminal"] is False
    assert state["last_status"] == "Running"


@pytest.mark.parametrize("mutation", ["missing_id", "conflicting_id"])
def test_reconcile_requires_explicit_consistent_response_id(client, attempt, mutation):
    result = unknown_attempt(client, attempt)
    if mutation == "missing_id":
        del result["_id"]
    else:
        result["submissionId"] = "other-id"
    client.get_submission.return_value = result
    with pytest.raises(cj.Error, match="_id/submissionId"):
        cj.reconcile(client, attempt, "candidate-1")
    assert not (attempt / "association.json").exists()


@pytest.mark.parametrize("command", ["info", "download", "rank", "prepare"])
@pytest.mark.parametrize("loaded", [True, False])
def test_public_commands_try_default_session_and_allow_anonymous_get(client, monkeypatch, command, loaded):
    client.load_session.side_effect = None
    client.load_session.return_value = loaded
    client.get_rankings = Mock(return_value=[])
    client.download_package = Mock(return_value="project")
    monkeypatch.setattr(cj, "prepare", Mock(return_value={"phase": "prepared"}))
    monkeypatch.setattr(cj.sdk, "CANNJudgeClient", Mock(return_value=client))
    args = [command, "--problem-id", "problem-1"]
    if command == "prepare":
        args += ["--project-dir", "project", "--attempt-dir", "attempt"]
    assert cj.main(args) == 0
    client.load_session.assert_called_once_with()
    client.session.post.assert_not_called()


@pytest.mark.parametrize("code", [1, -1, 401, "0", None, False])
def test_http_200_business_error_or_unverified_code_with_id_is_not_acknowledged(client, attempt, code):
    body = {"code": code, "data": {"submissionId": "misleading-id"}}
    client.session.post.return_value = Mock(status_code=200, json=lambda: body)
    with pytest.raises(cj.Error):
        cj.submit(client, attempt)
    state = cj.read(attempt / "state.json")
    assert state["phase"] == "unknown"
    assert state["request_identity_confirmed"] is False
    assert "submission_id" not in state
    assert cj.read(attempt / "receipt.json")["response"] == body
    with pytest.raises(cj.Error):
        cj.query(client, attempt)
    client.get_submission.assert_not_called()


def test_business_code_zero_allows_acknowledgement(client, attempt):
    client.session.post.return_value = Mock(status_code=200, json=lambda: {"code": 0, "data": {"submissionId": "submission-1"}})
    assert cj.submit(client, attempt)["request_identity_confirmed"] is True


@pytest.mark.parametrize("status", [401, 403])
def test_only_401_logs_out_after_receipt_is_saved(client, attempt, status):
    client.session.post.return_value = Mock(status_code=status, json=lambda: {"error": "rejected"})
    def logout():
        assert cj.read(attempt / "receipt.json")["http_status"] == 401
        client.user_id = None
    client.logout.side_effect = logout
    with pytest.raises(cj.Error):
        cj.submit(client, attempt)
    if status == 401:
        client.logout.assert_called_once_with()
        assert client.user_id is None
    else:
        client.logout.assert_not_called()
        assert client.user_id == "user-1"
    assert cj.read(attempt / "state.json")["phase"] == "unknown"
    assert client.session.post.call_count == 1


def test_rsa_dependency_is_optional():
    dependencies = (SCRIPT.parent / "requirements.txt").read_text().splitlines()
    required = [line for line in dependencies if line.strip() and not line.lstrip().startswith("#")]
    assert required == ["requests>=2.31,<3"]
