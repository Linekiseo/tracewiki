from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path

import pytest

from evidence_rag.rag.sources.code.contracts import CodeRelationType
from evidence_rag.rag.sources.code.diff_symbol_v2 import (
    DiffChangeType,
    DiffHunkVersion,
    DiffSymbolDiagnosticCode,
    DiffSymbolEvaluation,
    DiffSymbolEvaluationLabel,
    DiffSymbolMapper,
    DiffSymbolMatchKind,
    DiffSymbolMatchStatus,
    DiffSymbolScopeError,
    DiffSymbolSide,
    DiffSymbolSignal,
    DiffSymbolTreatmentStatus,
    SymbolVersionRef,
)
from evidence_rag.rag.sources.code.graph_v2 import CodeGraphEntityType
from evidence_rag.rag.sources.code.history_v2 import (
    HistoricalMaterializer,
    HistoryMaterializationRequest,
    InMemoryHistoricalPublicationStore,
    LineageStatus,
    SymbolLineage,
)


@dataclass(frozen=True, slots=True)
class DiffRepo:
    root: Path
    parent_sha: str
    target_sha: str
    parent_units: tuple
    target_units: tuple


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


@pytest.fixture
def diff_repo(tmp_path: Path) -> DiffRepo:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Diff Symbol Test")
    _git(root, "config", "user.email", "diff-symbol@example.invalid")
    _write(
        root / "src/stable.py",
        """\
VERSION = 1

class Calculator:
    factor = 2

    def total(self, value: int) -> int:
        return value + 1

    def helper(self, value: int) -> int:
        return value * 2


def outside(value: int) -> int:
    return value - 1
""",
    )
    _write(
        root / "src/rename_old.py",
        """\
def renamed(value: int) -> int:
    return value + 1
""",
    )
    _write(
        root / "src/deleted.py",
        """\
def removed(value: int) -> int:
    return value - 1
""",
    )
    _write(root / "assets/blob.bin", b"\x00\x01\x02")
    _git(root, "add", "--", "src", "assets/blob.bin")
    _git(root, "commit", "-q", "-m", "parent")
    parent_sha = _git(root, "rev-parse", "HEAD")

    _write(
        root / "src/stable.py",
        """\
VERSION = 2

class Calculator:
    factor = 3

    def total(self, value: int) -> int:
        return value + 2

    def helper(self, value: int) -> int:
        return value * 3


def outside(value: int) -> int:
    return value - 2
""",
    )
    _git(root, "mv", "src/rename_old.py", "src/rename_new.py")
    _write(
        root / "src/rename_new.py",
        """\
def renamed(value: int) -> int:
    return value + 2
""",
    )
    (root / "src/deleted.py").unlink()
    _write(
        root / "src/added.py",
        """\
def introduced(value: int) -> int:
    return value + 1
""",
    )
    _git(root, "add", "-A", "--", "src")
    _git(root, "commit", "-q", "-m", "target")
    target_sha = _git(root, "rev-parse", "HEAD")

    materializer = HistoricalMaterializer(InMemoryHistoricalPublicationStore())
    parent = materializer.materialize(
        HistoryMaterializationRequest(
            project_id="project-diff",
            repository_id="repo-diff",
            repository_root=root,
            ref=parent_sha,
            commit_sha=parent_sha,
            generation_id="generation-parent",
            acl_ref="acl:engineering",
            paths=("src/stable.py", "src/rename_old.py", "src/deleted.py"),
        )
    )
    target = materializer.materialize(
        HistoryMaterializationRequest(
            project_id="project-diff",
            repository_id="repo-diff",
            repository_root=root,
            ref=target_sha,
            commit_sha=target_sha,
            generation_id="generation-target",
            acl_ref="acl:engineering",
            paths=("src/stable.py", "src/rename_new.py", "src/added.py"),
        )
    )
    return DiffRepo(
        root=root,
        parent_sha=parent_sha,
        target_sha=target_sha,
        parent_units=parent.units,
        target_units=target.units,
    )


