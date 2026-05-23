from __future__ import annotations

import os
from pathlib import Path

import pytest

from testagent.plan.prd_parser import PrdDocument, PrdParser


# ── PrdDocument Tests ─────────────────────────────────────────────────────────


class TestPrdDocument:
    def test_create_with_text_only(self) -> None:
        doc = PrdDocument(text="Hello world", images=[], format="md")
        assert doc.text == "Hello world"
        assert doc.images == []
        assert doc.format == "md"

    def test_create_with_images(self) -> None:
        images = [
            {"path": "img1.png", "description": "Screenshot 1"},
            {"path": "img2.png", "description": "Diagram 2"},
        ]
        doc = PrdDocument(text="Some text", images=images, format="pdf")
        assert doc.text == "Some text"
        assert doc.images == images
        assert doc.format == "pdf"

    def test_formatted_text_no_images(self) -> None:
        doc = PrdDocument(text="Just text", images=[], format="md")
        assert doc.formatted_text == "Just text"

    def test_formatted_text_with_images(self) -> None:
        images = [
            {"path": "diagram.png", "description": "Architecture diagram"},
            {"path": "flow.png", "description": "Flow chart"},
        ]
        doc = PrdDocument(text="Main content", images=images, format="md")
        expected = "Main content\n\n---\n\n[Image: diagram.png] Architecture diagram\n\n[Image: flow.png] Flow chart"
        assert doc.formatted_text == expected


# ── PrdParser Tests ───────────────────────────────────────────────────────────


class TestPrdParserSupports:
    def setup_method(self) -> None:
        self.parser = PrdParser()

    def test_supports_md(self) -> None:
        assert self.parser.supports("doc.md") is True

    def test_supports_pdf(self) -> None:
        assert self.parser.supports("doc.pdf") is True

    def test_supports_docx(self) -> None:
        assert self.parser.supports("doc.docx") is True

    def test_does_not_support_txt(self) -> None:
        assert self.parser.supports("doc.txt") is False

    def test_does_not_support_xyz(self) -> None:
        assert self.parser.supports("doc.xyz") is False

    def test_does_not_support_no_extension(self) -> None:
        assert self.parser.supports("README") is False


class TestPrdParserParseMd:
    def setup_method(self) -> None:
        self.parser = PrdParser()

    def test_parse_md_plain_text(self, tmp_path: Path) -> None:
        md_file = tmp_path / "doc.md"
        md_file.write_text("# Title\n\nThis is **bold** and *italic* text.\n\n- item 1\n- item 2")
        doc = self.parser.parse_md(str(md_file))
        assert "Title" in doc.text
        assert "bold" in doc.text
        assert "italic" in doc.text
        assert "item 1" in doc.text
        assert "item 2" in doc.text
        assert doc.images == []
        assert doc.format == "md"

    def test_parse_md_with_images(self, tmp_path: Path) -> None:
        md_file = tmp_path / "doc.md"
        md_file.write_text(
            "# Report\n\n![Architecture](images/arch.png)\n\nSome text.\n\n![Flow](images/flow.png \"Flow chart\")"
        )
        doc = self.parser.parse_md(str(md_file))
        assert "Report" in doc.text
        assert "Some text" in doc.text
        assert len(doc.images) == 2
        assert doc.images[0]["description"] == "Architecture"
        assert doc.images[1]["description"] == "Flow chart"
        # Paths should be resolved relative to the document's parent directory
        expected_path_0 = os.path.normpath(os.path.join(str(tmp_path), "images/arch.png"))
        expected_path_1 = os.path.normpath(os.path.join(str(tmp_path), "images/flow.png"))
        assert os.path.normpath(doc.images[0]["path"]) == expected_path_0
        assert os.path.normpath(doc.images[1]["path"]) == expected_path_1
        assert doc.format == "md"

    def test_parse_md_no_images(self, tmp_path: Path) -> None:
        md_file = tmp_path / "readme.md"
        md_file.write_text("Just plain content\n\nNo images here.")
        doc = self.parser.parse_md(str(md_file))
        assert doc.text == "Just plain content\n\nNo images here."
        assert doc.images == []


class TestPrdParserParseText:
    def setup_method(self) -> None:
        self.parser = PrdParser()

    def test_parse_text_plain(self) -> None:
        result = self.parser.parse_text("dummy.md", content="Hello\nWorld")
        assert result == "Hello\nWorld"

    def test_parse_text_from_file(self, tmp_path: Path) -> None:
        txt_file = tmp_path / "sample.txt"
        txt_file.write_text("File content here")
        result = self.parser.parse_text(str(txt_file))
        assert result == "File content here"

    def test_parse_text_from_file_with_content_override(self, tmp_path: Path) -> None:
        txt_file = tmp_path / "sample.txt"
        txt_file.write_text("File content")
        result = self.parser.parse_text(str(txt_file), content="Override")
        assert result == "Override"


class TestPrdParserParse:
    def setup_method(self) -> None:
        self.parser = PrdParser()

    def test_parse_dispatches_md(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("# Title\n\nContent", encoding="utf-8")
        doc = self.parser.parse(str(f))
        assert doc.format == "md"
        assert "Title" in doc.text

    def test_parse_unsupported_format(self) -> None:
        with pytest.raises(ValueError, match="Unsupported file format"):
            self.parser.parse("file.xyz")
