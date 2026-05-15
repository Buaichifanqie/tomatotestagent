from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from testagent.common.errors import RAGIngestionError
from testagent.rag.ingestion import (
    Chunk,
    CodeChunker,
    DocumentIngestor,
    MarkdownChunker,
    TextChunker,
)

_MARKDOWN_CONTENT = """# 主标题

前言内容, 这是第一段
## 功能概述

功能概述的详细说明
这里有多行描述
## 安装指南

### 子标题
安装步骤:
1. 第一步 2. 第二步
## API 参考

API 文档内容"""

_MARKDOWN_NO_HEADINGS = """这是纯文本内容,没有任何 ## 标题
只有一些段落"""

_PYTHON_CODE = '''
"""模块文档字符串"""

import os
import sys

GLOBAL_VAR = "value"


def helper():
    """辅助函数"""
    return True


class MyClass:
    """示例类"""

    def __init__(self, name: str) -> None:
        self.name = name

    def greet(self) -> str:
        return f"Hello, {self.name}"


async def async_handler(param: int) -> dict:
    """异步处理函数"""
    result = await fetch(param)
    return {"data": result}
'''

_PYTHON_CODE_NO_DEFS = """import os
import sys

x = 1
y = 2
print(x + y)
"""

_PYTHON_WITH_DECORATORS = '''
import functools

def outer_decorator(fn):
    return fn

@outer_decorator
def decorated_func():
    """带装饰器的函数"""
    pass


class Base:
    pass


@functools.lru_cache(maxsize=128)
def cached_func(key: str) -> int:
    """带缓存装饰器的函数"""
    return len(key)
'''

_TEXT_CONTENT = (
    "This is a long text that needs to be chunked into smaller pieces. "
    "It contains multiple sentences. Each sentence will be part of a chunk. "
    "The chunking algorithm should respect sentence boundaries when possible. "
    "This ensures that the chunks are semantically coherent. "
    "Overlap between chunks helps maintain context across chunk boundaries. "
    "This is very important for RAG systems that need to retrieve relevant passages. "
    "Without overlap, important context could be lost at the boundaries. "
    "The chunking should be deterministic and reproducible. "
    "This means that given the same input, the same chunks should be produced. " * 20
)


class TestMarkdownChunker:
    def test_chunk_by_headings(self) -> None:
        chunker = MarkdownChunker()
        chunks = chunker.chunk(_MARKDOWN_CONTENT)

        assert len(chunks) == 4
        assert chunks[0].metadata["chunk_type"] == "markdown"
        assert "# 主标题" in chunks[0].content
        assert "前言内容" in chunks[0].content

        assert "## 功能概述" in chunks[1].content
        assert "功能概述的详细说明" in chunks[1].content

        assert "## 安装指南" in chunks[2].content
        assert "### 子标题" in chunks[2].content
        assert "1. 第一步" in chunks[2].content

        assert "## API 参考" in chunks[3].content
        assert "API 文档内容" in chunks[3].content

    def test_chunk_positions(self) -> None:
        chunker = MarkdownChunker()
        chunks = chunker.chunk(_MARKDOWN_CONTENT)

        assert chunks[0].metadata["position"] == 0
        assert chunks[1].metadata["position"] == 1
        assert chunks[2].metadata["position"] == 2
        assert chunks[3].metadata["position"] == 3

    def test_chunk_no_headings(self) -> None:
        chunker = MarkdownChunker()
        chunks = chunker.chunk(_MARKDOWN_NO_HEADINGS)

        assert len(chunks) == 1
        assert "纯文本内容" in chunks[0].content
        assert chunks[0].metadata["chunk_type"] == "markdown"
        assert chunks[0].metadata["position"] == 0

    def test_chunk_empty_content(self) -> None:
        chunker = MarkdownChunker()
        chunks = chunker.chunk("")

        assert chunks == []

    def test_chunk_whitespace_only(self) -> None:
        chunker = MarkdownChunker()
        chunks = chunker.chunk("   \n\n  \n")

        assert chunks == []

    def test_chunk_preserves_subheadings(self) -> None:
        content = "## 章节一\n\n内容一\n\n### 子节\n\n内容二\n\n## 章节二\n\n内容二"
        chunker = MarkdownChunker()
        chunks = chunker.chunk(content)

        assert len(chunks) == 2
        assert "### 子节" in chunks[0].content
        assert "## 章节二" in chunks[1].content

    def test_chunk_single_heading(self) -> None:
        content = "## 唯一章节\n\n一些内容"
        chunker = MarkdownChunker()
        chunks = chunker.chunk(content)

        assert len(chunks) == 1
        assert "## 唯一章节" in chunks[0].content
        assert "一些内容" in chunks[0].content