def _refs(units: tuple, path: str) -> tuple[SymbolVersionRef, ...]:
    return tuple(
        SymbolVersionRef.from_historical_unit(item)
        for item in units
        if item.file_path == path and item.unit_type in {"symbol.ast_block", "file.surface"}
    )


def _symbol(refs: tuple[SymbolVersionRef, ...], qualified_name: str) -> SymbolVersionRef:
    return next(
        item
        for item in refs
        if item.qualified_name == qualified_name
        or item.qualified_name.endswith("." + qualified_name)
    )


def _hunk(
    repo: DiffRepo,
    *,
    path: str = "src/stable.py",
    old_path: str | None = None,
    change_type: DiffChangeType = DiffChangeType.MODIFY,
    old_start: int,
    old_count: int,
    new_start: int,
    new_count: int,
    patch: str,
    is_binary: bool = False,
) -> DiffHunkVersion:
    return DiffHunkVersion.create(
        project_id="project-diff",
        repository_id="repo-diff",
        parent_commit_sha=repo.parent_sha,
        target_commit_sha=repo.target_sha,
        parent_generation_id="generation-parent",
        target_generation_id="generation-target",
        acl_ref="acl:engineering",
        path=path,
        old_path=old_path,
        change_type=change_type,
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        patch=patch,
        is_binary=is_binary,
    )


def _single_line_hunk(
    repo: DiffRepo,
    old_line: int,
    new_line: int,
    *,
    old_text: str = "old",
    new_text: str = "new",
) -> DiffHunkVersion:
    return _hunk(
        repo,
        old_start=old_line,
        old_count=1,
        new_start=new_line,
        new_count=1,
        patch=f"@@ -{old_line},1 +{new_line},1 @@\n-{old_text}\n+{new_text}\n",
    )


def _map_stable(repo: DiffRepo, hunk: DiffHunkVersion):
    return DiffSymbolMapper().map_hunk(
        hunk,
        parent_symbols=repo.parent_units,
        target_symbols=repo.target_units,
    )


def test_function_body_signature_class_and_file_top_level_mapping(
    diff_repo: DiffRepo,
) -> None:
    old_refs = _refs(diff_repo.parent_units, "src/stable.py")
    new_refs = _refs(diff_repo.target_units, "src/stable.py")
    old_total = _symbol(old_refs, "Calculator.total")
    new_total = _symbol(new_refs, "Calculator.total")
    old_class = _symbol(old_refs, "Calculator")
    new_class = _symbol(new_refs, "Calculator")

    body = _map_stable(
        diff_repo,
        _single_line_hunk(
            diff_repo,
            old_total.start_line + 1,
            new_total.start_line + 1,
        ),
    )
    signature = _map_stable(
        diff_repo,
        _single_line_hunk(diff_repo, old_total.start_line, new_total.start_line),
    )
    class_level = _map_stable(
        diff_repo,
        _single_line_hunk(
            diff_repo,
            old_class.start_line + 1,
            new_class.start_line + 1,
        ),
    )
    file_top = _map_stable(diff_repo, _single_line_hunk(diff_repo, 1, 1))

    assert body.status is DiffSymbolTreatmentStatus.MAPPED
    assert {item.match_kind for item in body.matches} == {DiffSymbolMatchKind.FUNCTION_BODY}
    assert {item.symbol.symbol_name for item in body.matches} == {"total"}
    assert {item.match_kind for item in signature.matches} == {DiffSymbolMatchKind.SIGNATURE}
    assert {item.match_kind for item in class_level.matches} == {DiffSymbolMatchKind.CLASS_LEVEL}
    assert all(item.symbol.symbol_name == "Calculator" for item in class_level.matches)
    assert {item.match_kind for item in file_top.matches} == {DiffSymbolMatchKind.FILE_TOP_LEVEL}
    assert all(
        item.symbol.entity_type is CodeGraphEntityType.FILE_VERSION for item in file_top.matches
    )
    assert all(not item.symbol.is_symbol for item in file_top.matches)
    assert any(
        item.code is DiffSymbolDiagnosticCode.FILE_TOP_LEVEL for item in file_top.diagnostics
    )


