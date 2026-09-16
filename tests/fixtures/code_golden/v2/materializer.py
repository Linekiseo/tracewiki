from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DATASET_ID = "code-golden"
DATASET_VERSION = "code-golden-v2"
FIXED_PROJECT_HEAD = "bc3326edc761e3bdb42ed78726a21f314ab44974"
PROJECT_ID = "project-code-golden-v2"
ACL_REF = "acl://code-golden-v2/public"
PROJECT_SNAPSHOT_PATHS = (
    "src/evidence_rag/code_history/service.py",
    "src/evidence_rag/ingestion.py",
    "src/evidence_rag/repository.py",
)

REMOTE_URLS = {
    "current_project_snapshot": (
        "https://fixtures.invalid/code-golden/current-project-snapshot.git"
    ),
    "controlled_multilingual": ("https://fixtures.invalid/code-golden/controlled-multilingual.git"),
    "historical_error": "https://fixtures.invalid/code-golden/historical-error.git",
}
REPOSITORY_IDS = {
    source_class: "repo://" + url.removeprefix("https://").removesuffix(".git")
    for source_class, url in REMOTE_URLS.items()
}

_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Code Golden Fixture",
    "GIT_AUTHOR_EMAIL": "code-golden@fixtures.invalid",
    "GIT_COMMITTER_NAME": "Code Golden Fixture",
    "GIT_COMMITTER_EMAIL": "code-golden@fixtures.invalid",
    "TZ": "UTC",
}

CONTROLLED_FILES = {
    "src/checkout/__init__.py": "",
    "src/checkout/pricing.py": """\
from __future__ import annotations

from .tax import calculate_tax


def format_line(label: str, cents: int) -> str:
    return f"{label}: {cents / 100:.2f}"


def apply_discount(cents: int, customer_tier: str) -> int:
    if customer_tier == "gold":
        return cents * 90 // 100
    return cents


def calculate_total(subtotal_cents: int, customer_tier: str) -> int:
    discounted = apply_discount(subtotal_cents, customer_tier)
    return discounted + calculate_tax(discounted)


class PriceEngine:
    def quote(self, subtotal_cents: int, customer_tier: str) -> str:
        total = calculate_total(subtotal_cents, customer_tier)
        return format_line("total", total)
""",
    "src/checkout/tax.py": """\
def calculate_tax(cents: int) -> int:
    return cents * 8 // 100


def format_line(cents: int) -> str:
    return f"tax={cents}"
""",
    "src/payments/gateway.py": """\
from typing import overload


@overload
def charge(amount: int, currency: str) -> str: ...


@overload
def charge(amount: float, currency: str) -> str: ...


def charge(amount: int | float, currency: str) -> str:
    return f"{currency}:{amount}"
""",
    "tests/pricing_cases.py": """\
from src.checkout.pricing import calculate_total


def test_gold_customer_discount() -> None:
    assert calculate_total(1000, "gold") == 972


def test_regular_customer_total() -> None:
    assert calculate_total(1000, "regular") == 1080
""",
    "web/checkout.ts": """\
import { formatMoney as displayMoney } from "./money";

export function renderCheckout(cents: number): string {
  return displayMoney(cents);
}
""",
    "web/money.ts": """\
export function formatMoney(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}
""",
    "web/admin.js": """\
export function formatLine(label, cents) {
  return `${label}:${cents}`;
}
""",
    "generated/client.ts": """\
export function generatedCharge(): string {
  return "generated";
}
""",
    "vendor/payment_sdk.js": """\
export function charge() {
  return "vendor";
}
""",
}

HISTORY_STATE_1 = {
    "src/checkout/__init__.py": "",
    "src/checkout/legacy_total.py": """\
def legacy_round_total(cents: int) -> int:
    return ((cents + 4) // 5) * 5


def calculate_total(subtotal_cents: int) -> int:
    return legacy_round_total(subtotal_cents)
""",
    "validation/check_status.py": """\
raise SystemExit(0)
""",
}

HISTORY_STATE_2 = {
    "src/checkout/__init__.py": "",
    "src/checkout/totals.py": """\
def round_total(cents: int) -> int:
    return ((cents + 4) // 5) * 5


def calculate_total(subtotal_cents: int) -> int:
    return round_total(subtotal_cents)
""",
    "validation/check_status.py": """\
raise SystemExit(1)
""",
}

HISTORY_STATE_3 = {
    "src/checkout/__init__.py": "",
    "src/checkout/pricing.py": """\
from .tax import calculate_tax


def calculate_total(subtotal_cents: int) -> int:
    return subtotal_cents + calculate_tax(subtotal_cents)
""",
    "src/checkout/tax.py": """\
def calculate_tax(cents: int) -> int:
    return cents * 8 // 100
""",
    "validation/check_status.py": """\
raise SystemExit(0)
""",
}

