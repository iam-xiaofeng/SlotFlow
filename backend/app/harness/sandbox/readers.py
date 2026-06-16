"""Structured readers for files inside a SlotFlow workspace."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from pypdf import PdfReader


WorkspaceReadKind = Literal["text", "document", "pdf", "image", "binary"]


TEXT_EXTENSIONS = {
    ".csv",
    ".htm",
    ".html",
    ".json",
    ".log",
    ".markdown",
    ".md",
    ".py",
    ".text",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}


@dataclass(frozen=True, slots=True)
class WorkspaceReadResult:
    """A model-readable representation of a workspace file."""

    path: str
    kind: WorkspaceReadKind
    media_type: str
    size_bytes: int
    content: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    warning: str | None = None

    def model_dump(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "path": self.path,
            "kind": self.kind,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "source": "slotflow_workspace",
            "metadata": self.metadata,
        }
        if self.content is not None:
            payload["content"] = self.content
        if self.warning is not None:
            payload["warning"] = self.warning
        return payload


def read_workspace_file(path: Path, *, relative_path: str) -> WorkspaceReadResult:
    """Read a workspace file as structured text/metadata for tool output."""

    extension = detect_workspace_file_extension(path)
    size_bytes = path.stat().st_size

    if extension in TEXT_EXTENSIONS:
        return WorkspaceReadResult(
            path=relative_path,
            kind="text",
            media_type=media_type_for_extension(extension),
            size_bytes=size_bytes,
            content=path.read_text(encoding="utf-8"),
            metadata={"format": extension.removeprefix(".") or "text"},
        )

    if extension == ".docx":
        return WorkspaceReadResult(
            path=relative_path,
            kind="document",
            media_type=media_type_for_extension(extension),
            size_bytes=size_bytes,
            content=extract_docx_text(path),
            metadata={"format": "docx"},
        )

    if extension == ".pdf":
        text, pages = extract_pdf_text(path)
        return WorkspaceReadResult(
            path=relative_path,
            kind="pdf",
            media_type=media_type_for_extension(extension),
            size_bytes=size_bytes,
            content=text,
            metadata={"format": "pdf", "pages": pages},
            warning=None if text.strip() else "pdf text extraction returned no text",
        )

    if extension in IMAGE_EXTENSIONS:
        data = path.read_bytes()
        metadata = image_metadata(data, extension=extension)
        return WorkspaceReadResult(
            path=relative_path,
            kind="image",
            media_type=media_type_for_extension(extension),
            size_bytes=size_bytes,
            metadata=metadata,
            warning=(
                "image pixels are not inlined; use a vision-capable model or OCR tool "
                "for visual understanding"
            ),
        )

    return WorkspaceReadResult(
        path=relative_path,
        kind="binary",
        media_type=media_type_for_extension(extension),
        size_bytes=size_bytes,
        metadata={"format": extension.removeprefix(".") or "unknown"},
        warning="unsupported binary file type; content was not inlined",
    )


def extract_docx_text(path: Path) -> str:
    """Extract paragraph-like text from a .docx document without external deps."""

    with zipfile.ZipFile(path) as archive:
        xml_bytes = archive.read("word/document.xml")

    root = ElementTree.fromstring(xml_bytes)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{namespace}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{namespace}tab":
                parts.append("\t")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def extract_pdf_text(path: Path) -> tuple[str, int]:
    """Extract text from a PDF using pypdf."""

    reader = PdfReader(str(path))
    page_text: list[str] = []
    for page in reader.pages:
        page_text.append(page.extract_text() or "")
    return "\n\n".join(text.strip() for text in page_text if text.strip()), len(reader.pages)


def image_metadata(data: bytes, *, extension: str) -> dict[str, object]:
    """Return minimal image metadata that works for text-only models."""

    width: int | None = None
    height: int | None = None
    image_format = extension.removeprefix(".").upper()

    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        image_format = "PNG"
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
    elif data.startswith(b"\xff\xd8"):
        image_format = "JPEG"
        width, height = jpeg_dimensions(data)
    elif data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        image_format = "GIF"
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        image_format = "WEBP"

    metadata: dict[str, object] = {"format": image_format}
    if width is not None and height is not None:
        metadata.update({"width": width, "height": height})
    return metadata


def jpeg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    """Parse dimensions from JPEG SOF markers."""

    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue

        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None, None

        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            return None, None

        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height

        index += segment_length

    return None, None


def media_type_for_extension(extension: str) -> str:
    """Map common workspace extensions to media types."""

    return {
        ".csv": "text/csv",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".gif": "image/gif",
        ".htm": "text/html",
        ".html": "text/html",
        ".jpeg": "image/jpeg",
        ".jpg": "image/jpeg",
        ".json": "application/json",
        ".log": "text/plain",
        ".markdown": "text/markdown",
        ".md": "text/markdown",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".py": "text/x-python",
        ".text": "text/plain",
        ".toml": "application/toml",
        ".ts": "text/typescript",
        ".tsx": "text/tsx",
        ".txt": "text/plain",
        ".webp": "image/webp",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
    }.get(extension, "application/octet-stream")


def detect_workspace_file_extension(path: Path) -> str:
    """Detect the file type from extension first, then from file content."""

    extension = path.suffix.lower()
    if extension:
        return extension

    with path.open("rb") as file:
        header = file.read(32)
    if header.startswith(b"%PDF-"):
        return ".pdf"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith(b"\xff\xd8"):
        return ".jpg"
    if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
        return ".gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return ".webp"

    if zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path) as archive:
                if "word/document.xml" in archive.namelist():
                    return ".docx"
        except zipfile.BadZipFile:
            return ""

    return ""


def plain_text_excerpt(value: str, *, max_chars: int = 240) -> str:
    """Collapse text into a short search/listing preview."""

    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars].rstrip()}..."
