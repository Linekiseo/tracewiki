from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from ..workspace.models import RelationCreate
from ..workspace.service import WorkspaceService
from .models import BindingReviewRequest, BindingScanRequest
from .store import BindingStore


class BindingError(ValueError):
    pass


class BindingService:
    def __init__(self, store: BindingStore, workspace: WorkspaceService) -> None:
        self.store = store
        self.workspace = workspace

    def start_scan(self, request: BindingScanRequest) -> str:
        self.workspace._require_project(request.project_id)
        scan_id = f"binding-scan://{uuid4().hex}"
        self.store.create_scan(scan_id, request.project_id, request.model_dump())
        return scan_id

    def scan(self, request: BindingScanRequest) -> dict[str, Any]:
        return self.run_scan(self.start_scan(request), request)

    def run_scan(self, scan_id: str, request: BindingScanRequest) -> dict[str, Any]:
        counts = {
            "source_items": 0,
            "source_items_processed": 0,
            "paths": 0,
            "file_candidates": 0,
            "commit_candidates": 0,
            "unmatched": 0,
            "explicit_uncommitted": 0,
            "invalid_paths": 0,
        }
        candidate_records: list[dict[str, Any]] = []
        unmatched_records: list[dict[str, Any]] = []
        symbol_cache: dict[tuple[str, str, str], list[str]] = {}
        history_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        commit_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
        patch_line_cache: dict[str, set[str]] = {}
        try:
            changes = self.store.source_changes(request.project_id, request.thread_ids)
            repositories = self.store.repository_snapshots(
                request.project_id, request.repository_ids
            )
            file_indexes = {
                repo["id"]: self._file_index(
                    self.store.files_for_repository(repo["id"], repo["active_generation_id"])
                )
                for repo in repositories
            }
            counts["source_items"] = len(changes)
            self.store.update_scan_progress(scan_id, counts)
            for item_index, change in enumerate(changes, start=1):
                raw_paths = self._raw_change_paths(change)
                paths = self._change_paths(change)
                counts["paths"] += len(paths)
                counts["invalid_paths"] += max(0, len(raw_paths) - len(paths))
                for changed_path in paths:
                    matches = self._matches(change, str(changed_path), repositories, file_indexes)
                    for repo, target, derivation, confidence, normalized in matches:
                        symbol_key = (
                            repo["id"],
                            repo["active_generation_id"],
                            target["path"],
                        )
                        if symbol_key not in symbol_cache:
                            symbol_cache[symbol_key] = self.store.symbols_for_file(*symbol_key)
                        symbols = symbol_cache[symbol_key]
                        token = hashlib.sha256(
                            f"{change['id']}\x1f{repo['id']}\x1f{changed_path}\x1f{target['id']}".encode()
                        ).hexdigest()
                        candidate_records.append(
                            {
                                "id": f"binding://sha256:{token}",
                                "project_id": request.project_id,
                                "scan_id": scan_id,
                                "binding_type": "codex_file_change",
                                "source_entity_id": change["id"],
                                "source_thread_id": change["thread_id"],
                                "source_turn_id": change["turn_id"],
                                "source_item_type": change["item_type"],
                                "source_title": change["thread_title"],
                                "changed_path": str(changed_path),
                                "repository_id": repo["id"],
                                "repository_name": repo["name"],
                                "target_entity_id": target["id"],
                                "target_generation_id": repo["active_generation_id"],
                                "target_path": target["path"],
                                "target_commit_id": repo["commit_id"],
                                "target_commit_sha": repo["head_commit"],
                                "target_symbol_ids": symbols,
                                "derivation": derivation,
                                "confidence": confidence,
                                "signals": {
                                    "normalized_path": normalized,
                                    "cwd": change.get("cwd"),
                                    "repository_local_path": repo["local_path"],
                                    "symbol_count": len(symbols),
                                    "commit_context_only": True,
                                },
                            }
                        )
                        counts["file_candidates"] += 1
                    commit_candidates = self._commit_matches(
                        change,
                        str(changed_path),
                        repositories,
                        history_cache=history_cache,
                        commit_cache=commit_cache,
                        patch_line_cache=patch_line_cache,
                    )
                    for candidate in commit_candidates:
                        candidate_records.append(
                            self._commit_candidate_record(
                                request.project_id,
                                scan_id,
                                change,
                                str(changed_path),
                                candidate,
                            )
                        )
                        counts["commit_candidates"] += 1
                    if not matches:
                        counts["unmatched"] += 1
                    if not commit_candidates:
                        counts["explicit_uncommitted"] += 1
                        unmatched_records.append(
                            {
                                "id": f"unmatched://{uuid4().hex}",
                                "project_id": request.project_id,
                                "scan_id": scan_id,
                                "source_entity_id": change["id"],
                                "source_thread_id": change["thread_id"],
                                "changed_path": str(changed_path),
                                "repository_id": matches[0][0]["id"] if matches else None,
                                "reason": "no_matching_commit_diff",
                                "signals": {
                                    "timestamp": change.get("timestamp"),
                                    "indexed_history_checked": True,
                                    "status": "executed_not_committed",
                                },
                            }
                        )
                counts["source_items_processed"] = item_index
                if item_index % 250 == 0:
                    self.store.update_scan_progress(scan_id, counts)
            counts["candidates"] = counts["file_candidates"] + counts["commit_candidates"]
            self.store.publish_scan(
                scan_id,
                candidates=candidate_records,
                unmatched=unmatched_records,
                counts=counts,
            )
            return {"scan_id": scan_id, "status": "completed", "counts": counts}
        except Exception as exc:
            self.store.fail_scan(scan_id, f"{type(exc).__name__}: {exc}")
            raise

    def review(self, binding_id: str, request: BindingReviewRequest) -> dict[str, Any]:
        candidate = self.store.get_candidate(binding_id)
        if not candidate:
            raise BindingError("binding candidate not found")
        if candidate["review_status"] != "unreviewed":
            raise BindingError("binding candidate has already been reviewed")
        relation = None
        if request.decision == "confirmed":
            predicate = request.predicate
            if (
                candidate["binding_type"] == "codex_patch_commit"
                and predicate == "changed_path_maps_to"
            ):
                predicate = "applied_as"
            relation = self.workspace.create_relation(
                RelationCreate(
                    project_id=candidate["project_id"],
                    source_entity_id=candidate["source_entity_id"],
                    predicate=predicate,
                    target_entity_id=candidate["target_entity_id"],
                    evidence_entity_id=candidate["source_entity_id"],
                    derivation="human_confirmed",
                    confidence=candidate["confidence"],
                    review_status="confirmed",
                    rule_version="codex-path-binding-v1",
                    metadata={
                        "binding_id": binding_id,
                        "changed_path": candidate["changed_path"],
                        "target_commit_sha": candidate["target_commit_sha"],
                        "commit_context_only": candidate["binding_type"] != "codex_patch_commit",
                    },
                )
            )
        reviewed = self.store.review(binding_id, request.decision, request.reviewer, request.note)
        return {"binding": reviewed, "relation": relation}

    def review_all(
        self,
        project_id: str,
        *,
        expected_pending: int,
        reviewer: str,
        note: str,
    ) -> dict[str, Any]:
        """Atomically confirm the exact pending queue observed by the reviewer.

        ``expected_pending`` is an optimistic concurrency guard.  A scan that is
        still publishing candidates, another reviewer, or a generation change
        causes the whole operation to fail without partially confirming rows.
        """

        self.workspace._require_project(project_id)
        try:
            return self.store.confirm_all(
                project_id,
                expected_pending=expected_pending,
                reviewer=reviewer,
                note=note,
            )
        except ValueError as exc:
            raise BindingError(str(exc)) from exc

    def _commit_matches(
        self,
        change: dict[str, Any],
        changed_path: str,
        repositories: list[dict[str, Any]],
        *,
        history_cache: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
        commit_cache: dict[tuple[str, str], dict[str, Any] | None] | None = None,
        patch_line_cache: dict[str, set[str]] | None = None,
    ) -> list[dict[str, Any]]:
        explicit_shas = set(
            re.findall(r"(?<![0-9a-f])[0-9a-f]{7,40}(?![0-9a-f])", change["content"], re.I)
        )
        for key in ("commit", "commit_sha", "head", "head_commit"):
            value = change.get("metadata", {}).get(key)
            if value:
                explicit_shas.add(str(value))
        matches: list[dict[str, Any]] = []
        source_patch_lines = self._patch_lines(change["content"])
        absolute_path = self._is_absolute_changed_path(changed_path)
        cwd_bound = any(
            self._cwd_within(change.get("cwd"), repo["local_path"]) for repo in repositories
        )
        for repo in repositories:
            normalized, aligned = self._relative_path(
                changed_path, change.get("cwd"), repo["local_path"]
            )
            if (
                not absolute_path
                and cwd_bound
                and not self._cwd_within(change.get("cwd"), repo["local_path"])
            ):
                continue
            if absolute_path and not aligned:
                continue
            for sha in explicit_shas:
                commit_key = (repo["id"], sha)
                if commit_cache is not None and commit_key in commit_cache:
                    commit = commit_cache[commit_key]
                else:
                    commit = self.store.resolve_history_commit(repo["id"], sha)
                    if commit_cache is not None:
                        commit_cache[commit_key] = commit
                if commit:
                    matches.append(
                        {
                            "repository": repo,
                            "commit": commit,
                            "path": normalized,
                            "derivation": "explicit_commit_sha",
                            "confidence": 1.0,
                            "signals": {"explicit_sha": sha},
                        }
                    )
            if any(item["repository"]["id"] == repo["id"] for item in matches):
                continue
            # FileChange events normally contain only structured path metadata.
            # Without an explicit commit or observable +/- patch lines there is
            # no patch truth to compare, so querying up to 200 history hunks per
            # event is both wasteful and semantically ungrounded.
            if not source_patch_lines:
                continue
            ranked = []
            history_key = (repo["id"], normalized)
            if history_cache is not None and history_key in history_cache:
                history = history_cache[history_key]
            else:
                history = self.store.history_candidates(repo["id"], normalized)
                if history_cache is not None:
                    history_cache[history_key] = history
            for hunk in history:
                hunk_key = str(hunk["id"])
                if patch_line_cache is not None and hunk_key in patch_line_cache:
                    target_patch_lines = patch_line_cache[hunk_key]
                else:
                    target_patch_lines = self._patch_lines(hunk["patch"])
                    if patch_line_cache is not None:
                        patch_line_cache[hunk_key] = target_patch_lines
                patch_score = self._patch_line_similarity(source_patch_lines, target_patch_lines)
                time_score = self._time_score(change.get("timestamp"), hunk.get("committed_at"))
                score = 0.88 * patch_score + 0.12 * time_score
                if score < 0.52:
                    continue
                ranked.append((score, patch_score, time_score, hunk))
            ranked.sort(key=lambda item: item[0], reverse=True)
            for score, patch_score, time_score, hunk in ranked[:3]:
                commit_key = (repo["id"], hunk["commit_sha"])
                if commit_cache is not None and commit_key in commit_cache:
                    commit = commit_cache[commit_key]
                else:
                    commit = self.store.resolve_history_commit(repo["id"], hunk["commit_sha"])
                    if commit_cache is not None:
                        commit_cache[commit_key] = commit
                if not commit:
                    continue
                matches.append(
                    {
                        "repository": repo,
                        "commit": commit,
                        "path": normalized,
                        "derivation": "patch_diff_exact"
                        if patch_score >= 0.98
                        else "patch_hunk_similarity",
                        "confidence": round(min(0.99, score), 4),
                        "signals": {
                            "diff_hunk_id": hunk["id"],
                            "patch_similarity": round(patch_score, 4),
                            "time_proximity": round(time_score, 4),
                            "committed_at": hunk.get("committed_at"),
                            "patch_hash": hunk["patch_hash"],
                        },
                    }
                )
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in matches:
            key = (item["repository"]["id"], item["commit"]["id"], item["path"])
            if key not in unique or item["confidence"] > unique[key]["confidence"]:
                unique[key] = item
        return list(unique.values())

    def _commit_candidate_record(
        self,
        project_id: str,
        scan_id: str,
        change: dict[str, Any],
        changed_path: str,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        repo = candidate["repository"]
        commit = candidate["commit"]
        token = hashlib.sha256(
            f"{change['id']}\x1f{repo['id']}\x1f{changed_path}\x1f{commit['id']}".encode()
        ).hexdigest()
        return {
            "id": f"binding://sha256:{token}",
            "project_id": project_id,
            "scan_id": scan_id,
            "binding_type": "codex_patch_commit",
            "source_entity_id": change["id"],
            "source_thread_id": change["thread_id"],
            "source_turn_id": change["turn_id"],
            "source_item_type": change["item_type"],
            "source_title": change["thread_title"],
            "changed_path": changed_path,
            "repository_id": repo["id"],
            "repository_name": repo["name"],
            "target_entity_id": commit["id"],
            "target_generation_id": repo["active_generation_id"],
            "target_path": candidate["path"],
            "target_commit_id": commit["id"],
            "target_commit_sha": commit["sha"],
            "target_symbol_ids": [],
            "derivation": candidate["derivation"],
            "confidence": candidate["confidence"],
            "signals": {
                **candidate["signals"],
                "commit_context_only": False,
                "relation_candidate": "applied_as",
            },
        }

    @staticmethod
    def _patch_lines(value: str) -> set[str]:
        normalized = set()
        for line in value.splitlines():
            if line.startswith(("+++", "---", "@@", "diff ", "index ")):
                continue
            if line.startswith(("+", "-")):
                content = re.sub(r"\s+", " ", line[1:].strip())
                if content:
                    normalized.add(line[0] + content)
        return normalized

    @staticmethod
    def _patch_line_similarity(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    @classmethod
    def _patch_similarity(cls, source: str, target: str) -> float:
        return cls._patch_line_similarity(cls._patch_lines(source), cls._patch_lines(target))

    @staticmethod
    def _time_score(left: str | None, right: str | None) -> float:
        if not left or not right:
            return 0.0
        try:
            start = datetime.fromisoformat(left.replace("Z", "+00:00"))
            end = datetime.fromisoformat(right.replace("Z", "+00:00"))
            hours = abs((end - start).total_seconds()) / 3600
            return max(0.0, 1.0 - hours / (24 * 14))
        except ValueError:
            return 0.0

    @staticmethod
    def _file_index(files: list[dict[str, Any]]) -> dict[str, Any]:
        exact = {item["path"].replace("\\", "/").lstrip("./"): item for item in files}
        suffix: dict[str, list[dict[str, Any]]] = {}
        for path, item in exact.items():
            parts = PurePosixPath(path).parts
            for offset in range(len(parts)):
                suffix.setdefault("/".join(parts[offset:]), []).append(item)
        return {"exact": exact, "suffix": suffix, "files": files}

    def _matches(
        self,
        change: dict[str, Any],
        changed_path: str,
        repositories: list[dict[str, Any]],
        indexes: dict[str, dict[str, Any]],
    ) -> list[tuple[dict[str, Any], dict[str, Any], str, float, str]]:
        results = []
        absolute_path = self._is_absolute_changed_path(changed_path)
        cwd_bound = any(
            self._cwd_within(change.get("cwd"), repo["local_path"]) for repo in repositories
        )
        for repo in repositories:
            normalized, cwd_aligned = self._relative_path(
                changed_path, change.get("cwd"), repo["local_path"]
            )
            if (
                not absolute_path
                and cwd_bound
                and not self._cwd_within(change.get("cwd"), repo["local_path"])
            ):
                continue
            if absolute_path and not cwd_aligned:
                continue
            index = indexes[repo["id"]]
            target = index["exact"].get(normalized)
            if target:
                confidence = 0.98 if cwd_aligned else 0.92
                results.append((repo, target, "path_exact", confidence, normalized))
                continue
            suffixes_by_id: dict[str, dict[str, Any]] = {}
            for item in index.get("suffix", {}).get(normalized, []):
                suffixes_by_id[str(item["id"])] = item
            normalized_parts = PurePosixPath(normalized).parts
            for offset in range(1, len(normalized_parts)):
                item = index["exact"].get("/".join(normalized_parts[offset:]))
                if item:
                    suffixes_by_id[str(item["id"])] = item
            suffixes = list(suffixes_by_id.values())
            if len(suffixes) == 1:
                results.append((repo, suffixes[0], "path_suffix_unique", 0.78, normalized))
        return results

    @staticmethod
    def _relative_path(path: str, cwd: str | None, repository_path: str) -> tuple[str, bool]:
        clean = path.replace("\\", "/")
        for prefix in ("*** Update File:", "*** Add File:", "*** Delete File:"):
            clean = clean.removeprefix(prefix).strip()
        clean = clean.removeprefix("a/").removeprefix("b/").removeprefix("./")
        repo = Path(repository_path).expanduser().resolve(strict=False)
        candidate = Path(clean).expanduser()
        if candidate.is_absolute():
            try:
                return candidate.resolve(strict=False).relative_to(repo).as_posix(), True
            except ValueError:
                return PurePosixPath(clean).as_posix().lstrip("/"), False
        cwd_aligned = False
        if cwd:
            try:
                Path(cwd).expanduser().resolve(strict=False).relative_to(repo)
                cwd_aligned = True
            except ValueError:
                pass
        return PurePosixPath(clean).as_posix(), cwd_aligned

    @staticmethod
    def _cwd_within(cwd: str | None, repository_path: str) -> bool:
        if not cwd:
            return False
        try:
            Path(cwd).expanduser().resolve(strict=False).relative_to(
                Path(repository_path).expanduser().resolve(strict=False)
            )
            return True
        except ValueError:
            return False

    @staticmethod
    def _is_absolute_changed_path(path: str) -> bool:
        clean = path.replace("\\", "/")
        for prefix in ("*** Update File:", "*** Add File:", "*** Delete File:"):
            clean = clean.removeprefix(prefix).strip()
        clean = clean.removeprefix("a/").removeprefix("b/").removeprefix("./")
        return Path(clean).expanduser().is_absolute()

    @staticmethod
    def _content_paths(content: str) -> list[str]:
        patterns = re.findall(
            r"^(?:\*\*\* (?:Update|Add|Delete) File:|\+\+\+|---)\s+([^\s]+)",
            content,
            re.MULTILINE,
        )
        return [value for value in dict.fromkeys(patterns) if value != "/dev/null"][:100]

    @classmethod
    def _raw_change_paths(cls, change: dict[str, Any]) -> list[str]:
        """Return path claims from the strongest observable representation.

        FileChange payloads contain a JSON object keyed by the files that were
        actually changed.  Older Codex imports also carry a lossy ``paths``
        metadata field which can include tokens scraped from the patch body.
        Preferring the payload keys prevents patch prose from becoming a high
        confidence relation merely because the same event mentions a commit.
        """

        content = str(change.get("content") or "")
        item_type = str(change.get("item_type") or "")
        if item_type == "FileChange":
            try:
                payload = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                payload = None
            if isinstance(payload, dict):
                payload_paths = [str(value) for value in payload if str(value).strip()]
                if payload_paths:
                    return list(dict.fromkeys(payload_paths))[:100]

        content_paths = cls._content_paths(content)
        if content_paths:
            return content_paths

        metadata = change.get("metadata")
        metadata_paths = metadata.get("paths") if isinstance(metadata, dict) else None
        if isinstance(metadata_paths, (list, tuple)):
            return list(dict.fromkeys(str(value) for value in metadata_paths if value))[:100]
        return []

    @classmethod
    def _change_paths(cls, change: dict[str, Any]) -> list[str]:
        return [value for value in cls._raw_change_paths(change) if cls._plausible_path(value)]

    @staticmethod
    def _plausible_path(value: str) -> bool:
        clean = str(value or "").strip().replace("\\", "/")
        for prefix in ("*** Update File:", "*** Add File:", "*** Delete File:"):
            clean = clean.removeprefix(prefix).strip()
        clean = clean.removeprefix("a/").removeprefix("b/").removeprefix("./")
        if (
            not clean
            or len(clean) > 1024
            or clean == "/dev/null"
            or "://" in clean
            or "//" in clean
            or any(ord(character) < 32 for character in clean)
            or any(token in clean for token in ("@@", "{", "}", "`"))
        ):
            return False
        parts = PurePosixPath(clean).parts
        if not parts or any(part in {"", ".", ".."} or part.endswith("+") for part in parts):
            return False
        basename = parts[-1]
        known_extensionless = {
            "Dockerfile",
            "Makefile",
            "Procfile",
            "LICENSE",
            "README",
            "CMakeLists.txt",
        }
        if basename in known_extensionless or basename.startswith("."):
            return True
        if "." in basename:
            return bool(re.fullmatch(r"[^/\s]+\.[A-Za-z0-9][A-Za-z0-9._-]{0,31}", basename))
        return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,120}", basename))
