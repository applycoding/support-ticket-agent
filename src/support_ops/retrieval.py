"""Keyword retrieval over the small policy set."""

from pathlib import Path

from pydantic import BaseModel

from support_ops.util import tokenize


class Passage(BaseModel):
    """One policy section."""

    title: str
    text: str
    source: str
    score: int = 0


class PolicyRetriever:
    """Score policy sections by token overlap. No embeddings and no network."""

    def __init__(self, policy_dir: Path) -> None:
        if not policy_dir.is_dir():
            raise FileNotFoundError(f"Policy directory not found: {policy_dir}")
        self._passages = _load_passages(policy_dir)

    def search(self, query: str, k: int = 3) -> list[Passage]:
        """Return the best matching sections. Falls back to one section if nothing hits."""
        query_tokens = tokenize(query)
        ranked: list[Passage] = []
        for passage in self._passages:
            haystack = tokenize(f"{passage.title} {passage.text}")
            score = sum(haystack.count(token) for token in query_tokens)
            ranked.append(passage.model_copy(update={"score": score}))
        ranked.sort(key=lambda passage: (-passage.score, passage.title, passage.source))
        positive = [passage for passage in ranked if passage.score > 0]
        if positive:
            return positive[:k]
        return ranked[:1]


def _load_passages(policy_dir: Path) -> list[Passage]:
    passages: list[Passage] = []
    for path in sorted(policy_dir.glob("*.md")):
        passages.extend(_chunk_markdown(path.read_text(encoding="utf-8"), path.name))
    if not passages:
        raise FileNotFoundError(f"No policy markdown found in {policy_dir}")
    return passages


def _chunk_markdown(text: str, source: str) -> list[Passage]:
    doc_title = source
    sections: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("# "):
            doc_title = line[2:].strip()
            continue
        if line.startswith("## "):
            if current_title is not None:
                sections.append((current_title, current_lines))
            current_title = line[3:].strip()
            current_lines = []
            continue
        if current_title is not None:
            current_lines.append(line)
    if current_title is not None:
        sections.append((current_title, current_lines))
    passages: list[Passage] = []
    for title, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        passages.append(Passage(title=f"{doc_title} / {title}", text=body, source=source, score=0))
    return passages