def test_rename_add_delete_and_old_new_versions_are_side_exact(
    diff_repo: DiffRepo,
) -> None:
    old_rename = _symbol(
        _refs(diff_repo.parent_units, "src/rename_old.py"),
        "renamed",
    )
    new_rename = _symbol(
        _refs(diff_repo.target_units, "src/rename_new.py"),
        "renamed",
    )
    rename = DiffSymbolMapper().map_hunk(
        _hunk(
            diff_repo,
            path="src/rename_new.py",
            old_path="src/rename_old.py",
            change_type=DiffChangeType.RENAME,
            old_start=old_rename.start_line + 1,
            old_count=1,
            new_start=new_rename.start_line + 1,
            new_count=1,
            patch=(
                f"@@ -{old_rename.start_line + 1},1 "
                f"+{new_rename.start_line + 1},1 @@\n"
                "-    return value + 1\n"
                "+    return value + 2\n"
            ),
        ),
        parent_symbols=diff_repo.parent_units,
        target_symbols=diff_repo.target_units,
    )
    added = _symbol(_refs(diff_repo.target_units, "src/added.py"), "introduced")
    add = DiffSymbolMapper().map_hunk(
        _hunk(
            diff_repo,
            path="src/added.py",
            change_type=DiffChangeType.ADD,
            old_start=0,
            old_count=0,
            new_start=added.start_line,
            new_count=2,
            patch=(
                f"@@ -0,0 +{added.start_line},2 @@\n"
                "+def introduced(value: int) -> int:\n"
                "+    return value + 1\n"
            ),
        ),
        parent_symbols=diff_repo.parent_units,
        target_symbols=diff_repo.target_units,
    )
    deleted = _symbol(_refs(diff_repo.parent_units, "src/deleted.py"), "removed")
    delete = DiffSymbolMapper().map_hunk(
        _hunk(
            diff_repo,
            path="src/deleted.py",
            change_type=DiffChangeType.DELETE,
            old_start=deleted.start_line,
            old_count=2,
            new_start=0,
            new_count=0,
            patch=(
                f"@@ -{deleted.start_line},2 +0,0 @@\n"
                "-def removed(value: int) -> int:\n"
                "-    return value - 1\n"
            ),
        ),
        parent_symbols=diff_repo.parent_units,
        target_symbols=diff_repo.target_units,
    )

    assert rename.status is DiffSymbolTreatmentStatus.MAPPED
    assert {item.symbol.path for item in rename.matches if item.side is DiffSymbolSide.OLD} == {
        "src/rename_old.py"
    }
    assert {item.symbol.path for item in rename.matches if item.side is DiffSymbolSide.NEW} == {
        "src/rename_new.py"
    }
    assert all(DiffSymbolSignal.RENAME_PATH in item.signals for item in rename.matches)
    assert all(item.change_role == "renamed" for item in rename.matches)
    assert {
        item.symbol.commit_sha for item in rename.matches if item.side is DiffSymbolSide.OLD
    } == {diff_repo.parent_sha}
    assert {
        item.symbol.commit_sha for item in rename.matches if item.side is DiffSymbolSide.NEW
    } == {diff_repo.target_sha}

    assert add.status is DiffSymbolTreatmentStatus.MAPPED
    assert {item.side for item in add.matches} == {DiffSymbolSide.NEW}
    assert all(item.change_role == "introduced" for item in add.matches)
    assert any(item.code is DiffSymbolDiagnosticCode.MISSING_OLD_SIDE for item in add.diagnostics)
    assert delete.status is DiffSymbolTreatmentStatus.MAPPED
    assert {item.side for item in delete.matches} == {DiffSymbolSide.OLD}
    assert all(item.change_role == "removed" for item in delete.matches)
    assert any(
        item.code is DiffSymbolDiagnosticCode.MISSING_NEW_SIDE for item in delete.diagnostics
    )