HISTORY_DIRTY_FILES = {
    "src/checkout/pricing.py": """\
from .tax import calculate_tax


def apply_coupon(cents: int, coupon: str) -> int:
    if coupon == "BROKEN":
        return -1
    return cents


def calculate_total(subtotal_cents: int, coupon: str = "") -> int:
    discounted = apply_coupon(subtotal_cents, coupon)
    return discounted + calculate_tax(discounted)
""",
    "validation/check_status.py": """\
raise SystemExit(1)
""",
}


@dataclass(frozen=True, slots=True)
class ValidationObservation:
    validation_id: str
    repository_id: str
    ref_name: str
    ref_sha: str
    script_path: str
    expected_status: str
    expected_exit_code: int
    observed_status: str
    observed_exit_code: int
    stdout_sha256: str
    stderr_sha256: str


@dataclass(frozen=True, slots=True)
class MaterializedSource:
    source_class: str
    repository_id: str
    path: str
    remote_url: str
    branch: str
    base_commit: str
    resolved_ref: str
    dirty: bool
    commits: tuple[str, ...]
    branches: dict[str, str]
    tags: dict[str, str]
    validation_observations: tuple[ValidationObservation, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    result = subprocess.run(
        args,
        cwd=cwd,
        env=merged_env,
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"{' '.join(args)} failed ({result.returncode}): {stderr}")
    return result


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return _run(["git", "-C", str(repo), *args], check=check)


def _git_text(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.decode("utf-8").strip()


def _prepare_empty_directory(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"materialization target must be empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _write_state(repo: Path, state: dict[str, str]) -> None:
    preserved = {".git"}
    for child in list(repo.iterdir()):
        if child.name in preserved:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for relative, content in sorted(state.items()):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _initialize_repository(repo: Path, remote_url: str) -> None:
    _prepare_empty_directory(repo)
    _run(["git", "init", "--quiet", "--initial-branch=main", str(repo)])
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "remote", "add", "origin", remote_url)


def _commit(repo: Path, message: str, timestamp: str) -> str:
    _git(repo, "add", "--all")
    env = {
        **_GIT_IDENTITY,
        "GIT_AUTHOR_DATE": timestamp,
        "GIT_COMMITTER_DATE": timestamp,
    }
    _run(["git", "-C", str(repo), "commit", "--quiet", "-m", message], env=env)
    return _git_text(repo, "rev-parse", "HEAD")


def _dirty_ref(repo: Path, base_commit: str) -> str:
    digest = hashlib.sha256()
    files = _git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z").stdout
    for raw_relative in sorted(part for part in files.split(b"\0") if part):
        relative = raw_relative.decode("utf-8", errors="surrogateescape")
        path = repo / relative
        if not path.is_file():
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return f"{base_commit}+dirty.{digest.hexdigest()[:12]}"


def _repository_identity(remote_url: str) -> str:
    return "repo://" + remote_url.removeprefix("https://").removesuffix(".git")


def _materialize_project_snapshot(destination: Path, project_root: Path) -> MaterializedSource:
    remote_url = REMOTE_URLS["current_project_snapshot"]
    project_head = _git_text(project_root, "rev-parse", FIXED_PROJECT_HEAD)
    if project_head != FIXED_PROJECT_HEAD:
        raise RuntimeError(f"fixed project commit is unavailable: {FIXED_PROJECT_HEAD}")
    _prepare_empty_directory(destination.parent)
    _run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-local",
            "--no-checkout",
            str(project_root),
            str(destination),
        ]
    )
    _git(destination, "checkout", "--quiet", "--detach", FIXED_PROJECT_HEAD)
    _git(destination, "sparse-checkout", "init", "--no-cone")
    _git(destination, "sparse-checkout", "set", *PROJECT_SNAPSHOT_PATHS)
    _git(destination, "remote", "set-url", "origin", remote_url)
    commits = tuple(_git_text(destination, "rev-list", "--max-count=3", "HEAD").splitlines())
    return MaterializedSource(
        source_class="current_project_snapshot",
        repository_id=_repository_identity(remote_url),
        path=str(destination.resolve()),
        remote_url=remote_url,
        branch="HEAD",
        base_commit=FIXED_PROJECT_HEAD,
        resolved_ref=FIXED_PROJECT_HEAD,
        dirty=False,
        commits=commits,
        branches={},
        tags={},
        validation_observations=(),
    )


def _materialize_controlled(destination: Path) -> MaterializedSource:
    remote_url = REMOTE_URLS["controlled_multilingual"]
    _initialize_repository(destination, remote_url)
    _write_state(destination, CONTROLLED_FILES)
    commit = _commit(destination, "fixture: controlled multilingual source", "2026-01-01T00:00:00Z")
    return MaterializedSource(
        source_class="controlled_multilingual",
        repository_id=_repository_identity(remote_url),
        path=str(destination.resolve()),
        remote_url=remote_url,
        branch="main",
        base_commit=commit,
        resolved_ref=commit,
        dirty=False,
        commits=(commit,),
        branches={"main": commit},
        tags={},
        validation_observations=(),
    )


