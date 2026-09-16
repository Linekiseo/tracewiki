from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class NotebookAdapter:
    adapter_version = "jupyter-ipynb-v1"

    def parse(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid notebook: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("cells"), list):
            raise ValueError("notebook must contain a cells array")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        papermill = metadata.get("papermill") if isinstance(metadata.get("papermill"), dict) else {}
        parameters = self._parameters(payload["cells"])
        cells = []
        for ordinal, raw in enumerate(payload["cells"]):
            if not isinstance(raw, dict):
                continue
            source = self._text(raw.get("source"))
            cell_metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            outputs = []
            for output_ordinal, output in enumerate(raw.get("outputs") or []):
                if not isinstance(output, dict):
                    continue
                data = output.get("data") if isinstance(output.get("data"), dict) else {}
                text = self._text(output.get("text"))
                if not text and "text/plain" in data:
                    text = self._text(data["text/plain"])
                outputs.append(
                    {
                        "ordinal": output_ordinal,
                        "output_type": str(output.get("output_type") or "display_data"),
                        "text_content": text[:100_000],
                        "data_types": sorted(data),
                        "error_name": output.get("ename"),
                        "error_value": output.get("evalue"),
                        "artifact_ref": None,
                        "metadata": {
                            "execution_count": output.get("execution_count"),
                            "binary_payload_omitted": any(key.startswith("image/") for key in data),
                        },
                    }
                )
            cells.append(
                {
                    "ordinal": ordinal,
                    "cell_type": str(raw.get("cell_type") or "raw"),
                    "source": source,
                    "source_hash": "sha256:" + hashlib.sha256(source.encode()).hexdigest(),
                    "execution_count": raw.get("execution_count"),
                    "tags": list(cell_metadata.get("tags") or []),
                    "metadata": cell_metadata,
                    "outputs": outputs,
                }
            )
        status = (
            "failed"
            if any(output.get("error_name") for cell in cells for output in cell["outputs"])
            else "completed"
        )
        return {
            "metadata": metadata,
            "kernel_name": (metadata.get("kernelspec") or {}).get("name"),
            "language": (metadata.get("language_info") or {}).get("name"),
            "parameters": parameters,
            "started_at": papermill.get("start_time"),
            "completed_at": papermill.get("end_time"),
            "status": status,
            "cells": cells,
        }

    @staticmethod
    def _parameters(cells: list[Any]) -> dict[str, Any]:
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            metadata = cell.get("metadata") if isinstance(cell.get("metadata"), dict) else {}
            if "parameters" not in (metadata.get("tags") or []):
                continue
            result: dict[str, Any] = {}
            source = NotebookAdapter._text(cell.get("source"))
            for line in source.splitlines():
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key.isidentifier():
                    result[key] = value.strip()
            return result
        return {}

    @staticmethod
    def _text(value: Any) -> str:
        if isinstance(value, list):
            return "".join(str(item) for item in value)
        return str(value or "")