def test_hunk_spanning_multiple_symbols_emits_each_exact_symbol_once(
    diff_repo: DiffRepo,
) -> None:
    old_refs = _refs(diff_repo.parent_units, "src/stable.py")
    new_refs = _refs(diff_repo.target_units, "src/stable.py")
    old_total = _symbol(old_refs, "Calculator.total")
    old_helper = _symbol(old_refs, "Calculator.helper")
    new_total = _symbol(new_refs, "Calculator.total")
    new_helper = _symbol(new_refs, "Calculator.helper")
    old_first = old_total.start_line + 1
    old_second = old_helper.start_line + 1
    new_first = new_total.start_line + 1
    new_second = new_helper.start_line + 1
    context_count = old_second - old_first - 1
    patch = (
        f"@@ -{old_first},{old_second - old_first + 1} "
        f"+{new_first},{new_second - new_first + 1} @@\n"
        "-    return value + 1\n"
        "+    return value + 2\n"
        + (" context\n" * context_count)
        + "-    return value * 2\n"
        + "+    return value * 3\n"
    )
    treatment = _map_stable(
        diff_repo,
        _hunk(
            diff_repo,
            old_start=old_first,
            old_count=old_second - old_first + 1,
            new_start=new_first,
            new_count=new_second - new_first + 1,
            patch=patch,
        ),
    )

    assert treatment.status is DiffSymbolTreatmentStatus.MAPPED
    confirmed = [
        item for item in treatment.matches if item.status is DiffSymbolMatchStatus.CONFIRMED
    ]
    assert {item.symbol.symbol_name for item in confirmed} == {"total", "helper"}
    assert len(confirmed) == 4
    affects = [item for item in treatment.edges if item.relation_type is CodeRelationType.AFFECTS]
    assert len(affects) == 4
    assert len({item.edge_id for item in treatment.edges}) == len(treatment.edges)


def test_zero_count_boundary_maps_enclosing_old_symbol_without_fake_overlap(
    diff_repo: DiffRepo,
) -> None:
    old_total = _symbol(
        _refs(diff_repo.parent_units, "src/stable.py"),
        "Calculator.total",
    )
    new_total = _symbol(
        _refs(diff_repo.target_units, "src/stable.py"),
        "Calculator.total",
    )
    treatment = _map_stable(
        diff_repo,
        _hunk(
            diff_repo,
            old_start=old_total.start_line + 1,
            old_count=0,
            new_start=new_total.start_line + 1,
            new_count=1,
            patch=(
                f"@@ -{old_total.start_line + 1},0 "
                f"+{new_total.start_line + 1},1 @@\n"
                "+    inserted = value\n"
            ),
        ),
    )
    old_match = next(item for item in treatment.matches if item.side is DiffSymbolSide.OLD)

    assert DiffSymbolSignal.ZERO_COUNT_BOUNDARY in old_match.signals
    assert old_match.overlap_line_count == 0
    assert old_match.overlap_start is None