class TestCodeChunker:
    def test_chunk_by_functions_and_classes(self) -> None:
        chunker = CodeChunker()
        chunks = chunker.chunk(_PYTHON_CODE, language="python")

        assert len(chunks) == 4

        assert "GLOBAL_VAR" in chunks[0].content
        assert chunks[0].metadata["chunk_type"] == "code"
        assert chunks[0].metadata["language"] == "python"

        assert "def helper()" in chunks[1].content
        assert "class MyClass" in chunks[2].content
        assert "async def async_handler" in chunks[3].content

    def test_chunk_language_metadata(self) -> None:
        chunker = CodeChunker()
        chunks = chunker.chunk(_PYTHON_CODE, language="python")

        for chunk in chunks:
            assert chunk.metadata["language"] == "python"
            assert chunk.metadata["chunk_type"] == "code"

    def test_chunk_no_definitions(self) -> None:
        chunker = CodeChunker()
        chunks = chunker.chunk(_PYTHON_CODE_NO_DEFS, language="python")

        assert len(chunks) == 1
        assert "x = 1" in chunks[0].content
        assert chunks[0].metadata["position"] == 0

    def test_chunk_empty_content(self) -> None:
        chunker = CodeChunker()
        chunks = chunker.chunk("", language="python")

        assert chunks == []

    def test_chunk_with_decorators(self) -> None:
        chunker = CodeChunker()
        chunks = chunker.chunk(_PYTHON_WITH_DECORATORS, language="python")

        decorated = [c for c in chunks if "decorated_func" in c.content]
        assert len(decorated) >= 1

    def test_chunk_with_class_methods(self) -> None:
        code = """
class Foo:
    def method_a(self):
        pass

    def method_b(self):
        pass
"""
        chunker = CodeChunker()
        chunks = chunker.chunk(code, language="python")

        assert len(chunks) == 1
        assert "method_a" in chunks[0].content
        assert "method_b" in chunks[0].content

    def test_chunk_javascript(self) -> None:
        code = """import React from 'react';

function App() {
    return <div>Hello</div>;
}

class Component extends React.Component {
    render() {
        return <span>World</span>;
    }
}
"""
        chunker = CodeChunker()
        chunks = chunker.chunk(code, language="javascript")

        assert len(chunks) >= 2
        assert all(c.metadata["language"] == "javascript" for c in chunks)


class TestTextChunker:
    def test_chunk_fixed_size(self) -> None:
        chunker = TextChunker(chars_per_token=4)
        content = "Hello world! " * 500
        chunks = chunker.chunk(content, chunk_size=100, overlap=0)

        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.content) <= 100 * 4 + 50

    def test_chunk_with_overlap(self) -> None:
        chunker = TextChunker(chars_per_token=4)
        content = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 50
        chunks = chunker.chunk(content, chunk_size=50, overlap=10)

        assert len(chunks) > 1

        chunk_texts = [c.content for c in chunks]
        for i in range(len(chunks) - 1):
            end_of_prev = chunk_texts[i][-40:]
            start_of_next = chunk_texts[i + 1][:40]
            overlap_found = any(end_of_prev[j : j + 20] in start_of_next for j in range(len(end_of_prev) - 20))
            assert overlap_found, f"No overlap found between chunk {i} and chunk {i + 1}"

    def test_chunk_single_chunk_short_text(self) -> None:
        chunker = TextChunker(chars_per_token=4)
        content = "Short text."
        chunks = chunker.chunk(content, chunk_size=512, overlap=64)

        assert len(chunks) == 1
        assert chunks[0].content == "Short text."

    def test_chunk_empty_content(self) -> None:
        chunker = TextChunker()
        chunks = chunker.chunk("", chunk_size=512, overlap=64)

        assert chunks == []

    def test_chunk_metadata(self) -> None:
        chunker = TextChunker(chars_per_token=4)
        content = "Some text content for testing. " * 100
        chunks = chunker.chunk(content, chunk_size=50, overlap=5)

        assert len(chunks) > 1
        for i, chunk in enumerate(chunks):
            assert chunk.metadata["chunk_type"] == "text"
            assert chunk.metadata["position"] == i

    def test_chunk_invalid_chunk_size(self) -> None:
        chunker = TextChunker()
        with pytest.raises(RAGIngestionError) as exc_info:
            chunker.chunk("content", chunk_size=0, overlap=0)
        assert exc_info.value.code == "INVALID_CHUNK_SIZE"

    def test_chunk_negative_overlap(self) -> None:
        chunker = TextChunker()
        with pytest.raises(RAGIngestionError) as exc_info:
            chunker.chunk("content", chunk_size=512, overlap=-1)
        assert exc_info.value.code == "INVALID_OVERLAP"

    def test_chunk_overlap_exceeds_size(self) -> None:
        chunker = TextChunker()
        with pytest.raises(RAGIngestionError) as exc_info:
            chunker.chunk("content", chunk_size=100, overlap=200)
        assert exc_info.value.code == "OVERLAP_EXCEEDS_CHUNK_SIZE"

    def test_chunk_default_parameters(self) -> None:
        chunker = TextChunker()
        content = "Test content. " * 200
        chunks = chunker.chunk(content)

        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.metadata["chunk_type"] == "text"

    def test_chunk_preserves_content_integrity(self) -> None:
        chunker = TextChunker(chars_per_token=4)
        content = "The quick brown fox jumps over the lazy dog. " * 30
        chunks = chunker.chunk(content, chunk_size=30, overlap=5)

        total_chars = sum(len(c.content) for c in chunks)
        assert total_chars >= len(content)

    def test_chunk_no_overlap(self) -> None:
        chunker = TextChunker(chars_per_token=4)
        content = "X" * 2000
        chunks = chunker.chunk(content, chunk_size=100, overlap=0)

        total = sum(len(c.content) for c in chunks)
        assert total >= len(content)


