#!/usr/bin/env python3
"""Check declared handoff consistency against current files; never execute or POST.

This is not a permission enforcer, evidence authenticator, or NPU correctness
proof. Inputs must be frozen: implementers write candidates to separate files.
Readiness means meeting this handoff's pass gate; valid failures remain usable.
Exit 0: consistent and ready; 1: inconsistent or not ready; 2: JSON/schema error.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


ROLES = {"plan", "implement", "review", "tune", "judge"}
ACTIONS = {"read", "local_write", "local_test", "judge_get", "judge_post"}
VERDICTS = {"PASS", "FAIL", "CONCERN", "NOT_APPLICABLE"}
CHECK_STATES = {"passed", "failed", "skipped", "not_run"}


def report_base():
    return {"scope": "declared_handoff_consistency",
            "evidence_authenticity_verified": False,
            "structurally_valid": False, "ready_for_next_step": False,
            "eligible": False, "errors": [], "issues": [], "format_errors": []}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def string(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label} must be a nonempty string")


def absolute(value, label):
    string(value, label)
    require("\x00" not in value and Path(value).is_absolute(), f"{label} must be an absolute path")


def array(value, label):
    require(isinstance(value, list), f"{label} must be an array")


def enum(value, allowed, label):
    require(isinstance(value, str) and value in allowed, f"{label} must be one of {sorted(allowed)}")


def sources(value, label):
    array(value, label)
    for item in value:
        require(isinstance(item, dict), f"{label} entries must be objects")
        absolute(item.get("path"), f"{label}.path")
        sha = item.get("sha256")
        require(isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{64}", sha) is not None,
                f"{label}.sha256 must be a SHA-256 hex digest")


def validate(value, label):
    require(isinstance(value, dict), f"{label} must be an object")
    require(type(value.get("schema_version")) is int and value["schema_version"] == 1,
            f"{label}.schema_version must be integer 1")
    string(value.get("task_id"), f"{label}.task_id")
    enum(value.get("role"), ROLES, f"{label}.role")
    sources(value.get("input_sources"), f"{label}.input_sources")
    if label == "task":
        require(type(value.get("allow_not_applicable", False)) is bool,
                "task.allow_not_applicable must be a boolean")
        string(value.get("objective"), "task.objective")
        absolute(value.get("output_path"), "task.output_path")
        array(value.get("write_scope"), "task.write_scope")
        for item in value["write_scope"]:
            require(isinstance(item, dict), "write_scope entries must be objects")
            absolute(item.get("path"), "write_scope.path")
            enum(item.get("kind"), {"file", "directory"}, "write_scope.kind")
        array(value.get("required_artifacts"), "task.required_artifacts")
        for path in value["required_artifacts"]:
            absolute(path, "required_artifacts entry")
        action_key = "authorized_actions"
    else:
        enum(value.get("status"), {"completed", "incomplete"}, "result.status")
        enum(value.get("verdict"), VERDICTS, "result.verdict")
        require(isinstance(value.get("not_applicable_reason", ""), str),
                "result.not_applicable_reason must be a string")
        sources(value.get("artifacts"), "result.artifacts")
        array(value.get("checks"), "result.checks")
        for check in value["checks"]:
            require(isinstance(check, dict), "checks entries must be objects")
            string(check.get("name"), "check.name")
            enum(check.get("status"), CHECK_STATES, "check.status")
            array(check.get("evidence"), "check.evidence")
            for path in check["evidence"]:
                absolute(path, "check.evidence entry")
        array(value.get("unresolved"), "result.unresolved")
        for item in value["unresolved"]:
            string(item, "unresolved entry")
        array(value.get("limitations", []), "result.limitations")
        for item in value.get("limitations", []):
            string(item, "limitations entry")
        array(value.get("inflight_requests"), "result.inflight_requests")
        require(all(isinstance(item, dict) for item in value["inflight_requests"]),
                "inflight_requests entries must be JSON objects")
        action_key = "actions_taken"
    array(value.get(action_key), f"{label}.{action_key}")
    for action in value[action_key]:
        enum(action, ACTIONS, f"{label}.{action_key} entry")


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, f"Duplicate JSON key: {key}")
        value[key] = item
    return value


def invalid_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def load_record(path):
    return json.loads(path.read_bytes(), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)


def verify(task_path, result_path):
    report = report_base()
    errors, issues = report["errors"], report["issues"]
    try:
        task, result = load_record(Path(task_path)), load_record(Path(result_path))
        validate(task, "task")
        validate(result, "result")
    except (ValueError, UnicodeError) as exc:
        report["format_errors"].append(str(exc))
        return report
    except OSError as exc:
        errors.append(f"Cannot read handoff record: {exc}")
        return report
    report.update({key: result[key] for key in
                   ("task_id", "role", "status", "verdict", "unresolved", "inflight_requests")})
    report["limitations"] = result.get("limitations", [])
    report["not_applicable_reason"] = result.get("not_applicable_reason")

    def resolved(path):
        return Path(path).resolve()

    def in_scope(path):
        path = resolved(path)
        for scope in task["write_scope"]:
            root = resolved(scope["path"])
            if path == root or (scope["kind"] == "directory" and root in path.parents):
                return True
        return False

    def check_hash(item, label):
        path = Path(item["path"])
        try:
            if not path.is_file():
                errors.append(f"{label} is not an existing file: {path}")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"].lower():
                errors.append(f"{label} SHA differs from current raw bytes: {path}")
        except OSError as exc:
            errors.append(f"Cannot read {label}: {path}: {exc}")

    def indexed(items, label):
        paths = [item["path"] for item in items]
        if len(paths) != len(set(paths)):
            errors.append(f"Duplicate paths in {label}")
        return {item["path"]: item["sha256"].lower() for item in items}

    try:
        for key in ("task_id", "role"):
            if task[key] != result[key]:
                errors.append(f"task/result {key} mismatch")
        if resolved(result_path) != resolved(task["output_path"]):
            errors.append("Result file is not task.output_path")
        expected = indexed(task["input_sources"], "task.input_sources")
        actual = indexed(result["input_sources"], "result.input_sources")
        if actual != expected:
            errors.append("result.input_sources must match the complete task path/SHA list")
        for item in task["input_sources"]:
            check_hash(item, "Input source")
        artifacts = indexed(result["artifacts"], "result.artifacts")
        for item in result["artifacts"]:
            check_hash(item, "Artifact")
        for path in [str(result_path), task["output_path"], *artifacts]:
            if not in_scope(path):
                errors.append(f"Output outside write_scope: {path}")
        for path in task["required_artifacts"]:
            if path not in artifacts:
                errors.append(f"Missing required artifact: {path}")
        for source in expected:
            outputs = [str(result_path)] + (list(artifacts) if task["role"] == "implement" else [])
            for path in outputs:
                if (resolved(path) == resolved(source) or
                        (Path(path).exists() and Path(source).exists() and Path(path).samefile(source))):
                    errors.append(f"Output must be separate from frozen input: {path}")
        taken, allowed = set(result["actions_taken"]), set(task["authorized_actions"])
        if taken - allowed:
            errors.append(f"Actions exceed authorization: {sorted(taken - allowed)}")
        if task["role"] != "judge" and (taken | allowed) & {"judge_get", "judge_post"}:
            errors.append("Only the judge role may declare judge_get/judge_post")
        names = [check["name"] for check in result["checks"]]
        if len(names) != len(set(names)):
            errors.append("Duplicate check names")
        for check in result["checks"]:
            if check["status"] == "passed" and not check["evidence"]:
                errors.append(f"Passed check requires evidence: {check['name']}")
            for path in check["evidence"]:
                if not Path(path).is_file():
                    errors.append(f"Check evidence is not an existing file: {path}")
        if result["verdict"] == "PASS" and (not result["checks"] or
                any(check["status"] != "passed" for check in result["checks"])):
            errors.append("PASS requires at least one passed check and no failed/skipped/not_run checks")
        if result["verdict"] == "NOT_APPLICABLE":
            if not task.get("allow_not_applicable", False):
                errors.append("NOT_APPLICABLE requires task.allow_not_applicable=true")
            if not result.get("not_applicable_reason", "").strip():
                errors.append("NOT_APPLICABLE requires a nonempty not_applicable_reason")
            if any(check["status"] == "failed" for check in result["checks"]):
                errors.append("NOT_APPLICABLE cannot contain failed checks")
        if result["status"] == "completed" and result["unresolved"]:
            errors.append("Completed result must have no unresolved items")
    except (OSError, RuntimeError) as exc:
        errors.append(f"Cannot inspect handoff paths: {exc}")
    if result["status"] == "incomplete":
        issues.append("Result is incomplete")
    if result["verdict"] in {"FAIL", "CONCERN"}:
        issues.append(f"Verdict {result['verdict']} is recorded; it does not meet the declared pass gate")
    if result["unresolved"]:
        issues.append("Result contains unresolved items")
    if result["inflight_requests"]:
        issues.append("In-flight requests are declared; their live state is not verified")
    report["structurally_valid"] = not errors
    report["ready_for_next_step"] = (not errors and result["status"] == "completed" and
                                    result["verdict"] in {"PASS", "NOT_APPLICABLE"} and not result["unresolved"])
    report["eligible"] = report["ready_for_next_step"]
    return report


class Parser(argparse.ArgumentParser):
    def error(self, message):
        report = report_base()
        report.update(format_errors=[message], exit_code=2)
        print(json.dumps(report))
        raise SystemExit(2)


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args(argv)
    report = verify(args.task, args.result)
    code = 2 if report["format_errors"] else 0 if report["ready_for_next_step"] else 1
    report["exit_code"] = code
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
