from __future__ import annotations

import hashlib
import math
import re
import struct
from collections.abc import Iterable

TOKEN_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*|[\u3400-\u9fff]+|\d+(?:\.\d+)?",
    re.UNICODE,
)
QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "代码": ("code", "symbol", "implementation"),
    "实现": ("implementation", "code"),
    "向量化": ("embedding", "vector"),
    "向量": ("vector", "embedding"),
    "检索": ("retrieval", "search"),
    "查询": ("query", "search", "retrieval"),
    "多源": ("multi-source", "cross-source", "source"),
    "证据": ("evidence", "citation"),
    "验证": ("validation", "test", "evidence"),
    "测试": ("test", "validation"),
    "实验": ("experiment", "run"),
    "指标": ("metric",),
    "文档": ("document", "claim"),
    "会话": ("codex", "thread", "session"),
    "绑定": ("binding", "relation", "link"),
    "关系": ("relation", "edge", "graph"),
    "版本": ("version", "commit"),
    "分析": ("analysis",),
}


class LocalHashEmbedding:
    """Deterministic, dependency-free feature hashing for offline hybrid search.

    It is deliberately a baseline rather than a learned embedding model. The stable
    binary format makes it possible to rebuild or replace vectors later without
    changing entity identities.
    """

    model_id = "local-hash-v2"
    supported_model_ids = frozenset({"local-hash-v1", model_id})

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 32:
            raise ValueError("embedding dimensions must be at least 32")
        self.dimensions = dimensions

    def tokens(self, text: str, *, model_id: str | None = None) -> Iterable[str]:
        resolved_model = model_id or self.model_id
        if resolved_model not in self.supported_model_ids:
            raise ValueError(f"unsupported embedding model: {resolved_model}")
        lowered = text.casefold()
        expansions = {
            expansion
            for phrase, values in QUERY_EXPANSIONS.items()
            if phrase in lowered
            for expansion in values
        }
        yield from sorted(expansions)
        for token in TOKEN_RE.findall(text):
            normalized = token.casefold()
            yield normalized
            # Preserve identifier semantics while making snake/camel components searchable.
            for part in re.split(r"_+|(?<=[a-z0-9])(?=[A-Z])", token):
                if part and part.casefold() != normalized:
                    yield part.casefold()
            # Unicode tokenizers usually keep a complete CJK phrase as one token.  Character
            # n-grams make questions such as “当前实验准确率” overlap with evidence containing
            # “实验的准确率”, while v1 remains available for already-published generations.
            if resolved_model == self.model_id and re.fullmatch(r"[\u3400-\u9fff]+", token):
                for width in (2, 3):
                    if len(token) <= width:
                        continue
                    for offset in range(len(token) - width + 1):
                        yield token[offset : offset + width]

    def embed(self, text: str, *, model_id: str | None = None) -> bytes:
        values = [0.0] * self.dimensions
        for token in self.tokens(text, model_id=model_id):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            raw = int.from_bytes(digest, "little")
            index = raw % self.dimensions
            sign = 1.0 if raw & (1 << 63) else -1.0
            values[index] += sign
        norm = math.sqrt(sum(value * value for value in values))
        if norm:
            values = [value / norm for value in values]
        return struct.pack(f"<{self.dimensions}f", *values)

    def similarity(self, left: bytes, right: bytes) -> float:
        if len(left) != len(right):
            return 0.0
        count = len(left) // 4
        a = struct.unpack(f"<{count}f", left)
        b = struct.unpack(f"<{count}f", right)
        # Feature-hash vectors are normalized at write time.  Negative correlation is not
        # relevance, and orthogonal vectors must remain 0 instead of being inflated to 0.5.
        cosine = sum(x * y for x, y in zip(a, b, strict=True))
        return max(0.0, min(1.0, cosine))
