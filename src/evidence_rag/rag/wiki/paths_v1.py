"""Canonical logical paths and physical keys for the agent-native Wiki."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from urllib.parse import unquote

WIKI_PATH_VERSION = "wiki-logical-path-v1"
WIKI_INTENT_DIRECTORIES = (
    "architecture",
    "capabilities",
    "changes",
    "components",
    "decisions",
    "experiments",
    "findings",
    "issues",
    "procedures",
    "requirements",
    "sources",
)

_SEGMENT_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_UNSAFE_PATH_RE = re.compile(r"(?:%|\\|\.{1,2}(?:/|$)|//|[\x00-\x1f\x7f])")


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def validate_wiki_logical_path_v1(path: str, *, allow_root: bool = True) -> str:
    """Validate and return a canonical generation-independent Wiki path."""

    if path == "/":
        if allow_root:
            return path
        raise ValueError("Wiki root path is not allowed here")
    if not path.startswith("/") or path.endswith("/") or _UNSAFE_PATH_RE.search(path):
        raise ValueError("Wiki logical path is not canonical")
    segments = path[1:].split("/")
    if not segments or len(segments) > 12:
        raise ValueError("Wiki logical path depth is invalid")
    if segments[0] not in WIKI_INTENT_DIRECTORIES:
        raise ValueError("Wiki logical path must begin with a reviewed intent directory")
    if any(not _SEGMENT_RE.fullmatch(segment) for segment in segments):
        raise ValueError("Wiki logical path contains a non-canonical segment")
    if unquote(path) != path:
        raise ValueError("Wiki logical path cannot contain encoded aliases")
    return path


def wiki_slug_v1(label: str, *, stable_identity: str) -> str:
    """Create a readable, collision-resistant stable slug.

    The stable suffix is intentional: display names can be renamed or normalize to the
    same ASCII form, while the logical knowledge address must remain unambiguous.
    """

    normalized = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    stem = stem[:45].rstrip("-") or "item"
    suffix = hashlib.sha256(stable_identity.encode("utf-8")).hexdigest()[:12]
    slug = f"{stem}-{suffix}"
    if not _SEGMENT_RE.fullmatch(slug):
        raise ValueError("Wiki slug construction produced an invalid segment")
    return slug


def wiki_logical_path_v1(intent: str, *segments: str) -> str:
    if intent not in WIKI_INTENT_DIRECTORIES:
        raise ValueError("Unknown Wiki intent directory")
    path = "/" + "/".join((intent, *segments))
    return validate_wiki_logical_path_v1(path, allow_root=False)


def wiki_parent_path_v1(path: str) -> str:
    canonical = validate_wiki_logical_path_v1(path)
    if canonical == "/":
        return "/"
    segments = canonical[1:].split("/")
    return "/" if len(segments) == 1 else "/" + "/".join(segments[:-1])


def wiki_ancestor_paths_v1(path: str) -> tuple[str, ...]:
    canonical = validate_wiki_logical_path_v1(path)
    if canonical == "/":
        return ("/",)
    segments = canonical[1:].split("/")
    return ("/",) + tuple("/" + "/".join(segments[:index]) for index in range(1, len(segments) + 1))


def wiki_record_physical_key_v1(
    *,
    project_id: str,
    generation_id: str,
    visibility_partition: str,
    logical_path: str,
    record_kind: str,
) -> str:
    """Return the immutable content namespace key for a Wiki record."""

    validate_wiki_logical_path_v1(logical_path)
    if not project_id or not generation_id or not visibility_partition or not record_kind:
        raise ValueError("Wiki physical key identity is incomplete")
    digest = _canonical_sha256(
        {
            "generation_id": generation_id,
            "logical_path": logical_path,
            "path_version": WIKI_PATH_VERSION,
            "project_id": project_id,
            "record_kind": record_kind,
            "visibility_partition": visibility_partition,
        }
    )
    if not _SHA256_RE.fullmatch(digest):
        raise AssertionError("Wiki physical key digest is malformed")
    return f"wiki-record:{digest.removeprefix('sha256:')}"
