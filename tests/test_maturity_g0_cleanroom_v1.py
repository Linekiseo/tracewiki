from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from evidence_rag.evaluation.golden import golden_materializer
from evidence_rag.evaluation.maturity_g0_admission_v2 import (
    build_admission_manifest,
    materialize_admission_snapshot,
    verify_admission_manifest,
    write_admission_manifest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CLEANROOM_PATH = REPOSITORY_ROOT / "tests/fixtures/g0_history/cleanroom.py"
HISTORY_MANIFEST = Path("tests/fixtures/g0_history/code-golden-v2-history.json")
HISTORY_BUNDLE = Path("tests/fixtures/g0_history/code-golden-v2-history.bundle")
FIXED_COMMIT = "bc3326edc761e3bdb42ed78726a21f314ab44974"


def _load_cleanroom() -> ModuleType:
    name = "rag_maturity_g0_cleanroom_v1_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, CLEANROOM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture(scope="module")
def prepared_cleanroom(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("g0-self-contained-cleanroom")
    admission = root / "admission.json"
    source = root / "source"
    write_admission_manifest(REPOSITORY_ROOT, admission)
    materialize_admission_snapshot(admission, REPOSITORY_ROOT, source)
    cleanroom = _load_cleanroom()
    result = cleanroom.prepare_cleanroom(source, admission)
    return {
        "admission": admission,
        "cleanroom": cleanroom,
        "result": result,
        "root": root,
        "source": source,
    }


def test_cleanroom_requires_a_materialized_uninitialized_source(tmp_path: Path) -> None:
    cleanroom = _load_cleanroom()
    with pytest.raises(cleanroom.CleanroomError, match="uninitialized materialized source"):
        cleanroom.prepare_cleanroom(REPOSITORY_ROOT, tmp_path / "missing-admission.json")


def test_history_bundle_tampering_fails_before_git_initialization(tmp_path: Path) -> None:
    cleanroom = _load_cleanroom()
    admission = build_admission_manifest(REPOSITORY_ROOT)
    source = tmp_path / "source"
    history_root = source / HISTORY_MANIFEST.parent
    history_root.mkdir(parents=True)
    shutil.copy2(REPOSITORY_ROOT / HISTORY_MANIFEST, source / HISTORY_MANIFEST)
    shutil.copy2(REPOSITORY_ROOT / HISTORY_BUNDLE, source / HISTORY_BUNDLE)
    with (source / HISTORY_BUNDLE).open("r+b") as stream:
        first = stream.read(1)
        assert first
        stream.seek(0)
        stream.write(bytes([first[0] ^ 0x01]))

    with pytest.raises(cleanroom.CleanroomError, match="admitted source bytes"):
        cleanroom._validate_history_manifest(source, admission)
    assert not (source / ".git").exists()


def test_cleanroom_is_release_ready_and_supports_real_v2_materialization(
    prepared_cleanroom: dict[str, Any],
) -> None:
    source = prepared_cleanroom["source"]
    admission = prepared_cleanroom["admission"]
    result = prepared_cleanroom["result"]

    assert result["status"] == "SELF_CONTAINED_CLEANROOM_READY"
    assert result["release_ready"] is True
    assert result["fixed_history_commit"] == FIXED_COMMIT
    assert result["history_security_scan"] == {
        "binary_blob_count": 64,
        "finding_blob_count": 7,
        "object_counts": {"blob": 328, "commit": 13, "tree": 90},
        "reachable_object_count": 431,
        "text_blob_count": 264,
    }
    assert _git(source, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert _git(source, "rev-parse", FIXED_COMMIT) == FIXED_COMMIT
    assert (
        verify_admission_manifest(admission, root=source, require_release_ready=True)[
            "current_release_ready"
        ]
        is True
    )

    materializer = golden_materializer(source)
    sources = materializer.materialize_all(prepared_cleanroom["root"] / "code-golden", source)
    snapshot = sources["current_project_snapshot"]
    assert snapshot.base_commit == FIXED_COMMIT
    assert snapshot.resolved_ref == FIXED_COMMIT
    assert snapshot.commits[:3] == (
        FIXED_COMMIT,
        "aee6638d7bfecc63ace41aac019711b2ea2f0374",
        "0c275774940d561319e114972c82121ff64e7e03",
    )
