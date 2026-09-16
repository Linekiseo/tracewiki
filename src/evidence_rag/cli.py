from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from .models import (
    CodexIngestRequest,
    CodexSearchRequest,
    EvidenceSearchRequest,
    RepositoryIngestRequest,
)
from .rag.release_control_plane_v2 import (
    current_runtime_release_status_v2,
    verify_exact_release_package_v2,
)
from .runtime import create_runtime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evidence-rag")
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="Run the API and web console")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    serve.add_argument("--reload", action="store_true")

    index = subcommands.add_parser("index", help="Index a local folder or remote Git URL")
    index.add_argument("source")
    index.add_argument("--branch")
    index.add_argument("--project", default="project-rag")
    index.add_argument("--acl", default="project:project-rag")
    index.add_argument("--ignore", action="append", default=[])

    search = subcommands.add_parser("search", help="Hybrid-search indexed code evidence")
    search.add_argument("query")
    search.add_argument("--repository", action="append", default=[])
    search.add_argument("--limit", type=int, default=10)

    codex_index = subcommands.add_parser("index-codex", help="Index Codex session JSONL")
    codex_index.add_argument("--source")
    codex_index.add_argument("--project-path")
    codex_index.add_argument("--project", default="project-rag")
    codex_index.add_argument("--acl", default="project:project-rag")
    codex_index.add_argument("--include-archived", action="store_true")
    codex_index.add_argument("--max-sessions", type=int, default=200)

    codex_search = subcommands.add_parser(
        "search-codex", help="Hybrid-search indexed Codex evidence"
    )
    codex_search.add_argument("query")
    codex_search.add_argument("--thread", action="append", default=[])
    codex_search.add_argument("--item-type", action="append", default=[])
    codex_search.add_argument("--limit", type=int, default=10)

    subcommands.add_parser(
        "status",
        help="Verify and print canonical release status without running RAG",
    )

    package = subcommands.add_parser("package", help="Verify portable release packages")
    package_subcommands = package.add_subparsers(dest="package_command", required=True)
    package_verify = package_subcommands.add_parser(
        "verify",
        help="Verify exact package bytes without publishing or switching configuration",
    )
    package_verify.add_argument("package_dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        project_root = Path(__file__).resolve().parents[2]
        reload_dirs = [
            str(path) for path in (project_root / "src", project_root / "web") if path.is_dir()
        ]
        uvicorn.run(
            "evidence_rag.api:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            reload_dirs=reload_dirs if args.reload else None,
        )
        return

    if args.command == "status":
        result = current_runtime_release_status_v2()
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return

    if args.command == "package" and args.package_command == "verify":
        try:
            result = verify_exact_release_package_v2(args.package_dir.resolve())
        except (OSError, ValueError):
            print(
                json.dumps(
                    {
                        "status": "INVALID",
                        "verify_only": True,
                        "reason_code": "package-verification-failed",
                    }
                ),
                file=sys.stderr,
            )
            raise SystemExit(2) from None
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return

    runtime = create_runtime()
    if args.command == "index":
        request = RepositoryIngestRequest(
            source=args.source,
            branch=args.branch,
            project_id=args.project,
            acl_ref=args.acl,
            ignore=args.ignore,
        )
        workflow_id = runtime.ingestion.enqueue(request)
        runtime.ingestion.run(workflow_id, request)
        print(json.dumps(runtime.store.get_workflow(workflow_id), ensure_ascii=False, indent=2))
        return

    if args.command == "index-codex":
        request = CodexIngestRequest(
            source=args.source,
            project_path=args.project_path,
            project_id=args.project,
            acl_ref=args.acl,
            include_archived=args.include_archived,
            max_sessions=args.max_sessions,
        )
        workflow_id = runtime.codex_ingestion.enqueue(request)
        runtime.codex_ingestion.run(workflow_id, request)
        print(json.dumps(runtime.store.get_workflow(workflow_id), ensure_ascii=False, indent=2))
        return

    if args.command == "search-codex":
        request = CodexSearchRequest(
            query=args.query,
            scope={"thread_ids": args.thread, "item_types": args.item_type},
            limit=args.limit,
        )
        print(json.dumps(runtime.codex_retriever.search(request), ensure_ascii=False, indent=2))
        return

    request = EvidenceSearchRequest(
        query=args.query,
        scope={"repository_ids": args.repository},
        limit=args.limit,
    )
    print(json.dumps(runtime.retriever.search(request), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
