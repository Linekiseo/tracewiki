"""Experiment E0-01 Golden, fixture, evaluator, and future baseline contracts."""

from . import analysis_v2 as _analysis_v2
from . import baseline_v1 as _baseline_v1
from . import contracts_v2 as _contracts_v2
from . import evaluation_v1 as _evaluation_v1
from . import fixture_v1 as _fixture_v1
from . import governance_v2 as _governance_v2
from . import pipeline_v2 as _pipeline_v2
from . import query_v2 as _query_v2
from . import runtime_v2 as _runtime_v2
from . import semantic_v2 as _semantic_v2
from . import store_v2 as _store_v2

__all__ = [
    *_evaluation_v1.__all__,
    *_fixture_v1.__all__,
    *_baseline_v1.__all__,
    *_contracts_v2.__all__,
    *_store_v2.__all__,
    *_query_v2.__all__,
    *_analysis_v2.__all__,
    *_semantic_v2.__all__,
    *_governance_v2.__all__,
    *_pipeline_v2.__all__,
    *_runtime_v2.__all__,
]

for _module in (
    _evaluation_v1,
    _fixture_v1,
    _baseline_v1,
    _contracts_v2,
    _store_v2,
    _query_v2,
    _analysis_v2,
    _semantic_v2,
    _governance_v2,
    _pipeline_v2,
    _runtime_v2,
):
    for _name in _module.__all__:
        globals()[_name] = getattr(_module, _name)

del _module, _name
