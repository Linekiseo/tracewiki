from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, HTTPException, Request

if TYPE_CHECKING:
    from .runtime import Runtime


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


@dataclass(frozen=True, slots=True)
class AccessContext:
    enforced: bool
    acl_refs: tuple[str, ...]

    def allows(self, acl_ref: str | None) -> bool:
        return not self.enforced or acl_ref == "public" or acl_ref in self.acl_refs


def access_context(
    x_rag_acl_refs: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
    runtime: Runtime = Depends(get_runtime),
) -> AccessContext:
    refs = tuple(
        dict.fromkeys(item.strip() for item in (x_rag_acl_refs or "").split(",") if item.strip())
    )
    if runtime.settings.deployment_mode == "production":
        supplied = authorization or ""
        if supplied.lower().startswith("bearer "):
            supplied = supplied[7:]
        expected = runtime.settings.api_token or ""
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")
        trusted = set(runtime.settings.trusted_acl_refs)
        untrusted = set(refs) - trusted
        if untrusted:
            raise HTTPException(status_code=403, detail="untrusted ACL reference")
    return AccessContext(enforced=runtime.settings.enforce_acl, acl_refs=refs)


def require_project_access(runtime: Runtime, access: AccessContext, project_id: str) -> dict:
    project = runtime.workspace.store.get_project(project_id)
    if not project or not access.allows(project["acl_ref"]):
        raise HTTPException(status_code=404, detail="project not found")
    return project


def require_entity_access(runtime: Runtime, access: AccessContext, entity_id: str) -> None:
    acl_ref = runtime.platform.store.entity_acl_refs([entity_id]).get(entity_id)
    if not acl_ref or not access.allows(acl_ref):
        raise HTTPException(status_code=404, detail="entity not found")


def mutation_auth(
    authorization: Annotated[str | None, Header()] = None,
    runtime: Runtime = Depends(get_runtime),
) -> None:
    expected = runtime.settings.api_token
    if not expected:
        return
    supplied = authorization or ""
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:]
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")
