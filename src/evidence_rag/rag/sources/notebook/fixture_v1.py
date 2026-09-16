"""Deterministic programmatic Notebook Golden fixture.

The fixture is built through :class:`NotebookAdapterV2`; tests do not hand-author derived
entities.  It contains multiple templates, revisions, executions, a failed execution,
a successful retry, stable native cell ids across a moved revision, stale output, binary
output omission, typed parameters, and same-name distractors.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, Self

from pydantic import BaseModel, ConfigDict

from .adapter_v2 import NotebookAdapterV2
from .contracts import NotebookPublication, canonical_sha256

NOTEBOOK_GOLDEN_DATASET_ID = "notebook-golden-v1"
NOTEBOOK_GOLDEN_DATASET_VERSION = "v1"
NOTEBOOK_GOLDEN_PROJECT_ID = "project-notebook-golden-v1"
NOTEBOOK_GOLDEN_ACL_REF = "public"
NOTEBOOK_GOLDEN_GENERATION_ID = "nbgen-golden-v1"
NOTEBOOK_FIXTURE_RECIPE_VERSION = "notebook-programmatic-fixture-v1"


class _FrozenFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class NotebookFixtureEntity(_FrozenFixture):
    entity_id: str
    entity_type: str
    locator: str
    publication_id: str
    revision_id: str | None = None
    execution_id: str | None = None
    content_sha256: str
    execution_order: int | None = None
    stale: bool = False


class NotebookFixtureAlias(_FrozenFixture):
    name: str
    entity_id: str


class NotebookFixtureBundle(_FrozenFixture):
    recipe_version: str = NOTEBOOK_FIXTURE_RECIPE_VERSION
    dataset_id: str = NOTEBOOK_GOLDEN_DATASET_ID
    dataset_version: str = NOTEBOOK_GOLDEN_DATASET_VERSION
    publications: tuple[NotebookPublication, ...]
    entities: tuple[NotebookFixtureEntity, ...]
    aliases: tuple[NotebookFixtureAlias, ...]
    recipe_sha256: str

    def alias_map(self) -> dict[str, str]:
        return {item.name: item.entity_id for item in self.aliases}

    def entity_map(self) -> dict[str, NotebookFixtureEntity]:
        return {item.entity_id: item for item in self.entities}


def _output_stream(text: str) -> dict[str, Any]:
    return {"output_type": "stream", "name": "stdout", "text": [text]}


def _base_cells(*, failed: bool) -> list[dict[str, Any]]:
    error_output: dict[str, Any]
    if failed:
        error_output = {
            "output_type": "error",
            "ename": "RuntimeError",
            "evalue": "temporary service failure",
            "traceback": ["RuntimeError: temporary service failure"],
        }
    else:
        error_output = _output_stream("retry succeeded\n")
    return [
        {
            "id": "parameters",
            "cell_type": "code",
            "metadata": {"tags": ["parameters"]},
            "source": [
                "seed = 7\n",
                "threshold = 0.75\n",
                "dataset = 'train-v2'\n",
            ],
            "execution_count": 1,
            "outputs": [],
        },
        {
            "id": "load-data",
            "cell_type": "code",
            "metadata": {},
            "source": ["records = [1, 2, 3, 4]\n"],
            "execution_count": 2,
            "outputs": [],
        },
        {
            "id": "score",
            "cell_type": "code",
            "metadata": {},
            "source": ["score = sum(records) / len(records)\n", "print(score)\n"],
            "execution_count": 4,
            "outputs": [_output_stream("2.5\n")],
        },
        {
            "id": "table",
            "cell_type": "code",
            "metadata": {},
            "source": ["summary = {'score': score, 'threshold': threshold}\n", "summary\n"],
            "execution_count": 5,
            "outputs": [
                {
                    "output_type": "execute_result",
                    "execution_count": 5,
                    "data": {
                        "text/plain": ["{'score': 2.5, 'threshold': 0.75}"],
                        "text/html": [
                            "<script>steal()</script><table><tr><td>2.5</td></tr></table>"
                        ],
                    },
                    "metadata": {},
                }
            ],
        },
        {
            "id": "figure",
            "cell_type": "code",
            "metadata": {},
            "source": ["plot_score(summary)\n"],
            "execution_count": 6,
            "outputs": [
                {
                    "output_type": "display_data",
                    "data": {
                        "text/plain": ["<Figure size 640x480>"],
                        "image/png": "not-a-real-image",
                    },
                    "metadata": {},
                }
            ],
        },
        {
            "id": "error-or-retry",
            "cell_type": "code",
            "metadata": {},
            "source": ["result = external_service(records)\n"],
            "execution_count": 7,
            "outputs": [error_output],
        },
        {
            "id": "stale-output",
            "cell_type": "code",
            "metadata": {},
            "source": ["old_score = 0.1\n"],
            "execution_count": None,
            "outputs": [_output_stream("0.1 from an old kernel\n")],
        },
        {
            "id": "never-ran",
            "cell_type": "code",
            "metadata": {},
            "source": ["future_score = score * 2\n"],
            "execution_count": None,
            "outputs": [],
        },
        {
            "id": "narrative",
            "cell_type": "markdown",
            "metadata": {},
            "source": ["The `score` variable is discussed here but not defined here."],
        },
        {
            "id": "external-dependency",
            "cell_type": "code",
            "metadata": {},
            "source": ["raw = open('/Users/example/private/data.csv').read()\n"],
            "execution_count": 3,
            "outputs": [],
        },
        {
            "id": "metric-like",
            "cell_type": "code",
            "metadata": {},
            "source": ["print('validation_accuracy=0.91')\n"],
            "execution_count": 8,
            "outputs": [_output_stream("validation_accuracy=0.91\n")],
        },
    ]


def _notebook_payload(
    cells: list[dict[str, Any]],
    *,
    start: str,
    end: str,
    extra_metadata: dict[str, Any] | None = None,
) -> bytes:
    metadata: dict[str, Any] = {
        "kernelspec": {"name": "python3"},
        "language_info": {"name": "python"},
        "papermill": {"start_time": start, "end_time": end},
    }
    metadata.update(extra_metadata or {})
    return json.dumps(
        {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": metadata,
            "cells": cells,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _publications() -> tuple[NotebookPublication, ...]:
    adapter = NotebookAdapterV2()
    failed_cells = _base_cells(failed=True)
    retry_cells = _base_cells(failed=False)
    moved_cells = [
        failed_cells[0],
        {
            "id": "new-quality-check",
            "cell_type": "code",
            "metadata": {},
            "source": ["quality_gate = score >= threshold\n"],
            "execution_count": 9,
            "outputs": [_output_stream("True\n")],
        },
        failed_cells[1],
        failed_cells[3],
        failed_cells[2],
        *failed_cells[4:],
    ]
    distractor_cells = [
        {
            "id": "parameters",
            "cell_type": "code",
            "metadata": {"tags": ["parameters"]},
            "source": ["seed = '7'\n", "threshold = '0.75'\n"],
            "execution_count": 1,
            "outputs": [],
        },
        {
            "id": "score",
            "cell_type": "code",
            "metadata": {},
            "source": ["score = 'not-the-training-score'\n"],
            "execution_count": None,
            "outputs": [],
        },
        {
            "id": "narrative",
            "cell_type": "markdown",
            "metadata": {},
            "source": ["A score and threshold are mentioned without executable evidence."],
        },
    ]
    specs = (
        (
            "training.ipynb",
            "revision-1",
            "execution-failed",
            _notebook_payload(
                failed_cells,
                start="2026-07-01T10:00:00Z",
                end="2026-07-01T10:00:10Z",
                extra_metadata={"experiment_run_id": "experiment-run-training"},
            ),
        ),
        (
            "training.ipynb",
            "revision-1",
            "execution-retry",
            _notebook_payload(
                retry_cells,
                start="2026-07-01T10:01:00Z",
                end="2026-07-01T10:01:08Z",
                extra_metadata={"experiment_run_id": "experiment-run-training"},
            ),
        ),
        (
            "training.ipynb",
            "revision-2",
            "execution-moved",
            _notebook_payload(
                moved_cells,
                start="2026-07-02T11:00:00Z",
                end="2026-07-02T11:00:11Z",
                extra_metadata={"experiment_run_id": "experiment-run-training-v2"},
            ),
        ),
        (
            "distractor.ipynb",
            "revision-1",
            "execution-distractor",
            _notebook_payload(
                distractor_cells,
                start="2026-07-03T12:00:00Z",
                end="2026-07-03T12:00:02Z",
            ),
        ),
    )
    return tuple(
        adapter.build_publication(
            payload=payload,
            project_id=NOTEBOOK_GOLDEN_PROJECT_ID,
            acl_ref=NOTEBOOK_GOLDEN_ACL_REF,
            generation_id=NOTEBOOK_GOLDEN_GENERATION_ID,
            source_key=source_key,
            source_version=source_version,
            execution_key=execution_key,
        )
        for source_key, source_version, execution_key, payload in specs
    )


def _entities(publications: Iterable[NotebookPublication]) -> tuple[NotebookFixtureEntity, ...]:
    rows: list[NotebookFixtureEntity] = []
    for publication in publications:
        revision = publication.revision
        execution = publication.execution
        rows.extend(
            (
                NotebookFixtureEntity(
                    entity_id=revision.revision_id,
                    entity_type="NotebookRevision",
                    locator=revision.locator,
                    publication_id=publication.publication_id,
                    revision_id=revision.revision_id,
                    content_sha256=revision.content_sha256,
                ),
                NotebookFixtureEntity(
                    entity_id=execution.execution_id,
                    entity_type="NotebookExecution",
                    locator=execution.locator,
                    publication_id=publication.publication_id,
                    revision_id=revision.revision_id,
                    execution_id=execution.execution_id,
                    content_sha256=execution.content_sha256,
                ),
            )
        )
        execution_by_cell = {item.cell_version_id: item for item in publication.cell_executions}
        for cell in publication.cell_versions:
            observed = execution_by_cell[cell.cell_version_id]
            rows.append(
                NotebookFixtureEntity(
                    entity_id=cell.cell_version_id,
                    entity_type="NotebookCellVersion",
                    locator=cell.locator,
                    publication_id=publication.publication_id,
                    revision_id=revision.revision_id,
                    execution_id=execution.execution_id,
                    content_sha256=cell.source_sha256,
                    execution_order=observed.execution_order,
                    stale=observed.stale,
                )
            )
        for observed in publication.cell_executions:
            rows.append(
                NotebookFixtureEntity(
                    entity_id=observed.cell_execution_id,
                    entity_type="NotebookCellExecution",
                    locator=observed.locator,
                    publication_id=publication.publication_id,
                    revision_id=revision.revision_id,
                    execution_id=execution.execution_id,
                    content_sha256=canonical_sha256(observed.model_dump(mode="json")),
                    execution_order=observed.execution_order,
                    stale=observed.stale,
                )
            )
        for parameter in publication.parameters:
            rows.append(
                NotebookFixtureEntity(
                    entity_id=parameter.parameter_id,
                    entity_type="NotebookParameter",
                    locator=parameter.locator,
                    publication_id=publication.publication_id,
                    revision_id=revision.revision_id,
                    execution_id=execution.execution_id,
                    content_sha256=parameter.value_sha256,
                )
            )
        for artifact in publication.artifacts:
            rows.append(
                NotebookFixtureEntity(
                    entity_id=artifact.artifact_id,
                    entity_type=(
                        "NotebookError" if artifact.artifact_type == "error" else "NotebookOutput"
                    ),
                    locator=artifact.locator,
                    publication_id=publication.publication_id,
                    revision_id=revision.revision_id,
                    execution_id=execution.execution_id,
                    content_sha256=artifact.content_sha256,
                )
            )
    unique = {item.entity_id: item for item in rows}
    return tuple(unique[key] for key in sorted(unique))


def _alias_rows(publications: tuple[NotebookPublication, ...]) -> tuple[NotebookFixtureAlias, ...]:
    failed, retry, moved, distractor = publications

    def cells(publication: NotebookPublication) -> dict[str, str]:
        return {
            item.native_cell_id or item.stable_cell_id: item.cell_version_id
            for item in publication.cell_versions
        }

    def cell_execs(publication: NotebookPublication) -> dict[str, str]:
        versions = {item.cell_version_id: item.native_cell_id for item in publication.cell_versions}
        return {
            str(versions[item.cell_version_id]): item.cell_execution_id
            for item in publication.cell_executions
        }

    def artifacts(publication: NotebookPublication) -> dict[tuple[str, int], str]:
        versions = {item.cell_version_id: item.native_cell_id for item in publication.cell_versions}
        native_by_execution = {
            item.cell_execution_id: str(versions[item.cell_version_id])
            for item in publication.cell_executions
        }
        return {
            (native_by_execution[item.cell_execution_id], item.ordinal): item.artifact_id
            for item in publication.artifacts
        }

    aliases: dict[str, str] = {
        "revision.failed": failed.revision.revision_id,
        "revision.retry": retry.revision.revision_id,
        "revision.moved": moved.revision.revision_id,
        "execution.failed": failed.execution.execution_id,
        "execution.retry": retry.execution.execution_id,
        "execution.moved": moved.execution.execution_id,
        "execution.distractor": distractor.execution.execution_id,
    }
    for prefix, publication in (
        ("failed", failed),
        ("retry", retry),
        ("moved", moved),
        ("distractor", distractor),
    ):
        aliases.update(
            {
                f"cell.{prefix}.{native}": entity_id
                for native, entity_id in cells(publication).items()
            }
        )
        aliases.update(
            {
                f"cell-execution.{prefix}.{native}": entity_id
                for native, entity_id in cell_execs(publication).items()
            }
        )
        aliases.update(
            {
                f"artifact.{prefix}.{native}.{ordinal}": entity_id
                for (native, ordinal), entity_id in artifacts(publication).items()
            }
        )
        aliases.update(
            {
                f"parameter.{prefix}.{parameter.name}": parameter.parameter_id
                for parameter in publication.parameters
            }
        )
    return tuple(
        NotebookFixtureAlias(name=name, entity_id=entity_id)
        for name, entity_id in sorted(aliases.items())
    )


def build_notebook_fixture_v1() -> NotebookFixtureBundle:
    publications = _publications()
    entities = _entities(publications)
    aliases = _alias_rows(publications)
    recipe_payload = {
        "recipe_version": NOTEBOOK_FIXTURE_RECIPE_VERSION,
        "dataset_id": NOTEBOOK_GOLDEN_DATASET_ID,
        "dataset_version": NOTEBOOK_GOLDEN_DATASET_VERSION,
        "publication_ids": [item.publication_id for item in publications],
        "entity_ids": [item.entity_id for item in entities],
        "aliases": [item.model_dump(mode="json") for item in aliases],
    }
    return NotebookFixtureBundle(
        publications=publications,
        entities=entities,
        aliases=aliases,
        recipe_sha256=canonical_sha256(recipe_payload),
    )


__all__ = [
    "NOTEBOOK_FIXTURE_RECIPE_VERSION",
    "NOTEBOOK_GOLDEN_ACL_REF",
    "NOTEBOOK_GOLDEN_DATASET_ID",
    "NOTEBOOK_GOLDEN_DATASET_VERSION",
    "NOTEBOOK_GOLDEN_GENERATION_ID",
    "NOTEBOOK_GOLDEN_PROJECT_ID",
    "NotebookFixtureAlias",
    "NotebookFixtureBundle",
    "NotebookFixtureEntity",
    "build_notebook_fixture_v1",
]
