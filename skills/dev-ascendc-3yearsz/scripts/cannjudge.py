#!/usr/bin/env python3
"""POSIX-only CANNJudge attempts. One durable dispatch marker permits at most one POST.

Locks cover one attempt, not an account: coordinate shared-account jobs separately.
The SDK's UTF-8/BOM/newline reading rules apply; comments are never removed.
Prepared snapshots contain exactly the source text destined for the API.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace

if os.name != "posix":
    raise SystemExit("Persistent attempts require POSIX fcntl and directory fsync.")
import fcntl

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor" / "cannjudge_submit"))
import cannjudge_cli as sdk
from project_files import relative_path

Error = sdk.CANNJudgeError
PASS = {"Pass", "Accepted"}
HIDDEN = {"Hidden", "Withheld"}
TERMINAL = PASS | {"Fail", "WA", "RE", "CE", "TLE", "MLE", "SystemError",
                   "Wrong Answer", "Runtime Error", "Compile Error",
                   "Time Limit Exceeded", "Memory Limit Exceeded", "System Error"}
REGISTRY = {"tiling_h", "tiling_key_h", "host_cpp", "kernel_cpp"}


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sync_dir(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(destination, data):
    destination = Path(destination)
    if destination.is_symlink():
        raise Error("Evidence files must not be symbolic links")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        sync_dir(destination.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save(destination, value):
    atomic_write(destination, encoded(value))


def read(destination):
    return json.loads(Path(destination).read_bytes())


@contextmanager
def locked(directory, create=False):
    directory = Path(directory).expanduser().absolute()
    if directory.is_symlink():
        raise Error("Attempt directory must not be a symbolic link")
    if create:
        directory.mkdir(parents=True, exist_ok=True)
        sync_dir(directory.parent)
    fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Error("Another process is using this attempt") from None
        yield directory
    finally:
        os.close(fd)


def sources(payload):
    if "files" not in payload:
        if not REGISTRY.issubset(payload) or any(not isinstance(payload[k], str) for k in REGISTRY):
            raise Error("Complete registry source is required")
        return {key: payload[key] for key in sorted(REGISTRY)}
    files = payload["files"]
    if not isinstance(files, list) or not files:
        raise Error("Complete files source is required")
    result = {}
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            raise Error("Submission source is missing")
        name = relative_path(item.get("path"))
        if name in result:
            raise Error("Duplicate submission source path")
        result[name] = item["content"]
    return result


def verify_snapshot(directory, state):
    raw = (directory / "prepared-payload.json").read_bytes()
    if sha(raw) != state["prepared_payload_sha256"]:
        raise Error("Prepared payload hash mismatch")
    payload = json.loads(raw)
    if payload.get("problemId") != state["problem_id"] or payload.get("userId") is not None:
        raise Error("Prepared payload identity mismatch")
    source_map = sources(payload)
    hashes = {name: sha(content.encode("utf-8")) for name, content in source_map.items()}
    if hashes != state["source_sha256"]:
        raise Error("Source manifest mismatch")
    for name, digest in hashes.items():
        if sha((directory / "sources" / relative_path(name)).read_bytes()) != digest:
            raise Error("Frozen source hash mismatch")
    for name in ("problem.json", "template.json"):
        if sha((directory / name).read_bytes()) != state["metadata_sha256"][name]:
            raise Error("Frozen problem/template hash mismatch")
    return payload


def final_payload(directory, state):
    prepared = verify_snapshot(directory, state)
    raw = (directory / "payload.json").read_bytes()
    if sha(raw) != state.get("payload_sha256"):
        raise Error("Dispatched payload hash mismatch")
    payload = json.loads(raw)
    if not state.get("user_id") or payload != dict(prepared, userId=state["user_id"]):
        raise Error("Dispatched payload/source identity mismatch")
    return payload


def prepare(client, selector, project_dir, attempt_dir, expected_testcases=None):
    if expected_testcases is not None and expected_testcases <= 0:
        raise Error("expected-testcases must be positive")
    with locked(attempt_dir, create=True) as directory:
        if any(p.name != ".lock" for p in directory.iterdir()):
            raise Error("Use a new empty attempt directory")
        problem = client.resolve_problem(selector)
        problem_id = problem["_id"]
        template = client.get_template(problem_id)
        # The SDK packages against the same metadata that is frozen below.
        frozen = SimpleNamespace(user_id=None, get_problem=lambda _: problem,
                                 get_template=lambda _: template)
        payload = sdk.CANNJudgeClient.prepare_submission(frozen, problem_id, project_dir)
        source_map = sources(payload)
        artifacts = {"prepared-payload.json": encoded(payload), "problem.json": encoded(problem),
                     "template.json": encoded(template)}
        for name, raw in artifacts.items():
            atomic_write(directory / name, raw)
        for name, content in source_map.items():
            atomic_write(directory / "sources" / name, content.encode("utf-8"))
        sync_dir(directory)
        state = {"version": 1, "phase": "prepared", "created_at": now(), "problem_id": problem_id,
                 "expected_testcases": expected_testcases, "request_identity_confirmed": False,
                 "prepared_payload_sha256": sha(artifacts["prepared-payload.json"]),
                 "metadata_sha256": {k: sha(v) for k, v in artifacts.items() if k != "prepared-payload.json"},
                 "source_sha256": {k: sha(v.encode("utf-8")) for k, v in source_map.items()}}
        save(directory / "state.json", state)
        return state


def authenticate(client, owner=None):
    if not client.load_session() or not client.user_id:
        raise Error("Run login in your own interactive terminal first")
    if owner is not None and owner != client.user_id:
        raise Error("Saved attempt belongs to a different account")


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise Error("A valid submission ID is required")
    return value


def receipt_id(receipt):
    body = receipt.get("response")
    if not 200 <= receipt.get("http_status", 0) < 300 or not isinstance(body, dict):
        return None
    if "code" in body and (type(body["code"]) not in (int, float) or body["code"] != 0):
        return None
    data = body.get("data")
    candidate = data.get("submissionId") if isinstance(data, dict) else None
    return candidate if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", candidate) else None


def submit(client, attempt_dir):
    with locked(attempt_dir) as directory:
        state = read(directory / "state.json")
        if state["phase"] != "prepared" or (directory / "dispatch.json").exists():
            raise Error("This attempt cannot POST again; use resume or reconcile")
        payload = verify_snapshot(directory, state)
        authenticate(client)
        payload["userId"] = client.user_id
        raw = encoded(payload)
        atomic_write(directory / "payload.json", raw)
        state.update(phase="dispatching", user_id=client.user_id, payload_sha256=sha(raw),
                     dispatch_started_at=now())
        # O_EXCL provides a second, irreversible local guard even if state persistence fails.
        fd = os.open(directory / "dispatch.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded(state))
            stream.flush()
            os.fsync(stream.fileno())
        sync_dir(directory)
        save(directory / "state.json", state)
        submission_id = None
        try:
            # No redirects or retry adapter: exactly one application-level POST.
            response = client.session.post(client.BASE_URL + "/api/submissions/submit", data=raw,
                                           headers={"Content-Type": "application/json"},
                                           timeout=30, allow_redirects=False)
            receipt = {"received_at": now(), "http_status": response.status_code,
                       "payload_sha256": state["payload_sha256"]}
            try:
                receipt["response"] = response.json()
            except ValueError:
                receipt["response_text"] = response.text
            submission_id = receipt_id(receipt)
            if submission_id:
                receipt["submission_id"] = submission_id
                state.update(submission_id=submission_id, request_identity_confirmed=True)
            save(directory / "receipt.json", receipt)
            if response.status_code == 401:
                client.logout()
            if not submission_id:
                raise Error("POST returned no confirmed ID; receipt saved; do not resubmit")
            state.update(phase="submitted", acknowledged_at=now())
            save(directory / "state.json", state)
            return state
        except BaseException as exc:
            state.update(phase="unknown", last_error_type=type(exc).__name__)
            try:
                save(directory / "state.json", state)
            except OSError:
                pass  # The already durable dispatch marker still prevents another POST.
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            suffix = f" Known ID: {submission_id}." if submission_id else ""
            raise Error("POST outcome/evidence is incomplete; use resume or reconcile; never retry this attempt." + suffix) from None


def summarize(result, state):
    status = result.get("status")
    returned = result.get("result")
    returned = returned if isinstance(returned, list) else []
    cases = []
    for index, item in enumerate(returned):
        item = item if isinstance(item, dict) else {}
        case_status = item.get("testcase_status")
        passed = isinstance(case_status, str) and case_status in PASS
        visible = isinstance(case_status, str) and case_status in TERMINAL
        hidden = isinstance(case_status, str) and case_status in HIDDEN
        value = item.get("time")
        cases.append({"index": index + 1, "testcase_id": item.get("testcase_id"),
                      "raw_status": case_status, "passed": passed, "visible": visible, "hidden": hidden,
                      "precision_ratio": item.get("precision_ratio"),
                      "time_raw": value, "time_unit_raw": item.get("time_unit", item.get("timeUnit", item.get("unit"))),
                      "timing_eligible": passed and isinstance(value, (int, float)) and not isinstance(value, bool)
                      and math.isfinite(value) and value > 0})
    passed_count = sum(item["passed"] for item in cases)
    visible_count = sum(item["visible"] for item in cases)
    hidden_count = sum(item["hidden"] for item in cases)
    ids = [str(item["testcase_id"]) for item in cases
           if type(item["testcase_id"]) in (str, int) and str(item["testcase_id"]).strip()]
    distinct = False if len(set(ids)) != len(ids) else (True if len(ids) == len(cases) and ids else None)
    expected = state.get("expected_testcases")
    return {"submission_id": state.get("submission_id"), "raw_status": status,
            "terminal": isinstance(status, str) and status in TERMINAL,
            "returned_testcase_count": len(cases), "visible_testcase_count": visible_count,
            "visible_passed_count": passed_count, "failed_count": visible_count - passed_count,
            "hidden_testcase_count": hidden_count, "unknown_testcase_count": len(cases) - visible_count - hidden_count,
            "expected_testcase_count": expected, "distinct_testcases_verified": distinct,
            "all_tests_verified": isinstance(status, str) and status in PASS and expected is not None
            and passed_count == len(cases) == expected and distinct is True,
            "request_identity_confirmed": state.get("request_identity_confirmed", False),
            "source_verified": state.get("source_verified"),
            "score_raw": result.get("score"), "testcases": cases}


def recover_id(directory, state):
    if state.get("submission_id"):
        return valid_id(state["submission_id"])
    if (directory / "receipt.json").is_file():
        receipt = read(directory / "receipt.json")
        if receipt.get("payload_sha256") == state.get("payload_sha256") and receipt.get("submission_id"):
            submission_id = valid_id(receipt["submission_id"])
            if receipt_id(receipt) != submission_id:
                raise Error("Receipt ID mismatch")
            state.update(submission_id=submission_id, request_identity_confirmed=True)
            save(directory / "state.json", state)
            return submission_id
    raise Error("No confirmed ID is saved; reconcile a candidate ID; never repeat POST")


def query(client, attempt_dir):
    with locked(attempt_dir) as directory:
        state = read(directory / "state.json")
        payload = final_payload(directory, state)
        submission_id = recover_id(directory, state)
        authenticate(client, state["user_id"])
        result = client.get_submission(submission_id)
        save(directory / "last-query-result.json", result)
        source_verified = verify_result(result, state, payload, submission_id)
        summary = summarize(result, dict(state, source_verified=source_verified))
        save(directory / "result.json", result)
        save(directory / "summary.json", summary)
        state.update(last_queried_at=now(), last_status=summary["raw_status"], terminal=summary["terminal"])
        if summary["terminal"] and state.get("request_identity_confirmed"):
            state["phase"] = "finished"
        elif state["phase"] == "finished":
            state["phase"] = "submitted"  # A rejudge may expose a nonterminal status again.
        save(directory / "state.json", state)
        return summary


def require_identity(result, keys, expected):
    values = [result[key] for key in keys if key in result]
    values = [v.get("_id") if isinstance(v, dict) else v for v in values]
    if not values or any(v != expected for v in values):
        raise Error("Candidate result has missing or mismatched " + "/".join(keys))


def verify_result(result, state, payload, submission_id, require_source=False):
    if not isinstance(result, dict):
        raise Error("Unexpected result format")
    require_identity(result, ("_id", "submissionId"), submission_id)
    require_identity(result, ("problemId", "problem_id"), state["problem_id"])
    require_identity(result, ("userId", "user_id"), state["user_id"])
    if "files" in payload:
        remote = {"files": result.get("files")}
        present = remote["files"] is not None and remote["files"] != []
    else:
        remote = {key: result[key] for key in REGISTRY if key in result}
        present = any(value not in (None, "") for value in remote.values())
    if not present:
        if require_source:
            raise Error("Candidate submission source is missing")
        return None
    if sources(remote) != sources(payload):
        raise Error("Candidate submission source does not match every frozen source byte")
    return True


def reconcile(client, attempt_dir, submission_id):
    submission_id = valid_id(submission_id)
    with locked(attempt_dir) as directory:
        state = read(directory / "state.json")
        payload = final_payload(directory, state)
        authenticate(client, state["user_id"])
        result = client.get_submission(submission_id)
        verify_result(result, state, payload, submission_id, require_source=True)
        associated = dict(state, submission_id=submission_id, request_identity_confirmed=False, source_verified=True)
        evidence_dir = directory / "associations" / submission_id
        save(evidence_dir / "result.json", result)
        save(evidence_dir / "summary.json", summarize(result, associated))
        association = {"submission_id": submission_id, "associated_at": now(), "problem_id": state["problem_id"],
                       "user_id": state["user_id"], "payload_sha256": state["payload_sha256"],
                       "source_match": True, "request_identity_confirmed": False,
                       "original_phase": state["phase"],
                       "reason": "Identical source and owner cannot prove the identity of the original POST."}
        save(directory / "association.json", association)
        return association


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-file", help="Session path; optional authentication for public reads")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("info", "download", "rank", "prepare"):
        command = sub.add_parser(name)
        selector = command.add_mutually_exclusive_group(required=True)
        selector.add_argument("--problem-url")
        selector.add_argument("--problem-id")
        if name == "download":
            command.add_argument("--output")
        if name == "prepare":
            command.add_argument("--project-dir", required=True)
            command.add_argument("--attempt-dir", required=True)
            command.add_argument("--expected-testcases", type=int)
    for name in ("submit", "query", "resume", "reconcile"):
        command = sub.add_parser(name)
        command.add_argument("--attempt-dir", required=True)
        if name == "reconcile":
            command.add_argument("--submission-id", required=True)
    # Delegate the supported upstream login flags without exposing its unsafe submit path.
    login = sub.add_parser("login", add_help=False)
    login.add_argument("login_args", nargs=argparse.REMAINDER)
    probe = argv[2:] if len(argv) >= 2 and argv[0] == "--session-file" else argv
    if probe and probe[0].startswith("--session-file="):
        probe = probe[1:]
    if probe and probe[0] == "login":
        old_argv = sys.argv
        try:
            sys.argv = [str(Path(sdk.__file__)), *argv]
            return sdk.main()
        finally:
            sys.argv = old_argv
    args = parser.parse_args(argv)
    try:
        client = sdk.CANNJudgeClient(session_file=args.session_file)
        if args.command in {"info", "download", "rank", "prepare"}:
            client.load_session()  # Missing session is allowed; authenticated/public GET share the same flow.
            selector = args.problem_url or args.problem_id
            if args.command == "prepare":
                result = prepare(client, selector, args.project_dir, args.attempt_dir, args.expected_testcases)
            else:
                problem = client.resolve_problem(selector)
                if args.command == "info":
                    result = problem
                elif args.command == "rank":
                    result = client.get_rankings(problem["_id"])  # Keep the API ordering and all rows.
                else:
                    result = {"project_dir": client.download_package(problem["_id"], args.output, include_metadata=True)}
        elif args.command == "submit":
            result = submit(client, args.attempt_dir)
        elif args.command == "reconcile":
            result = reconcile(client, args.attempt_dir, args.submission_id)
        else:
            result = query(client, args.attempt_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (Error, ValueError, OSError, sdk.requests.RequestException) as exc:
        message = str(exc) if isinstance(exc, Error) else type(exc).__name__
        print("Error: " + message, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled. A dispatched attempt must only be resumed or reconciled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