def _run_validation_at_ref(
    repo: Path,
    *,
    repository_id: str,
    validation_id: str,
    ref_name: str,
    ref_sha: str,
    expected_exit_code: int,
    run_root: Path,
) -> ValidationObservation:
    script_path = "validation/check_status.py"
    content = _git(repo, "show", f"{ref_sha}:{script_path}").stdout
    script = run_root / f"{validation_id}.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(content)
    result = _run([sys.executable, "-I", str(script)], check=False)
    observed_status = "passed" if result.returncode == 0 else "failed"
    expected_status = "passed" if expected_exit_code == 0 else "failed"
    return ValidationObservation(
        validation_id=validation_id,
        repository_id=repository_id,
        ref_name=ref_name,
        ref_sha=ref_sha,
        script_path=script_path,
        expected_status=expected_status,
        expected_exit_code=expected_exit_code,
        observed_status=observed_status,
        observed_exit_code=result.returncode,
        stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
        stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
    )


def _run_dirty_validation(
    repo: Path,
    *,
    repository_id: str,
    dirty_ref: str,
) -> ValidationObservation:
    result = _run(
        [sys.executable, "-I", str(repo / "validation" / "check_status.py")],
        check=False,
    )
    return ValidationObservation(
        validation_id="historical-dirty-failing",
        repository_id=repository_id,
        ref_name="dirty",
        ref_sha=dirty_ref,
        script_path="validation/check_status.py",
        expected_status="failed",
        expected_exit_code=1,
        observed_status="passed" if result.returncode == 0 else "failed",
        observed_exit_code=result.returncode,
        stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
        stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
    )


def _materialize_historical(destination: Path, run_root: Path) -> MaterializedSource:
    remote_url = REMOTE_URLS["historical_error"]
    repository_id = _repository_identity(remote_url)
    _initialize_repository(destination, remote_url)

    _write_state(destination, HISTORY_STATE_1)
    first = _commit(destination, "checkout: add legacy rounded total", "2026-01-02T00:00:00Z")

    _write_state(destination, HISTORY_STATE_2)
    second = _commit(destination, "checkout: rename total helper", "2026-01-03T00:00:00Z")
    _git(destination, "update-ref", "refs/heads/release-v1", second)
    _git(destination, "tag", "v1.0.0", second)

    _write_state(destination, HISTORY_STATE_3)
    third = _commit(destination, "checkout: replace rounding with tax", "2026-01-04T00:00:00Z")

    observations = [
        _run_validation_at_ref(
            destination,
            repository_id=repository_id,
            validation_id="historical-release-failing",
            ref_name="release-v1",
            ref_sha=second,
            expected_exit_code=1,
            run_root=run_root,
        ),
        _run_validation_at_ref(
            destination,
            repository_id=repository_id,
            validation_id="historical-main-passing",
            ref_name="main",
            ref_sha=third,
            expected_exit_code=0,
            run_root=run_root,
        ),
    ]

    for relative, content in HISTORY_DIRTY_FILES.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    dirty_ref = _dirty_ref(destination, third)
    observations.append(
        _run_dirty_validation(
            destination,
            repository_id=repository_id,
            dirty_ref=dirty_ref,
        )
    )

    return MaterializedSource(
        source_class="historical_error",
        repository_id=repository_id,
        path=str(destination.resolve()),
        remote_url=remote_url,
        branch="main",
        base_commit=third,
        resolved_ref=dirty_ref,
        dirty=True,
        commits=(first, second, third),
        branches={"main": third, "release-v1": second},
        tags={"v1.0.0": second},
        validation_observations=tuple(observations),
    )


def materialize_all(destination: Path, project_root: Path) -> dict[str, MaterializedSource]:
    """Build the three v2 source classes without mutating the source checkout."""

    destination = destination.resolve()
    project_root = project_root.resolve()
    _prepare_empty_directory(destination)
    repositories_root = destination / "repositories"
    repositories_root.mkdir()
    validation_root = destination / "validation-runs"
    validation_root.mkdir()

    sources = (
        _materialize_project_snapshot(
            repositories_root / "current-project-snapshot",
            project_root,
        ),
        _materialize_controlled(repositories_root / "controlled-multilingual"),
        _materialize_historical(
            repositories_root / "historical-error",
            validation_root,
        ),
    )
    return {source.source_class: source for source in sources}


def write_observation(path: Path, sources: dict[str, MaterializedSource]) -> None:
    payload = {
        "schema_version": "code-golden-materialization-observation-v2",
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "sources": {
            source_class: source.to_dict() for source_class, source in sorted(sources.items())
        },
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
