"""Non-persistent qualification contract for the product-level ASGI replay layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

PRODUCT_REPLAY_CONTRACT_VERSION = "rag-product-replay-v2"
PRODUCT_REPLAY_LAYER = "L3_ASGI_SMOKE_CONTRACT"


class ProductReplayQualificationStatus(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ProductReplayQualification:
    contract_version: str
    layer: str
    status: ProductReplayQualificationStatus
    required_trusted_cases: int
    available_trusted_cases: int
    quality_claim_permitted: bool
    label_derived_candidate_generation_permitted: bool
    persistent_eval_run_permitted: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


PRODUCT_REPLAY_QUALIFICATION = ProductReplayQualification(
    contract_version=PRODUCT_REPLAY_CONTRACT_VERSION,
    layer=PRODUCT_REPLAY_LAYER,
    status=ProductReplayQualificationStatus.UNAVAILABLE,
    required_trusted_cases=60,
    available_trusted_cases=0,
    quality_claim_permitted=False,
    label_derived_candidate_generation_permitted=False,
    persistent_eval_run_permitted=False,
    reason=(
        "No independently reviewed 60-case production replay dataset is available. "
        "This contract qualifies only isolated L3 ASGI smoke and HTTP behavior."
    ),
)


def product_replay_qualification() -> dict[str, Any]:
    """Return the static qualification boundary without reading datasets or creating runs."""

    return PRODUCT_REPLAY_QUALIFICATION.as_dict()


__all__ = [
    "PRODUCT_REPLAY_CONTRACT_VERSION",
    "PRODUCT_REPLAY_LAYER",
    "PRODUCT_REPLAY_QUALIFICATION",
    "ProductReplayQualification",
    "ProductReplayQualificationStatus",
    "product_replay_qualification",
]
