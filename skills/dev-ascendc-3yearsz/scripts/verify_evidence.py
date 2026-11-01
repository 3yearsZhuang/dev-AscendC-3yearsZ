#!/usr/bin/env python3
"""Offline consistency checks for source/verified-result evidence; never submits.

Relative paths inside a record resolve under --repo-root, or its directory.
Exit codes: 0 consistent, 1 inconsistent/incomplete, 2 malformed input.
A source_transform explicitly binds snapshot/current hashes using input_sha256,
output_sha256 and method. Its output is the submitted source; this checks the
binding, not the correctness or semantics of the transformation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys


PASS = {"pass", "accepted", "ac"}
FAIL = {"fail", "failed", "wrong answer", "wa", "runtime error", "re", "compile error", "ce",
        "compilation error", "time limit exceeded", "tle", "memory limit exceeded",
        "mle", "output limit exceeded", "presentation error", "system error"}
HIDDEN = {"hidden", "withheld"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def valid_sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def normalized_status(value):
    return value.strip().lower() if isinstance(value, str) else ""


def same_status(left, right):
    a, b = normalized_status(left), normalized_status(right)
    return a == b or (a in PASS and b in PASS)


def verify(source, record_path, repo_root=None):
    report = {"scope": "offline_evidence_consistency", "errors": [], "unverified": [],
              "format_errors": [], "server_source_verified": None, "notes": []}
    def read(path):
        try:
            return path.read_bytes()
        except OSError as exc:
            report["unverified"].append(f"Cannot read {path}: {exc.strerror}")
            return None
    def object_from(path):
        data = read(path)
        if data is None:
            return None
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object in {path}")
        return value
    def evidence_path(value):
        if not isinstance(value, str) or not value:
            raise ValueError("Evidence paths must be nonempty strings")
        path = Path(value)
        return path if path.is_absolute() else base / path
    try:
        base = Path(repo_root) if repo_root else record_path.parent
        record = object_from(record_path)
        raw = read(source)
        if record is None or raw is None:
            return report
        expected = record.get("source_sha256")
        if not valid_sha(expected):
            raise ValueError("record.source_sha256 must be a lowercase SHA-256 hex digest")
        actual = digest(raw)
        report.update(source_sha256=actual, record_source_sha256=expected)
        if actual != expected:
            report["errors"].append("Current raw source SHA differs from record.source_sha256")
        submitted_sha = expected
        if "source_snapshot" in record:
            snapshot = read(evidence_path(record["source_snapshot"]))
            if snapshot is not None:
                snapshot_sha = digest(snapshot)
                report["snapshot_sha256"] = snapshot_sha
                if snapshot_sha != expected:
                    transform = record.get("source_transform")
                    if (isinstance(transform, dict) and isinstance(transform.get("method"), str)
                            and transform["method"].strip()
                            and transform.get("input_sha256") == snapshot_sha
                            and transform.get("output_sha256") == expected):
                        submitted_sha = transform["output_sha256"]
                        report["semantic_verification"] = "not_performed"
                        report["notes"].append("Explicit source_transform hashes match; transformation semantics were not checked")
                    else:
                        report["errors"].append("Snapshot SHA differs without an explicit matching source_transform binding")
        report["submitted_source_sha256"] = submitted_sha
        if not isinstance(record.get("submission_id"), str) or not record["submission_id"]:
            raise ValueError("record.submission_id must be a nonempty string")
        if not record.get("result"):
            report["unverified"].append("No raw result path is recorded")
            return report
        result = object_from(evidence_path(record["result"]))
        if result is None:
            return report
        if result.get("code", 0) != 0:
            report["errors"].append("Raw result contains a nonzero API response code")
        body = result.get("data", result)
        if not isinstance(body, dict):
            raise ValueError("Raw result data must be an object")
        ids = [body[k] for k in ("_id", "submissionId", "submission_id") if k in body]
        report["submission_id"] = record["submission_id"]
        if not ids:
            report["unverified"].append("Raw result has no submission ID")
        elif any(value != record["submission_id"] for value in ids):
            report["errors"].append("Raw result submission ID differs from the record")
        status = body.get("status")
        report["raw_status"] = status
        if normalized_status(status) not in PASS | FAIL:
            report["unverified"].append(f"Raw status is not a recognized terminal status: {status!r}")
        if "status" in record and not same_status(record["status"], status):
            report["errors"].append("Recorded status differs from raw result status")
        rows = next((body[k] for k in ("testcases", "results", "result") if k in body), None)
        if rows is None:
            report["unverified"].append("Raw result contains no testcase rows")
        elif not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Raw testcase rows must be a list of objects")
        else:
            statuses = [normalized_status(row.get("testcase_status", row.get("status"))) for row in rows]
            passed = sum(s in PASS for s in statuses)
            hidden = sum(s in HIDDEN for s in statuses)
            failed = sum(s in FAIL for s in statuses)
            unknown = len(rows) - passed - hidden - failed
            ids = [row.get("testcase_id") for row in rows if isinstance(row.get("testcase_id"), str)]
            if len(ids) != len(set(ids)):
                report["errors"].append("Duplicate testcase IDs cannot establish distinct coverage")
            report.update(observed_testcases=len(rows), visible_passed_testcases=passed,
                          failed_testcases=failed, hidden_testcases=hidden,
                          unknown_testcases=unknown, observed_statuses_complete=bool(rows) and not hidden and not unknown)
            if not rows or unknown:
                report["unverified"].append("Empty or unknown testcase statuses prevent complete coverage verification")
            if hidden:
                report["notes"].append("Hidden testcases are not counted as visible passes")
            claim = record.get("passed_testcases")
            if claim is not None:
                if isinstance(claim, bool) or not isinstance(claim, int) or claim < 0:
                    raise ValueError("record.passed_testcases must be a nonnegative integer")
                if claim != passed:
                    report["errors"].append(f"Recorded passed_testcases={claim} differs from {passed} observable passes")
            if normalized_status(status) in PASS and failed:
                report["errors"].append("Overall Pass conflicts with failing testcase rows")
        sources = []
        files = body.get("files", [])
        if not isinstance(files, list):
            raise ValueError("Raw result files must be a list")
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("Raw result files entries must be objects")
            if item.get("path") == "kernel.asc" or item.get("key") == "kernel.asc":
                if not isinstance(item.get("content"), str):
                    raise ValueError("Server kernel.asc content must be a string")
                sources.append(digest(item["content"].encode("utf-8")))
        kernel_cpp = body.get("kernel_cpp")
        if kernel_cpp:
            if not isinstance(kernel_cpp, str):
                raise ValueError("Raw kernel_cpp must be a string")
            sources.append(digest(kernel_cpp.encode("utf-8")))
        if sources:
            report["server_source_verified"] = all(sha == submitted_sha for sha in sources)
            report["server_source_sha256"] = sources
            if not report["server_source_verified"]:
                report["errors"].append("Server source bytes differ from the bound submitted source")
        elif record.get("source_matches_server") is True:
            report["unverified"].append("Record claims source_matches_server but the raw result contains no source")
    except (ValueError, TypeError) as exc:
        report["format_errors"].append(str(exc))
    return report


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({"format_errors": [message], "exit_code": 2}))
        raise SystemExit(2)


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args(argv)
    report = verify(args.source, args.record, args.repo_root)
    code = 2 if report["format_errors"] else 1 if report["errors"] or report["unverified"] else 0
    report.update(exit_code=code, consistent=code == 0)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