class TestDocumentIngestor:
    async def test_ingest_markdown(self) -> None:
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.md"
            file_path.write_text(_MARKDOWN_CONTENT, encoding="utf-8")

            ingestor = DocumentIngestor()
            chunks = await ingestor.ingest(str(file_path), "req_docs")

            assert len(chunks) == 4
            for chunk in chunks:
                assert chunk.doc_id
                assert chunk.chunk_id
                assert chunk.doc_id in chunk.chunk_id
                assert chunk.metadata["source"] == str(file_path)
                assert chunk.metadata["collection"] == "req_docs"
                assert chunk.metadata["file_type"] == "markdown"
                assert chunk.metadata["chunk_type"] == "markdown"

    async def test_ingest_python_code(self) -> None:
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "module.py"
            file_path.write_text(_PYTHON_CODE, encoding="utf-8")

            ingestor = DocumentIngestor()
            chunks = await ingestor.ingest(str(file_path), "api_docs")

            assert len(chunks) == 4
            for chunk in chunks:
                assert chunk.metadata["file_type"] == "code"
                assert chunk.metadata["chunk_type"] == "code"
                assert chunk.metadata["language"] == "python"

    async def test_ingest_text_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "notes.txt"
            content = "Plain text notes. " * 100
            file_path.write_text(content, encoding="utf-8")

            ingestor = DocumentIngestor()
            chunks = await ingestor.ingest(str(file_path), "test_reports")

            assert len(chunks) >= 1
            for chunk in chunks:
                assert chunk.metadata["file_type"] == "text"
                assert chunk.metadata["chunk_type"] == "text"

    async def test_ingest_json_as_code(self) -> None:
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "config.json"
            file_path.write_text('{"key": "value"}', encoding="utf-8")

            ingestor = DocumentIngestor()
            chunks = await ingestor.ingest(str(file_path), "api_docs")

            assert len(chunks) == 1
            assert chunks[0].metadata["file_type"] == "code"
            assert "key" in chunks[0].content

    async def test_ingest_file_not_found(self) -> None:
        ingestor = DocumentIngestor()
        with pytest.raises(RAGIngestionError) as exc_info:
            await ingestor.ingest("/nonexistent/file.md", "req_docs")
        assert exc_info.value.code == "SOURCE_NOT_FOUND"

    async def test_ingest_empty_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "empty.md"
            file_path.write_text("", encoding="utf-8")

            ingestor = DocumentIngestor()
            chunks = await ingestor.ingest(str(file_path), "req_docs")
            assert chunks == []

    async def test_ingest_whitespace_only_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "blank.md"
            file_path.write_text("   \n\n  ", encoding="utf-8")

            ingestor = DocumentIngestor()
            chunks = await ingestor.ingest(str(file_path), "req_docs")
            assert chunks == []

    async def test_ingest_custom_metadata(self) -> None:
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "doc.md"
            file_path.write_text("## Section\n\nContent.", encoding="utf-8")

            ingestor = DocumentIngestor()
            custom_meta = {"author": "test-user", "version": "1.0"}
            chunks = await ingestor.ingest(str(file_path), "req_docs", metadata=custom_meta)

            assert len(chunks) == 1
            assert chunks[0].metadata["author"] == "test-user"
            assert chunks[0].metadata["version"] == "1.0"
            assert chunks[0].metadata["source"] == str(file_path)
            assert chunks[0].metadata["collection"] == "req_docs"

    async def test_ingest_chunk_ids_are_unique(self) -> None:
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "doc.md"
            file_path.write_text(_MARKDOWN_CONTENT, encoding="utf-8")

            ingestor = DocumentIngestor()
            chunks = await ingestor.ingest(str(file_path), "req_docs")

            chunk_ids = [c.chunk_id for c in chunks]
            assert len(chunk_ids) == len(set(chunk_ids))

    async def test_ingest_doc_id_consistent(self) -> None:
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "doc.md"
            file_path.write_text("## Section\n\nContent.", encoding="utf-8")

            ingestor = DocumentIngestor()
            chunks1 = await ingestor.ingest(str(file_path), "req_docs")
            file_path.write_text("## Section\n\nUpdated content.", encoding="utf-8")
            chunks2 = await ingestor.ingest(str(file_path), "req_docs")

            assert chunks1[0].doc_id == chunks2[0].doc_id

    async def test_ingest_different_collections_yield_different_ids(self) -> None:
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "doc.md"
            file_path.write_text("## Section\n\nContent.", encoding="utf-8")

            ingestor = DocumentIngestor()
            chunks1 = await ingestor.ingest(str(file_path), "req_docs")
            chunks2 = await ingestor.ingest(str(file_path), "api_docs")

            assert chunks1[0].doc_id != chunks2[0].doc_id

    async def test_ingest_javascript_code(self) -> None:
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "app.js"
            file_path.write_text("function main() {\n  console.log('hello');\n}\n", encoding="utf-8")

            ingestor = DocumentIngestor()
            chunks = await ingestor.ingest(str(file_path), "locator_library")

            assert len(chunks) >= 1
            assert chunks[-1].metadata["language"] == "javascript"

    async def test_ingest_unknown_extension(self) -> None:
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "data.xyz"
            file_path.write_text("Some custom data.", encoding="utf-8")

            ingestor = DocumentIngestor()
            chunks = await ingestor.ingest(str(file_path), "test_reports")

            assert len(chunks) == 1
            assert chunks[0].metadata["file_type"] == "text"
            assert chunks[0].metadata["chunk_type"] == "text"

    async def test_ingest_yaml_as_markdown(self) -> None:
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "config.yaml"
            content = "## Service Config\n\nhost: localhost\nport: 8080\n"
            file_path.write_text(content, encoding="utf-8")

            ingestor = DocumentIngestor()
            chunks = await ingestor.ingest(str(file_path), "api_docs")

            assert len(chunks) == 1
            assert chunks[0].metadata["file_type"] == "markdown"
            assert "host: localhost" in chunks[0].content


class TestChunkDataclass:
    def test_chunk_creation(self) -> None:
        chunk = Chunk(
            doc_id="doc123",
            chunk_id="doc123_chunk_0000",
            content="Hello World",
            metadata={"type": "text", "position": 0},
        )

        assert chunk.doc_id == "doc123"
        assert chunk.chunk_id == "doc123_chunk_0000"
        assert chunk.content == "Hello World"
        assert chunk.metadata == {"type": "text", "position": 0}

    def test_chunk_mutable_metadata(self) -> None:
        chunk = Chunk(
            doc_id="doc1",
            chunk_id="doc1_chunk_0000",
            content="test",
            metadata={"key": "value"},
        )

        chunk.metadata["new_key"] = 42
        assert chunk.metadata["new_key"] == 42
        assert chunk.metadata["key"] == "value"

    def test_chunk_equality(self) -> None:
        c1 = Chunk(doc_id="d1", chunk_id="c1", content="a", metadata={})
        c2 = Chunk(doc_id="d1", chunk_id="c1", content="a", metadata={})
        c3 = Chunk(doc_id="d2", chunk_id="c2", content="b", metadata={})

        assert c1 == c2
        assert c1 != c3