def test_whitespace_binary_and_missing_patch_evidence_are_honest(
    diff_repo: DiffRepo,
) -> None:
    total = _symbol(
        _refs(diff_repo.parent_units, "src/stable.py"),
        "Calculator.total",
    )
    whitespace = _map_stable(
        diff_repo,
        _single_line_hunk(
            diff_repo,
            total.start_line + 1,
            total.start_line + 1,
            old_text="return value + 1",
            new_text="    return   value + 1",
        ),
    )
    binary = DiffSymbolMapper().map_hunk(
        _hunk(
            diff_repo,
            path="assets/blob.bin",
            old_start=0,
            old_count=0,
            new_start=0,
            new_count=0,
            patch="Binary files a/assets/blob.bin and b/assets/blob.bin differ",
            is_binary=True,
        ),
    )

    assert whitespace.status is DiffSymbolTreatmentStatus.SKIPPED
    assert whitespace.matches == ()
    assert [item.relation_type for item in whitespace.edges] == [CodeRelationType.CONTAINS]
    assert any(
        item.code is DiffSymbolDiagnosticCode.WHITESPACE_ONLY_SKIPPED
        for item in whitespace.diagnostics
    )
    assert binary.status is DiffSymbolTreatmentStatus.UNAVAILABLE
    assert binary.matches == ()
    assert any(
        item.code is DiffSymbolDiagnosticCode.BINARY_UNAVAILABLE for item in binary.diagnostics
    )


def test_wrong_parent_project_repository_generation_or_acl_fails_closed(
    diff_repo: DiffRepo,
) -> None:
    hunk = _single_line_hunk(diff_repo, 7, 7)
    old_ref = _symbol(_refs(diff_repo.parent_units, "src/stable.py"), "Calculator.total")
    new_ref = _symbol(_refs(diff_repo.target_units, "src/stable.py"), "Calculator.total")
    wrong_values = (
        replace(
            old_ref,
            commit_sha="f" * 40,
            evidence_locator=old_ref.evidence_locator.replace(
                diff_repo.parent_sha,
                "f" * 40,
            ),
        ),
        replace(old_ref, project_id="wrong-project"),
        replace(old_ref, repository_id="wrong-repository"),
        replace(old_ref, generation_id="wrong-generation"),
        replace(old_ref, acl_ref="acl:other"),
    )

    for wrong in wrong_values:
        with pytest.raises(DiffSymbolScopeError):
            DiffSymbolMapper().map_hunk(
                hunk,
                parent_symbols=(wrong,),
                target_symbols=(new_ref,),
            )


def test_equally_specific_symbols_remain_candidates_without_affects_assertion(
    diff_repo: DiffRepo,
) -> None:
    old_ref = _symbol(_refs(diff_repo.parent_units, "src/stable.py"), "Calculator.total")
    new_ref = _symbol(_refs(diff_repo.target_units, "src/stable.py"), "Calculator.total")
    duplicate = replace(
        old_ref,
        symbol_version_id=old_ref.symbol_version_id + "-ambiguous",
        unit_id=old_ref.unit_id + "-ambiguous",
        symbol_name="total_alias",
        qualified_name="Calculator.total_alias",
    )
    hunk = _single_line_hunk(
        diff_repo,
        old_ref.start_line + 1,
        new_ref.start_line + 1,
    )
    treatment = DiffSymbolMapper().map_hunk(
        hunk,
        parent_symbols=(old_ref, duplicate),
        target_symbols=(new_ref,),
    )

    old_matches = [item for item in treatment.matches if item.side is DiffSymbolSide.OLD]
    assert treatment.status is DiffSymbolTreatmentStatus.PARTIAL
    assert len(old_matches) == 2
    assert all(item.status is DiffSymbolMatchStatus.CANDIDATE for item in old_matches)
    assert all(item.review_required for item in old_matches)
    assert any(
        item.code is DiffSymbolDiagnosticCode.AMBIGUOUS_SYMBOLS for item in treatment.diagnostics
    )
    affects_targets = {
        item.target_id for item in treatment.edges if item.relation_type is CodeRelationType.AFFECTS
    }
    assert old_ref.symbol_version_id not in affects_targets
    assert duplicate.symbol_version_id not in affects_targets


