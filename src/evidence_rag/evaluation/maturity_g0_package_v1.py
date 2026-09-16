"""Build and verify a portable G0 engineering evidence package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .maturity_g0_v1 import (
    MATURITY_G0_BASELINE_VERSION,
    _canonical_json_bytes,
    _canonical_sha256,
    build_baseline_inventory,
)

MATURITY_G0_PACKAGE_VERSION = "rag-maturity-g0-package-v1"
_FILES = frozenset(
    {
        "checksums.json",
        "commands.jsonl",
        "environment.json",
        "failures.jsonl",
        "inputs.json",
        "limitations.json",
        "manifest.json",
        "results.json",
        "security.json",
    }
)
_PAYLOAD_FILES = tuple(sorted(_FILES - {"checksums.json", "manifest.json"}))
_COUNT_PATTERNS = {
    "passed": re.compile(r"(?m)(\d+) passed(?:[ ,]|$)"),
    "failed": re.compile(r"(?m)(\d+) failed(?:[ ,]|$)"),
    "errors": re.compile(r"(?m)(\d+) errors?(?:[ ,]|$)"),
    "warnings": re.compile(r"(?m)(\d+) warnings?(?:[ ,]|$)"),
}


class MaturityG0PackageError(RuntimeError):
    """A G0 package could not be executed, published, or verified."""


@dataclass(frozen=True, slots=True)
class G0Command:
    command_id: str
    arguments: tuple[str, ...]
    category: str
    timeout_seconds: int = 1_200


DEFAULT_G0_COMMANDS = (
    G0Command("backend-smoke", ("make", "backend-smoke"), "backend"),
    G0Command("backend-integration", ("make", "backend-integration"), "backend"),
    G0Command("backend-evaluation", ("make", "backend-evaluation"), "evaluation"),
    G0Command("backend-full", ("make", "backend-full"), "backend"),
    G0Command(
        "frontend-typecheck",
        ("npm", "--prefix", "frontend", "run", "typecheck"),
        "frontend",
    ),
    G0Command(
        "frontend-tests",
        ("npm", "--prefix", "frontend", "test", "--", "--run"),
        "frontend",
    ),
)


def _write_json(path: Path, payload: object) -> None:
    path.write_bytes(_canonical_json_bytes(payload) + b"\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_bytes(b"".join(_canonical_json_bytes(row) + b"\n" for row in rows))


def _read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaturityG0PackageError(f"artifact JSON is invalid: {path.name}") from exc
    if not isinstance(payload, dict) or raw != _canonical_json_bytes(payload) + b"\n":
        raise MaturityG0PackageError(f"artifact JSON is not canonical: {path.name}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_bytes().splitlines(keepends=True):
        try:
            payload = json.loads(line)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise MaturityG0PackageError(f"artifact JSONL is invalid: {path.name}") from exc
        if not isinstance(payload, dict) or line != _canonical_json_bytes(payload) + b"\n":
            raise MaturityG0PackageError(f"artifact JSONL is not canonical: {path.name}")
        rows.append(payload)
    return rows


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _tool_version(arguments: tuple[str, ...]) -> str | None:
    try:
        result = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = (result.stdout or result.stderr).strip().splitlines()
    return value[0][:160] if value else None


def _environment() -> dict[str, Any]:
    return {
        "architecture": platform.machine(),
        "git_version": _tool_version(("git", "--version")),
        "node_version": _tool_version(("node", "--version")),
        "npm_version": _tool_version(("npm", "--version")),
        "operating_system": platform.system(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "uv_version": _tool_version(("uv", "--version")),
    }


def _counts(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, pattern in _COUNT_PATTERNS.items():
        matches = pattern.findall(output)
        counts[name] = int(matches[-1]) if matches else 0
    frontend = re.findall(r"(?m)^\s*Tests\s+(\d+) passed", output)
    if frontend:
        counts["passed"] = int(frontend[-1])
    return counts


def _run_command(root: Path, command: G0Command) -> tuple[dict[str, Any], str]:
    started = time.perf_counter()
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["UV_NO_SYNC"] = "1"
    try:
        result = subprocess.run(
            command.arguments,
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=command.timeout_seconds,
        )
        output = (result.stdout or "") + (result.stderr or "")
        exit_code = result.returncode
        error_type = None
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        exit_code = 124
        error_type = "TimeoutExpired"
    except OSError as exc:
        output = ""
        exit_code = 127
        error_type = type(exc).__name__
    duration_ms = round((time.perf_counter() - started) * 1_000, 3)
    record = {
        "arguments": command.arguments,
        "category": command.category,
        "command_id": command.command_id,
        "counts": _counts(output),
        "duration_ms": duration_ms,
        "error_type": error_type,
        "exit_code": exit_code,
        "output_sha256": _sha256_bytes(output.encode("utf-8", errors="replace")),
        "status": "passed" if exit_code == 0 else "failed",
    }
    return record, output


def _version_control_ready(inputs: dict[str, Any]) -> bool:
    worktree = inputs["git"]["worktree"]
    scopes = inputs["scopes"]
    return (
        worktree["conflict_count"] == 0
        and worktree["modified_or_staged_count"] == 0
        and worktree["untracked_count"] == 0
        and all(scope["untracked_file_count"] == 0 for scope in scopes.values())
    )


def _scan_serialized_payloads(directory: Path) -> tuple[str, ...]:
    findings: list[str] = []
    forbidden = (
        str(Path.home()),
        "BEGIN PRIVATE KEY",
        "ghp_",
        "xoxb-",
        "api_key=",
        "password=",
    )
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name in {"checksums.json", "manifest.json"}:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker and marker in text:
                findings.append(f"{path.name}:{marker[:24]}")
    return tuple(findings)


def build_g0_package(
    root: Path,
    output: Path,
    *,
    commands: tuple[G0Command, ...] = DEFAULT_G0_COMMANDS,
) -> dict[str, Any]:
    resolved_root = root.resolve()
    resolved_output = output.resolve()
    if resolved_output.exists():
        raise MaturityG0PackageError("G0 package output must not already exist")
    if resolved_root not in resolved_output.parents:
        raise MaturityG0PackageError("G0 package output must stay inside the repository")
    if len({item.command_id for item in commands}) != len(commands) or not commands:
        raise MaturityG0PackageError("G0 commands must be nonempty with unique identities")
    inputs = build_baseline_inventory(resolved_root)
    formal_database = resolved_root / "var" / "evidence-rag.sqlite3"
    wiki_database = resolved_root / "var" / "wiki" / "wiki.sqlite3"

    def database_state(path: Path) -> tuple[bool, int | None, int | None]:
        if not path.exists():
            return False, None, None
        stat = path.stat()
        return True, stat.st_size, stat.st_mtime_ns

    database_state_before = {
        "formal": database_state(formal_database),
        "wiki": database_state(wiki_database),
    }
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".g0-package-", dir=resolved_output.parent))
    try:
        records: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for command in commands:
            record, _output = _run_command(resolved_root, command)
            records.append(record)
            if record["status"] != "passed":
                failures.append(
                    {
                        "command_id": command.command_id,
                        "error_type": record["error_type"],
                        "exit_code": record["exit_code"],
                        "output_sha256": record["output_sha256"],
                    }
                )
        all_commands_passed = not failures
        version_control_ready = _version_control_ready(inputs)
        g0_qualified = all_commands_passed and version_control_ready
        results = {
            "all_commands_passed": all_commands_passed,
            "command_count": len(records),
            "failed_command_count": len(failures),
            "g0_qualified": g0_qualified,
            "passed_command_count": len(records) - len(failures),
            "version_control_ready": version_control_ready,
        }
        limitations = {
            "ci_observed_on_github": False,
            "database_read_access_instrumented": False,
            "formal_database_coverage_remeasured": False,
            "native_clients_included": False,
            "production_quality_claimed": False,
            "version_control_admission": (
                "passed" if version_control_ready else "clean-checkout-not-proven"
            ),
        }
        _write_json(staging / "environment.json", _environment())
        _write_json(staging / "inputs.json", inputs)
        _write_jsonl(staging / "commands.jsonl", records)
        _write_jsonl(staging / "failures.jsonl", failures)
        _write_json(staging / "results.json", results)
        _write_json(staging / "limitations.json", limitations)
        security_findings = _scan_serialized_payloads(staging)
        database_state_after = {
            "formal": database_state(formal_database),
            "wiki": database_state(wiki_database),
        }
        security = {
            "absolute_user_path_disclosure": False,
            "database_read_access": "not-instrumented",
            "database_state_unchanged": database_state_before == database_state_after,
            "finding_count": len(security_findings),
            "findings": security_findings,
            "network_required_by_verification_commands": False,
        }
        _write_json(staging / "security.json", security)
        checksums = {name: _file_sha256(staging / name) for name in _PAYLOAD_FILES}
        _write_json(staging / "checksums.json", checksums)
        manifest: dict[str, Any] = {
            "artifact_files": tuple(sorted(_FILES)),
            "checksums_sha256": _file_sha256(staging / "checksums.json"),
            "decision": "G0_ENGINEERING_PASS" if g0_qualified else "QUALITY_HOLD",
            "default_engine": "v1",
            "g0_qualified": g0_qualified,
            "inputs_sha256": inputs["content_sha256"],
            "production_authorized": False,
            "schema_version": MATURITY_G0_PACKAGE_VERSION,
            "status": "ENGINEERING_PASS" if all_commands_passed else "FAILED",
        }
        manifest["content_sha256"] = _canonical_sha256(manifest)
        _write_json(staging / "manifest.json", manifest)
        if security_findings:
            raise MaturityG0PackageError("G0 package security scan failed")
        staging.rename(resolved_output)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_g0_package(directory: Path) -> dict[str, Any]:
    resolved = directory.resolve()
    files = frozenset(path.name for path in resolved.iterdir() if path.is_file())
    if files != _FILES:
        raise MaturityG0PackageError("G0 package file set is not exact")
    manifest = _read_json(resolved / "manifest.json")
    checksums = _read_json(resolved / "checksums.json")
    for name in _PAYLOAD_FILES:
        if checksums.get(name) != _file_sha256(resolved / name):
            raise MaturityG0PackageError(f"G0 package checksum mismatch: {name}")
    if manifest.get("checksums_sha256") != _file_sha256(resolved / "checksums.json"):
        raise MaturityG0PackageError("G0 package checksum authority mismatch")
    content_sha256 = manifest.get("content_sha256")
    unsigned = {key: value for key, value in manifest.items() if key != "content_sha256"}
    if content_sha256 != _canonical_sha256(unsigned):
        raise MaturityG0PackageError("G0 package manifest digest mismatch")
    if (
        manifest.get("schema_version") != MATURITY_G0_PACKAGE_VERSION
        or manifest.get("production_authorized") is not False
        or manifest.get("default_engine") != "v1"
    ):
        raise MaturityG0PackageError("G0 package release boundary is invalid")
    inputs = _read_json(resolved / "inputs.json")
    input_digest = inputs.get("content_sha256")
    unsigned_inputs = {key: value for key, value in inputs.items() if key != "content_sha256"}
    if (
        inputs.get("schema_version") != MATURITY_G0_BASELINE_VERSION
        or input_digest != _canonical_sha256(unsigned_inputs)
        or manifest.get("inputs_sha256") != input_digest
    ):
        raise MaturityG0PackageError("G0 package baseline authority is invalid")
    commands = _read_jsonl(resolved / "commands.jsonl")
    failures = _read_jsonl(resolved / "failures.jsonl")
    results = _read_json(resolved / "results.json")
    security = _read_json(resolved / "security.json")
    if (
        results.get("command_count") != len(commands)
        or results.get("failed_command_count") != len(failures)
        or results.get("all_commands_passed") is not all(
            command.get("status") == "passed" and command.get("exit_code") == 0
            for command in commands
        )
        or security.get("database_read_access") != "not-instrumented"
        or security.get("database_state_unchanged") is not True
        or security.get("finding_count") != 0
    ):
        raise MaturityG0PackageError("G0 package results or security contract is invalid")
    if manifest.get("g0_qualified") is not (
        results.get("all_commands_passed") is True
        and results.get("version_control_ready") is True
    ):
        raise MaturityG0PackageError("G0 package qualification decision is inconsistent")
    return {
        "content_sha256": content_sha256,
        "decision": manifest["decision"],
        "g0_qualified": manifest["g0_qualified"],
        "production_authorized": False,
        "schema_version": MATURITY_G0_PACKAGE_VERSION,
        "status": "verified",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("output", type=Path)
    build.add_argument("--root", type=Path, default=Path.cwd())
    verify = subparsers.add_parser("verify")
    verify.add_argument("directory", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    result = (
        build_g0_package(arguments.root, arguments.output)
        if arguments.command == "build"
        else verify_g0_package(arguments.directory)
    )
    print(_canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
