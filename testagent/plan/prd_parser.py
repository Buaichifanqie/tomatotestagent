"""PRD document parser for markdown, PDF, and DOCX formats."""

from __future__ import annotations

import os
import re
from pathlib import Path


class PrdDocument:
    """Represents a parsed PRD document with text, images, and format metadata."""

    def __init__(self, text: str, images: list[dict[str, str]], format: str) -> None:
        self.text = text
        self.images = images
        self.format = format

    @property
    def formatted_text(self) -> str:
        """Return text with image descriptions appended."""
        if not self.images:
            return self.text

        parts = [self.text]
        parts.append("---")
        parts.extend(f"[Image: {img['path']}] {img['description']}" for img in self.images)
        return "\n\n".join(parts)


class PrdParser:
    """Parser for PRD documents in markdown, PDF, and DOCX formats."""

    SUPPORTED_EXTENSIONS = {".md", ".pdf", ".docx"}

    def supports(self, file_path: str) -> bool:
        """Check if the file extension is supported."""
        ext = Path(file_path).suffix.lower()
        return ext in self.SUPPORTED_EXTENSIONS

    def parse(self, file_path: str) -> PrdDocument:
        """Dispatch to the appropriate format-specific parser."""
        ext = Path(file_path).suffix.lower()
        if ext == ".md":
            return self.parse_md(file_path)
        if ext == ".pdf":
            return self.parse_pdf(file_path)
        if ext == ".docx":
            return self.parse_docx(file_path)
        msg = f"Unsupported file format: {ext}"
        raise ValueError(msg)

    def parse_md(self, file_path: str) -> PrdDocument:
        """Parse a markdown file and extract text and image references."""
        text = self.parse_text(file_path)
        images = self._extract_md_images(text, file_path)
        # Clean markdown syntax from the text
        clean_text = self._strip_markdown(text)
        return PrdDocument(text=clean_text, images=images, format="md")

    def parse_pdf(self, file_path: str) -> PrdDocument:
        """Parse a PDF file using PyMuPDF (fitz)."""
        import fitz  # type: ignore[import-untyped]

        doc = fitz.open(file_path)
        text_parts: list[str] = []
        images: list[dict[str, str]] = []
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text_parts.append(page.get_text())
            for img_index, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                base_image = doc.extract_image(xref)
                img_filename = f"page{page_num + 1}_img{img_index + 1}.{base_image['ext']}"
                images.append({"path": img_filename, "description": f"Page {page_num + 1} image {img_index + 1}"})
        doc.close()
        return PrdDocument(text="\n".join(text_parts), images=images, format="pdf")

    def parse_docx(self, file_path: str) -> PrdDocument:
        """Parse a DOCX file using python-docx."""
        from docx import Document  # type: ignore[import-untyped]

        doc = Document(file_path)
        text_parts: list[str] = []
        for para in doc.paragraphs:
            text_parts.append(para.text)

        images: list[dict[str, str]] = []
        # Extract inline shapes (images) from the document
        for rel_id, rel in doc.part.rels.items():  # type: ignore[attr-defined]
            if "image" in rel.reltype:
                images.append({
                    "path": rel.target_ref,
                    "description": f"Image: {Path(rel.target_ref).name}",
                })
        return PrdDocument(text="\n".join(text_parts), images=images, format="docx")

    def parse_text(self, file_path: str, content: str | None = None) -> str:
        """Read text from a file, or return the provided content."""
        if content is not None:
            return content
        return Path(file_path).read_text(encoding="utf-8")

    # ── Private helpers ──────────────────────────────────────────────────────────

    def _extract_md_images(self, text: str, file_path: str) -> list[dict[str, str]]:
        """Extract image references from markdown text."""
        pattern = r"!\[([^\]]*)\]\(([^)]+)\)"
        parent = Path(file_path).parent
        images: list[dict[str, str]] = []
        for match in re.finditer(pattern, text):
            alt_text = match.group(1) or ""
            raw_path_and_title = match.group(2)
            # Parse the optional quoted title (e.g., "Flow chart")
            title = ""
            rest = raw_path_and_title.strip()
            if '"' in rest or "'" in rest:
                # Find the last quoted string
                quote_match = re.search(r'''["']([^"']+)["']\s*$''', rest)
                if quote_match:
                    title = quote_match.group(1)
                    # Remove the title from the path part
                    rest = rest[: quote_match.start()].strip()
            img_path = rest
            resolved = (parent / img_path).resolve()
            images.append({
                "path": str(resolved),
                "description": title or alt_text,
            })
        return images

    def _strip_markdown(self, text: str) -> str:
        """Strip markdown syntax while preserving meaningful content."""
        # Remove image references entirely (already extracted)
        text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", "", text)
        # Remove link references but keep link text
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        # Remove bold/italic markers
        text = re.sub(r"(\*{1,3}|_{1,3})(.*?)\1", r"\2", text)
        # Remove inline code backticks
        text = re.sub(r"`([^`]+)`", r"\1", text)
        # Remove heading markers
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        # Remove horizontal rules
        text = re.sub(r"^---+$", "", text, flags=re.MULTILINE)
        # Remove blockquote markers
        text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
        # Remove list markers (-, *, +, or numbered)
        text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
        return text.strip()
