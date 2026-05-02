from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from testagent.common.errors import RAGIngestionError
from testagent.common.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_CHARS_PER_TOKEN = 4


@dataclass
class Chunk:
    doc_id: str
    chunk_id: str
    content: str
    metadata: dict[str, object]


class MarkdownChunker:
    _HEADING_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\n(?=## )")

    def chunk(self, content: str) -> list[Chunk]:
        sections = self._HEADING_PATTERN.split(content)
        chunks: list[Chunk] = []
        for i, section in enumerate(sections):
            stripped = section.strip()
            if stripped:
                chunks.append(
                    Chunk(
                        doc_id="",
                        chunk_id="",
                        content=stripped,
                        metadata={"chunk_type": "markdown", "position": i},
                    )
                )
        return chunks


class CodeChunker:
    _TOPLEVEL_DEF_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:(?:async\s+)?def\s+\w+|class\s+\w+)",
    )
    _DECORATOR_LINE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^@\w+")

    def chunk(self, content: str, language: str = "python") -> list[Chunk]:
        lines = content.split("\n")
        n = len(lines)

        def_indices: list[int] = []
        for i, line in enumerate(lines):
            if self._TOPLEVEL_DEF_PATTERN.match(line):
                def_indices.append(i)

        if not def_indices:
            stripped = content.strip()
            if stripped:
                return [
                    Chunk(
                        doc_id="",
                        chunk_id="",
                        content=stripped,
                        metadata={"chunk_type": "code", "language": language, "position": 0},
                    )
                ]
            return []

        groups: list[tuple[int, int]] = []
        for di in def_indices:
            start = di
            j = di - 1
            while j >= 0:
                stripped_line = lines[j].strip()
                if self._DECORATOR_LINE_PATTERN.match(stripped_line):
                    start = j
                    j -= 1
                elif stripped_line == "":
                    j -= 1
                else:
                    break
            groups.append((start, di))

        chunks: list[Chunk] = []
        position = 0

        first_start = groups[0][0]
        if first_start > 0:
            preamble = "\n".join(lines[:first_start]).strip()
            if preamble:
                chunks.append(
                    Chunk(
                        doc_id="",
                        chunk_id="",
                        content=preamble,
                        metadata={"chunk_type": "code", "language": language, "position": position},
                    )
                )
                position += 1

        for i, (start, _def_line) in enumerate(groups):
            end = groups[i + 1][0] if i + 1 < len(groups) else n
            block = "\n".join(lines[start:end]).strip()
            if block:
                chunks.append(
                    Chunk(
                        doc_id="",
                        chunk_id="",
                        content=block,
                        metadata={"chunk_type": "code", "language": language, "position": position},
                    )
                )
                position += 1

        return chunks


class TextChunker:
    def __init__(self, chars_per_token: int = _DEFAULT_CHARS_PER_TOKEN) -> None:
        self._chars_per_token = chars_per_token

    def chunk(self, content: str, chunk_size: int = 512, overlap: int = 64) -> list[Chunk]:
        if chunk_size <= 0:
            raise RAGIngestionError(
                f"chunk_size must be positive, got {chunk_size}",
                code="INVALID_CHUNK_SIZE",
            )
        if overlap < 0:
            raise RAGIngestionError(
                f"overlap must be non-negative, got {overlap}",
                code="INVALID_OVERLAP",
            )
        if overlap >= chunk_size:
            raise RAGIngestionError(
                f"overlap ({overlap}) must be less than chunk_size ({chunk_size})",
                code="OVERLAP_EXCEEDS_CHUNK_SIZE",
            )

        chunk_chars = chunk_size * self._chars_per_token
        overlap_chars = overlap * self._chars_per_token
        step = chunk_chars - overlap_chars

        chunks: list[Chunk] = []
        start = 0
        content_length = len(content)
        while start < content_length:
            end = min(start + chunk_chars, content_length)
            if end < content_length:
                newline_pos = content.rfind("\n", start, end)
                if newline_pos > start + chunk_chars // 2:
                    end = newline_pos + 1
            chunk_text = content[start:end]
            if chunk_text.strip():
                chunks.append(
                    Chunk(
                        doc_id="",
                        chunk_id="",
                        content=chunk_text,
                        metadata={"chunk_type": "text", "position": len(chunks)},
                    )
                )
            start += step
            if end >= content_length:
                break

        return chunks


class DocumentIngestor:
    _EXTENSION_MAP: ClassVar[dict[str, str]] = {
        ".md": "markdown",
        ".markdown": "markdown",
        ".py": "code",
        ".js": "code",
        ".ts": "code",
        ".tsx": "code",
        ".jsx": "code",
        ".go": "code",
        ".java": "code",
        ".rs": "code",
        ".cpp": "code",
        ".c": "code",
        ".h": "code",
        ".yaml": "markdown",
        ".yml": "markdown",
        ".json": "code",
        ".txt": "text",
    }

    _LANGUAGE_MAP: ClassVar[dict[str, str]] = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".go": "go",
        ".java": "java",
        ".rs": "rust",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
    }

    def __init__(self) -> None:
        self._markdown_chunker = MarkdownChunker()
        self._code_chunker = CodeChunker()
        self._text_chunker = TextChunker()

    async def ingest(self, source: str, collection: str, metadata: dict[str, object] | None = None) -> list[Chunk]:
        source_path = Path(source)
        if not source_path.exists():
            raise RAGIngestionError(
                f"Source file not found: {source}",
                code="SOURCE_NOT_FOUND",
                details={"source": source},
            )

        try:
            content = source_path.read_text(encoding="utf-8")
        except Exception as exc:
            raise RAGIngestionError(
                f"Failed to read source file {source}: {exc}",
                code="SOURCE_READ_ERROR",
                details={"source": source, "error": str(exc)},
            ) from exc

        if not content.strip():
            logger.warning("Source file is empty: %s", source)
            return []

        doc_id = self._generate_doc_id(source, collection)
        file_type = self._detect_type(source_path)
        base_meta = metadata.copy() if metadata else {}
        base_meta["source"] = source
        base_meta["collection"] = collection
        base_meta["file_type"] = file_type

        raw_chunks: list[Chunk]
        if file_type == "markdown":
            raw_chunks = self._markdown_chunker.chunk(content)
        elif file_type == "code":
            language = self._LANGUAGE_MAP.get(source_path.suffix.lower(), "python")
            raw_chunks = self._code_chunker.chunk(content, language=language)
        else:
            raw_chunks = self._text_chunker.chunk(content)

        result: list[Chunk] = []
        for i, chunk in enumerate(raw_chunks):
            merged_meta = {**base_meta, **chunk.metadata}
            chunk.doc_id = doc_id
            chunk.chunk_id = f"{doc_id}_chunk_{i:04d}"
            chunk.metadata = merged_meta
            result.append(chunk)

        logger.info(
            "Ingested %d chunks from %s into collection %s",
            len(result),
            source,
            collection,
            extra={
                "extra_data": {
                    "doc_id": doc_id,
                    "chunk_count": len(result),
                    "source": source,
                    "collection": collection,
                }
            },
        )
        return result

    @staticmethod
    def _generate_doc_id(source: str, collection: str) -> str:
        raw = f"{collection}:{source}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _detect_type(source_path: Path) -> str:
        suffix = source_path.suffix.lower()
        return DocumentIngestor._EXTENSION_MAP.get(suffix, "text")
