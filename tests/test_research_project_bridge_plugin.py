from __future__ import annotations

import json
import re
from pathlib import Path

from evidence_rag.codex_bridge.service import CodexBridgeService

PLUGIN_ROOT = Path(__file__).parents[1] / "plugins" / "research-project-bridge"
PLUGIN_VERSION = "0.2.0+wiki.20260803"
EXPECTED_SKILLS = {
    "execute-research-development",
    "manage-research-project",
    "navigate-agent-native-wiki",
    "review-research-evidence",
}
WIKI_TOOLS = {
    "wiki_navigation_status",
    "wiki_search",
    "wiki_read",
    "wiki_follow",
    "wiki_navigate",
    "evidence_read",
    "wiki_propose_patch",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _frontmatter_name(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(?P<header>.*?)\n---\n", text, flags=re.DOTALL)
    assert match is not None, path
    names = re.findall(r"^name:\s*([^\n]+)$", match.group("header"), flags=re.MULTILINE)
    descriptions = re.findall(
        r"^description:\s*([^\n]+)$", match.group("header"), flags=re.MULTILINE
    )
    assert len(names) == 1, path
    assert len(descriptions) == 1 and len(descriptions[0].strip()) >= 40, path
    return names[0].strip()


def test_plugin_manifest_mcp_and_skills_are_one_versioned_contract() -> None:
    manifest = _load_json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json")
    mcp = _load_json(PLUGIN_ROOT / ".mcp.json")

    assert manifest["name"] == "research-project-bridge"
    assert manifest["version"] == PLUGIN_VERSION
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["interface"]["capabilities"] == ["Read", "Write", "MCP"]
    assert CodexBridgeService.MCP_SERVER_VERSION == "0.2.0"

    assert set(mcp) == {"mcpServers"}
    bridge = mcp["mcpServers"]["research-project-bridge"]
    assert bridge == {
        "type": "http",
        "url": "http://127.0.0.1:8000/mcp",
        "bearer_token_env_var": "EVIDENCE_RAG_MCP_TOKEN",
        "required": False,
        "startup_timeout_sec": 10,
        "tool_timeout_sec": 120,
        "disabled_tools": [],
    }
    serialized_mcp = json.dumps(mcp).lower()
    assert '"token"' not in serialized_mcp
    assert '"secret"' not in serialized_mcp
    assert '"api_key"' not in serialized_mcp
    assert '"authorization"' not in serialized_mcp

    skill_files = sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))
    assert {_frontmatter_name(path) for path in skill_files} == EXPECTED_SKILLS
    for path in skill_files:
        assert path.parent.name == _frontmatter_name(path)


def test_plugin_skill_tool_references_are_server_capabilities() -> None:
    skill_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))
    )
    referenced = {
        token
        for token in re.findall(r"`([a-z][a-z0-9_]+)`", skill_text)
        if token.startswith(("wiki_", "evidence_", "research_", "execution_", "project_"))
    }
    assert referenced >= WIKI_TOOLS
    assert referenced <= set(CodexBridgeService.CAPABILITIES)


def test_plugin_bundle_contains_no_local_path_or_embedded_secret() -> None:
    bundle = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(PLUGIN_ROOT.rglob("*"))
        if path.is_file()
    )
    assert "/Users/" not in bundle
    assert "file://" not in bundle
    assert "sk-" not in bundle
    assert "Bearer " not in bundle
    assert "EVIDENCE_RAG_MCP_TOKEN" in bundle
