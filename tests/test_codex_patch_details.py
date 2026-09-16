import json

from evidence_rag.codex_store import _decode_apply_patch, _split_apply_patch


def test_apply_patch_payload_is_recovered_and_given_real_line_numbers() -> None:
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: /workspace/src/ranker.py",
            "@@",
            " def rank(items):",
            "-    return sorted(items)",
            "+    ranked = sorted(items)",
            "+    return ranked",
            "*** End Patch",
        ]
    )
    stored = json.dumps({"value": f"const patch = {json.dumps(patch)};"})

    decoded = _decode_apply_patch(stored)
    assert decoded == patch

    details = _split_apply_patch(
        decoded,
        normalize_path=lambda value: value.removeprefix("/workspace/"),
        after_content_by_path={
            "src/ranker.py": ("def rank(items):\n    ranked = sorted(items)\n    return ranked\n")
        },
    )

    assert details["src/ranker.py"]["patch"].startswith("@@ -1,2 +1,3 @@")
    assert details["src/ranker.py"]["additions"] == 2
    assert details["src/ranker.py"]["deletions"] == 1
    assert details["src/ranker.py"]["hunk_count"] == 1
    assert details["src/ranker.py"]["patch_format"] == "recovered"


def test_added_file_patch_has_addressable_new_lines() -> None:
    details = _split_apply_patch(
        "*** Begin Patch\n"
        "*** Add File: /workspace/src/new.py\n"
        "+first = 1\n"
        "+second = 2\n"
        "*** End Patch",
        normalize_path=lambda value: value.removeprefix("/workspace/"),
        after_content_by_path={},
    )

    assert details["src/new.py"]["patch"] == ("@@ -0,0 +1,2 @@\n+first = 1\n+second = 2")
