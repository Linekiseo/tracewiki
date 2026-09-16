from __future__ import annotations

import re

HIGH_CONFIDENCE_SECRET_PATTERNS = {
    "private_key": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
        r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        re.DOTALL,
    ),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "openai_api_key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "bearer_token": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{24,}={0,2}"),
    "generic_api_key": re.compile(
        r"(?i)(?P<prefix>\b(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[\"']?)"
        r"(?P<secret>[A-Za-z0-9._~+/-]{24,})"
    ),
}


def secret_findings(content: str) -> list[str]:
    """Return high-confidence secret categories without returning secret material."""
    return [
        name for name, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS.items() if pattern.search(content)
    ]


def redact_secrets(content: str) -> tuple[str, list[str]]:
    """Redact high-confidence credentials while preserving useful surrounding evidence."""
    redacted = content
    findings: list[str] = []
    for name, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS.items():
        if not pattern.search(redacted):
            continue
        findings.append(name)
        if name == "generic_api_key":
            category = name
            redacted = pattern.sub(
                lambda match, category=category: f"{match.group('prefix')}[REDACTED:{category}]",
                redacted,
            )
        else:
            redacted = pattern.sub(f"[REDACTED:{name}]", redacted)
    return redacted, findings
