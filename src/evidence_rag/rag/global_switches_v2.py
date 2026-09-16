"""Operational component switches for the reviewed seven-step M7 rollback."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

GLOBAL_COMPONENT_SWITCHES_VERSION = "global-component-switches-v2"
GLOBAL_COMPONENT_NAMES_V2 = (
    "generator_prompt",
    "context_packer",
    "fusion",
    "reranker",
    "embedding_generation",
    "source_retrievers",
    "planner",
)


class GlobalComponentSwitchesV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    generator_prompt: bool = True
    context_packer: bool = True
    fusion: bool = True
    reranker: bool = True
    embedding_generation: bool = True
    source_retrievers: bool = True
    planner: bool = True

    @property
    def disabled_components(self) -> tuple[str, ...]:
        return tuple(name for name in GLOBAL_COMPONENT_NAMES_V2 if not getattr(self, name))

    @property
    def rollback_blocker(self) -> str | None:
        """Return the first deterministic blocker for an incomplete V2 stack."""

        disabled = self.disabled_components
        return f"component_disabled:{disabled[0]}" if disabled else None

    def operational_snapshot(self) -> dict[str, object]:
        """Expose independent component state without deployment configuration."""

        components = tuple(
            (
                name,
                "enabled" if getattr(self, name) else "v1_fallback",
            )
            for name in GLOBAL_COMPONENT_NAMES_V2
        )
        return {
            "version": GLOBAL_COMPONENT_SWITCHES_VERSION,
            "components": components,
            "disabled_count": len(self.disabled_components),
        }


__all__ = [
    "GLOBAL_COMPONENT_NAMES_V2",
    "GLOBAL_COMPONENT_SWITCHES_VERSION",
    "GlobalComponentSwitchesV2",
]
