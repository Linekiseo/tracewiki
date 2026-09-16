from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv

_CODE_ENGINE_VALUES = frozenset({"v1", "v2"})
_CODE_UNIT_BUILDER_VALUES = frozenset({"raw-v1", "ast-v2"})
_CODE_SEMANTIC_RESOLVER_VALUES = frozenset({"off", "scip-python"})
_CODE_RERANKER_VALUES = frozenset({"off", "profile"})
_CODE_CONTEXT_VALUES = frozenset({"snippet-v1", "structured-v2"})
_DEPLOYMENT_MODE_VALUES = frozenset({"development", "production"})
_CODE_PROFILE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _parse_code_enum(name: str, default: str, allowed: frozenset[str]) -> str:
    value = os.getenv(name, default)
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {choices}")
    return value


def _parse_code_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"{name} must be exactly 'true' or 'false'")


def _validate_code_profile(name: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string profile identifier")
    if _CODE_PROFILE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must fully match {_CODE_PROFILE_PATTERN.pattern!r}")
    return value


def _parse_code_profile(name: str, default: str) -> str:
    return _validate_code_profile(name, os.getenv(name, default))


def _parse_code_percentage(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name} must be an integer from 0 through 100")
    parsed = int(value)
    if not 0 <= parsed <= 100:
        raise ValueError(f"{name} must be an integer from 0 through 100")
    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_path: Path
    repository_cache: Path
    web_dir: Path
    allowed_local_roots: tuple[Path, ...]
    wiki_data_dir: Path | None = None
    codex_home: Path | None = None
    project_root: Path | None = None
    max_file_bytes: int = 1_500_000
    max_codex_session_bytes: int = 250_000_000
    max_codex_item_chars: int = 16_000
    embedding_dimensions: int = 384
    api_token: str | None = field(default=None, repr=False)
    mcp_token: str | None = field(default=None, repr=False)
    enforce_acl: bool = False
    deployment_mode: Literal["development", "production"] = "development"
    trusted_acl_refs: tuple[str, ...] = ()
    llm_base_url: str | None = None
    llm_api_key: str | None = field(default=None, repr=False)
    llm_model: str | None = None
    wiki_embedding_base_url: str | None = None
    wiki_embedding_api_key: str | None = field(default=None, repr=False)
    wiki_embedding_model: str | None = None
    wiki_embedding_dimension: int = 1536
    wiki_embedding_timeout_seconds: float = 15.0
    wiki_reranker_base_url: str | None = None
    wiki_reranker_api_key: str | None = field(default=None, repr=False)
    wiki_reranker_model: str | None = None
    wiki_reranker_timeout_seconds: float = 15.0
    wiki_external_inference_allowed: bool = False
    rag_code_engine: Literal["v1", "v2"] = "v1"
    rag_code_shadow: bool = False
    rag_code_unit_builder: Literal["raw-v1", "ast-v2"] = "raw-v1"
    rag_code_embedding_profile: str = "local-hash-v2"
    rag_code_dense_index: bool = True
    rag_code_graph: bool = False
    rag_code_semantic_resolver: Literal["off", "scip-python"] = "off"
    rag_code_reranker: Literal["off", "profile"] = "off"
    rag_code_context: Literal["snippet-v1", "structured-v2"] = "snippet-v1"
    rag_code_canary_percent: int = 0
    rag_multisource_calibration_bundle: Path | None = None
    rag_multisource_calibration_sha256: str | None = None
    rag_global_generator_prompt_v2: bool = True
    rag_global_context_packer_v2: bool = True
    rag_global_fusion_v2: bool = True
    rag_global_reranker_v2: bool = True
    rag_global_embedding_generation_v2: bool = True
    rag_global_source_retrievers_v2: bool = True
    rag_global_planner_v2: bool = True

    def __post_init__(self) -> None:
        enum_fields = (
            ("deployment_mode", self.deployment_mode, _DEPLOYMENT_MODE_VALUES),
            ("rag_code_engine", self.rag_code_engine, _CODE_ENGINE_VALUES),
            ("rag_code_unit_builder", self.rag_code_unit_builder, _CODE_UNIT_BUILDER_VALUES),
            (
                "rag_code_semantic_resolver",
                self.rag_code_semantic_resolver,
                _CODE_SEMANTIC_RESOLVER_VALUES,
            ),
            ("rag_code_reranker", self.rag_code_reranker, _CODE_RERANKER_VALUES),
            ("rag_code_context", self.rag_code_context, _CODE_CONTEXT_VALUES),
        )
        for name, value, allowed in enum_fields:
            if type(value) is not str or value not in allowed:
                choices = ", ".join(sorted(allowed))
                raise ValueError(f"{name} must be one of: {choices}")
        for name, value in (
            ("rag_code_shadow", self.rag_code_shadow),
            ("rag_code_dense_index", self.rag_code_dense_index),
            ("rag_code_graph", self.rag_code_graph),
            ("wiki_external_inference_allowed", self.wiki_external_inference_allowed),
            ("rag_global_generator_prompt_v2", self.rag_global_generator_prompt_v2),
            ("rag_global_context_packer_v2", self.rag_global_context_packer_v2),
            ("rag_global_fusion_v2", self.rag_global_fusion_v2),
            ("rag_global_reranker_v2", self.rag_global_reranker_v2),
            (
                "rag_global_embedding_generation_v2",
                self.rag_global_embedding_generation_v2,
            ),
            ("rag_global_source_retrievers_v2", self.rag_global_source_retrievers_v2),
            ("rag_global_planner_v2", self.rag_global_planner_v2),
        ):
            if type(value) is not bool:
                raise TypeError(f"{name} must be a bool")
        _validate_code_profile("rag_code_embedding_profile", self.rag_code_embedding_profile)
        if self.rag_code_semantic_resolver != "off" and (
            self.rag_code_unit_builder != "ast-v2" or not self.rag_code_graph
        ):
            raise ValueError(
                "rag_code_semantic_resolver requires rag_code_unit_builder='ast-v2' "
                "and rag_code_graph=True"
            )
        if type(self.rag_code_canary_percent) is not int:
            raise TypeError("rag_code_canary_percent must be an int")
        if not 0 <= self.rag_code_canary_percent <= 100:
            raise ValueError("rag_code_canary_percent must be between 0 and 100")
        if self.trusted_acl_refs != tuple(dict.fromkeys(self.trusted_acl_refs)):
            raise ValueError("trusted_acl_refs must be ordered and unique")
        if any(not item or item != item.strip() for item in self.trusted_acl_refs):
            raise ValueError("trusted_acl_refs must contain non-empty canonical values")
        if not 8 <= self.wiki_embedding_dimension <= 65_536:
            raise ValueError("wiki_embedding_dimension must be between 8 and 65536")
        for name, value in (
            ("wiki_embedding_timeout_seconds", self.wiki_embedding_timeout_seconds),
            ("wiki_reranker_timeout_seconds", self.wiki_reranker_timeout_seconds),
        ):
            if type(value) not in {int, float} or not 0.1 <= float(value) <= 120.0:
                raise ValueError(f"{name} must be between 0.1 and 120 seconds")
        self._validate_wiki_provider(
            "embedding",
            self.wiki_embedding_base_url,
            self.wiki_embedding_model,
        )
        self._validate_wiki_provider(
            "reranker",
            self.wiki_reranker_base_url,
            self.wiki_reranker_model,
        )
        if (self.rag_multisource_calibration_bundle is None) != (
            self.rag_multisource_calibration_sha256 is None
        ):
            raise ValueError(
                "reviewed multi-source calibration bundle and trust digest must be configured together"
            )
        if (
            self.rag_multisource_calibration_sha256 is not None
            and re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                self.rag_multisource_calibration_sha256,
            )
            is None
        ):
            raise ValueError("RAG_MULTISOURCE_CALIBRATION_SHA256 must be a canonical sha256")
        if self.deployment_mode == "production":
            if not self.api_token:
                raise ValueError("production deployment requires RAG_API_TOKEN")
            if not self.enforce_acl:
                raise ValueError("production deployment requires RAG_ENFORCE_ACL=true")
        wiki_root = self.resolved_wiki_data_dir
        if wiki_root == self.database_path or self.database_path.is_relative_to(wiki_root):
            raise ValueError("Wiki data directory cannot contain the formal evidence database")

    def _validate_wiki_provider(
        self,
        label: str,
        base_url: str | None,
        model: str | None,
    ) -> None:
        if (base_url is None) != (model is None):
            raise ValueError(f"Wiki {label} base URL and model must be configured together")
        if base_url is None:
            return
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"Wiki {label} base URL is not a canonical HTTP endpoint")
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if not loopback and not self.wiki_external_inference_allowed:
            raise ValueError(f"remote Wiki {label} requires explicit external inference approval")
        if self.deployment_mode == "production" and parsed.scheme != "https" and not loopback:
            raise ValueError(f"production Wiki {label} requires HTTPS")

    @classmethod
    def from_env(cls, *, base_dir: Path | None = None) -> Settings:
        base = (base_dir or Path.cwd()).resolve()
        load_dotenv(base / ".env", override=False)
        data_dir = _resolve_path(os.getenv("RAG_DATA_DIR", "./var"), base)
        raw_roots = os.getenv("RAG_ALLOWED_LOCAL_ROOTS", str(base))
        roots = tuple(
            _resolve_path(item.strip(), base) for item in raw_roots.split(",") if item.strip()
        )
        package_root = Path(__file__).resolve().parents[2]
        default_web_dir = package_root / "web"
        if not default_web_dir.is_dir() and (base / "web").is_dir():
            default_web_dir = base / "web"
        web_dir = _resolve_path(os.getenv("RAG_WEB_DIR", str(default_web_dir)), base)
        codex_home = _resolve_path(
            os.getenv("RAG_CODEX_HOME", os.getenv("CODEX_HOME", "~/.codex")), base
        )
        project_root = _resolve_path(os.getenv("RAG_PROJECT_ROOT", str(base)), base)
        trusted_acl_refs = tuple(
            dict.fromkeys(
                item.strip()
                for item in os.getenv("RAG_TRUSTED_ACL_REFS", "").split(",")
                if item.strip()
            )
        )
        calibration_path = os.getenv("RAG_MULTISOURCE_CALIBRATION_BUNDLE")
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "evidence-rag.sqlite3",
            repository_cache=data_dir / "repositories",
            web_dir=web_dir,
            allowed_local_roots=roots or (base,),
            wiki_data_dir=_resolve_path(
                os.getenv("RAG_WIKI_DATA_DIR", str(data_dir / "wiki")), base
            ),
            codex_home=codex_home,
            project_root=project_root,
            max_file_bytes=int(os.getenv("RAG_MAX_FILE_BYTES", "1500000")),
            max_codex_session_bytes=int(os.getenv("RAG_MAX_CODEX_SESSION_BYTES", "250000000")),
            max_codex_item_chars=int(os.getenv("RAG_MAX_CODEX_ITEM_CHARS", "16000")),
            embedding_dimensions=int(os.getenv("RAG_EMBEDDING_DIMENSIONS", "384")),
            api_token=os.getenv("RAG_API_TOKEN") or None,
            mcp_token=os.getenv("EVIDENCE_RAG_MCP_TOKEN") or None,
            enforce_acl=os.getenv("RAG_ENFORCE_ACL", "false").casefold()
            in {"1", "true", "yes", "on"},
            deployment_mode=_parse_code_enum(
                "RAG_DEPLOYMENT_MODE",
                "development",
                _DEPLOYMENT_MODE_VALUES,
            ),
            trusted_acl_refs=trusted_acl_refs,
            llm_base_url=os.getenv("RAG_LLM_BASE_URL") or None,
            llm_api_key=os.getenv("RAG_LLM_API_KEY") or None,
            llm_model=os.getenv("RAG_LLM_MODEL") or None,
            wiki_embedding_base_url=os.getenv("RAG_WIKI_EMBEDDING_BASE_URL") or None,
            wiki_embedding_api_key=os.getenv("RAG_WIKI_EMBEDDING_API_KEY") or None,
            wiki_embedding_model=os.getenv("RAG_WIKI_EMBEDDING_MODEL") or None,
            wiki_embedding_dimension=int(os.getenv("RAG_WIKI_EMBEDDING_DIMENSION", "1536")),
            wiki_embedding_timeout_seconds=float(
                os.getenv("RAG_WIKI_EMBEDDING_TIMEOUT_SECONDS", "15")
            ),
            wiki_reranker_base_url=os.getenv("RAG_WIKI_RERANKER_BASE_URL") or None,
            wiki_reranker_api_key=os.getenv("RAG_WIKI_RERANKER_API_KEY") or None,
            wiki_reranker_model=os.getenv("RAG_WIKI_RERANKER_MODEL") or None,
            wiki_reranker_timeout_seconds=float(
                os.getenv("RAG_WIKI_RERANKER_TIMEOUT_SECONDS", "15")
            ),
            wiki_external_inference_allowed=_parse_code_bool(
                "RAG_WIKI_EXTERNAL_INFERENCE_ALLOWED", False
            ),
            rag_code_engine=_parse_code_enum("RAG_CODE_ENGINE", "v1", _CODE_ENGINE_VALUES),
            rag_code_shadow=_parse_code_bool("RAG_CODE_SHADOW", False),
            rag_code_unit_builder=_parse_code_enum(
                "RAG_CODE_UNIT_BUILDER", "raw-v1", _CODE_UNIT_BUILDER_VALUES
            ),
            rag_code_embedding_profile=_parse_code_profile(
                "RAG_CODE_EMBEDDING_PROFILE", "local-hash-v2"
            ),
            rag_code_dense_index=_parse_code_bool("RAG_CODE_DENSE_INDEX", True),
            rag_code_graph=_parse_code_bool("RAG_CODE_GRAPH", False),
            rag_code_semantic_resolver=_parse_code_enum(
                "RAG_CODE_SEMANTIC_RESOLVER", "off", _CODE_SEMANTIC_RESOLVER_VALUES
            ),
            rag_code_reranker=_parse_code_enum("RAG_CODE_RERANKER", "off", _CODE_RERANKER_VALUES),
            rag_code_context=_parse_code_enum(
                "RAG_CODE_CONTEXT", "snippet-v1", _CODE_CONTEXT_VALUES
            ),
            rag_code_canary_percent=_parse_code_percentage("RAG_CODE_CANARY_PERCENT", 0),
            rag_multisource_calibration_bundle=(
                _resolve_path(calibration_path, base) if calibration_path else None
            ),
            rag_multisource_calibration_sha256=(
                os.getenv("RAG_MULTISOURCE_CALIBRATION_SHA256") or None
            ),
            rag_global_generator_prompt_v2=_parse_code_bool("RAG_GLOBAL_GENERATOR_PROMPT_V2", True),
            rag_global_context_packer_v2=_parse_code_bool("RAG_GLOBAL_CONTEXT_PACKER_V2", True),
            rag_global_fusion_v2=_parse_code_bool("RAG_GLOBAL_FUSION_V2", True),
            rag_global_reranker_v2=_parse_code_bool("RAG_GLOBAL_RERANKER_V2", True),
            rag_global_embedding_generation_v2=_parse_code_bool(
                "RAG_GLOBAL_EMBEDDING_GENERATION_V2", True
            ),
            rag_global_source_retrievers_v2=_parse_code_bool(
                "RAG_GLOBAL_SOURCE_RETRIEVERS_V2", True
            ),
            rag_global_planner_v2=_parse_code_bool("RAG_GLOBAL_PLANNER_V2", True),
        )

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.repository_cache.mkdir(parents=True, exist_ok=True)
        self.raw_object_dir.mkdir(parents=True, exist_ok=True)
        self.resolved_wiki_data_dir.mkdir(parents=True, exist_ok=True)

    def with_canonical_release_hold(self) -> Settings:
        """Remove config-authored production default promotion while held."""

        if self.rag_code_engine == "v1" and self.rag_code_canary_percent == 0:
            return self
        return replace(
            self,
            rag_code_engine="v1",
            rag_code_canary_percent=0,
        )

    @property
    def raw_object_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def resolved_wiki_data_dir(self) -> Path:
        return (self.wiki_data_dir or (self.data_dir / "wiki")).resolve()

    @property
    def wiki_database_path(self) -> Path:
        return self.resolved_wiki_data_dir / "wiki.sqlite3"