def test_only_explicit_confirmed_c6_lineage_is_cited(diff_repo: DiffRepo) -> None:
    old_ref = _symbol(_refs(diff_repo.parent_units, "src/stable.py"), "Calculator.total")
    new_ref = _symbol(_refs(diff_repo.target_units, "src/stable.py"), "Calculator.total")
    hunk = _single_line_hunk(
        diff_repo,
        old_ref.start_line + 1,
        new_ref.start_line + 1,
    )
    lineage = SymbolLineage(
        contract_version="c6-symbol-lineage-v1",
        lineage_id="lineage://confirmed-total",
        project_id="project-diff",
        repository_id="repo-diff",
        source_commit_sha=diff_repo.parent_sha,
        target_commit_sha=diff_repo.target_sha,
        source_generation_id="generation-parent",
        target_generation_id="generation-target",
        acl_ref="acl:engineering",
        source_unit_id=old_ref.unit_id,
        target_unit_id=new_ref.unit_id,
        source_locator=old_ref.evidence_locator,
        target_locator=new_ref.evidence_locator,
        relation_type=CodeRelationType.SAME_SYMBOL_AS,
        confidence=1.0,
        status=LineageStatus.CONFIRMED,
        review_required=False,
        signals=(),
        explanation_trace=("explicit C6-01 confirmed result",),
    )
    treatment = DiffSymbolMapper().map_hunk(
        hunk,
        parent_symbols=(old_ref,),
        target_symbols=(new_ref,),
        confirmed_lineages=(lineage,),
    )

    assert treatment.confirmed_lineages == (lineage,)
    assert {item.relation_type for item in treatment.edges} == {
        CodeRelationType.CONTAINS,
        CodeRelationType.AFFECTS,
    }
    assert all(
        item.relation_type
        not in {
            CodeRelationType.SAME_SYMBOL_AS,
            CodeRelationType.RENAMED_TO,
            CodeRelationType.MOVED_TO,
        }
        for item in treatment.edges
    )


def test_contracts_are_frozen_versioned_and_evaluation_labels_are_explicit(
    diff_repo: DiffRepo,
) -> None:
    hunk = _single_line_hunk(diff_repo, 7, 7)
    evaluation = DiffSymbolEvaluation(
        evaluation_id="diff-symbol-evaluation://case-1",
        project_id="project-diff",
        repository_id="repo-diff",
        parent_commit_sha=diff_repo.parent_sha,
        target_commit_sha=diff_repo.target_sha,
        acl_ref="acl:engineering",
        hunk_id=hunk.hunk_id,
        label=DiffSymbolEvaluationLabel.NOT_EVALUATED,
        match_id=None,
        expected_symbol_version_id=None,
        observed_symbol_version_id=None,
    )

    assert hunk.contract_version
    assert evaluation.evaluation_version
    with pytest.raises(FrozenInstanceError):
        hunk.path = "src/other.py"  # type: ignore[misc]


def test_mapping_is_deterministic_and_idempotent_for_reordered_inputs(
    diff_repo: DiffRepo,
) -> None:
    old_ref = _symbol(_refs(diff_repo.parent_units, "src/stable.py"), "Calculator.total")
    new_ref = _symbol(_refs(diff_repo.target_units, "src/stable.py"), "Calculator.total")
    hunk = _single_line_hunk(
        diff_repo,
        old_ref.start_line + 1,
        new_ref.start_line + 1,
    )
    mapper = DiffSymbolMapper()
    forward = mapper.map_hunk(
        hunk,
        parent_symbols=diff_repo.parent_units,
        target_symbols=diff_repo.target_units,
    )
    reverse = mapper.map_hunk(
        hunk,
        parent_symbols=reversed(diff_repo.parent_units),
        target_symbols=reversed(diff_repo.target_units),
    )
    repeated = mapper.map(
        hunk,
        old_symbols=diff_repo.parent_units,
        new_symbols=diff_repo.target_units,
    )

    assert forward == reverse == repeated
    assert forward.canonical_json_bytes() == reverse.canonical_json_bytes()
    assert forward.canonical_sha256() == repeated.canonical_sha256()
    assert len({item.edge_id for item in forward.edges}) == len(forward.edges)
