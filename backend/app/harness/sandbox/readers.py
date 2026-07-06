"""Structured readers for files inside a SlotFlow workspace."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from pypdf import PdfReader


WorkspaceReadKind = Literal[
    "text",
    "document",
    "spreadsheet",
    "presentation",
    "diagram",
    "pdf",
    "image",
    "binary",
]


TEXT_EXTENSIONS = {
    ".csv",
    ".css",
    ".graphql",
    ".htm",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".log",
    ".markdown",
    ".md",
    ".mjs",
    ".py",
    ".sql",
    ".svg",
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

    if extension == ".drawio":
        return WorkspaceReadResult(
            path=relative_path,
            kind="diagram",
            media_type=media_type_for_extension(extension),
            size_bytes=size_bytes,
            content=path.read_text(encoding="utf-8"),
            metadata={"format": "drawio"},
        )

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
            warning=None if path.stat().st_size else "document is empty",
        )

    if extension in {".xlsx", ".xlsm"}:
        content, metadata = extract_xlsx_text(path)
        return WorkspaceReadResult(
            path=relative_path,
            kind="spreadsheet",
            media_type=media_type_for_extension(extension),
            size_bytes=size_bytes,
            content=content,
            metadata=metadata,
            warning=None if content.strip() else "spreadsheet text extraction returned no text",
        )

    if extension == ".pptx":
        content, metadata = extract_pptx_text(path)
        return WorkspaceReadResult(
            path=relative_path,
            kind="presentation",
            media_type=media_type_for_extension(extension),
            size_bytes=size_bytes,
            content=content,
            metadata=metadata,
            warning=None if content.strip() else "presentation text extraction returned no text",
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


def extract_xlsx_text(path: Path) -> tuple[str, dict[str, object]]:
    """Extract sheet names and cell text from an .xlsx workbook without external deps."""

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        shared_strings = _xlsx_shared_strings(archive) if "xl/sharedStrings.xml" in names else []
        workbook_sheets = _xlsx_sheet_names(archive) if "xl/workbook.xml" in names else []
        sheet_paths = sorted(name for name in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name))

        sheet_texts: list[str] = []
        for index, sheet_path in enumerate(sheet_paths):
            sheet_name = workbook_sheets[index] if index < len(workbook_sheets) else Path(sheet_path).stem
            rows = _xlsx_sheet_rows(archive, sheet_path, shared_strings)
            if not rows:
                continue
            sheet_texts.append(
                "\n".join(
                    [
                        f"## {sheet_name}",
                        *["\t".join(row) for row in rows[:100]],
                    ]
                )
            )

    metadata: dict[str, object] = {
        "format": "xlsx",
        "sheets": workbook_sheets or [Path(path).stem for path in sheet_paths],
        "sheet_count": len(sheet_paths),
    }
    return "\n\n".join(sheet_texts), metadata


def extract_pptx_text(path: Path) -> tuple[str, dict[str, object]]:
    """Extract slide text from a .pptx presentation without external deps."""

    with zipfile.ZipFile(path) as archive:
        slide_paths = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=_natural_sort_key,
        )
        slides: list[str] = []
        for index, slide_path in enumerate(slide_paths, start=1):
            text = _zip_xml_text(archive, slide_path)
            if text:
                slides.append(f"## Slide {index}\n{text}")
    return "\n\n".join(slides), {"format": "pptx", "slides": len(slide_paths)}


def extract_pdf_text(path: Path) -> tuple[str, int]:
    """Extract text from a PDF using pypdf."""

    reader = PdfReader(str(path))
    page_text: list[str] = []
    for page in reader.pages:
        page_text.append(page.extract_text() or "")
    return "\n\n".join(text.strip() for text in page_text if text.strip()), len(reader.pages)


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.iter():
        if item.tag.endswith("}si") or item.tag == "si":
            values.append(_element_text(item))
    return values


def _xlsx_sheet_names(archive: zipfile.ZipFile) -> list[str]:
    root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    names: list[str] = []
    for node in root.iter():
        if node.tag.endswith("}sheet") or node.tag == "sheet":
            name = node.attrib.get("name")
            if name:
                names.append(name)
    return names


def _xlsx_sheet_rows(
    archive: zipfile.ZipFile,
    sheet_path: str,
    shared_strings: list[str],
) -> list[list[str]]:
    root = ElementTree.fromstring(archive.read(sheet_path))
    rows: list[list[str]] = []
    for row in root.iter():
        if not (row.tag.endswith("}row") or row.tag == "row"):
            continue
        cells: list[str] = []
        for cell in row:
            if not (cell.tag.endswith("}c") or cell.tag == "c"):
                continue
            cells.append(_xlsx_cell_text(cell, shared_strings))
        if any(value.strip() for value in cells):
            rows.append(cells)
    return rows


def _xlsx_cell_text(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    value = ""
    inline = ""
    for child in cell.iter():
        if child is cell:
            continue
        if child.tag.endswith("}v") or child.tag == "v":
            value = child.text or ""
        elif child.tag.endswith("}t") or child.tag == "t":
            inline += child.text or ""
    if cell_type == "s" and value.isdigit():
        index = int(value)
        return shared_strings[index] if 0 <= index < len(shared_strings) else value
    return inline or value


def _zip_xml_text(archive: zipfile.ZipFile, path: str) -> str:
    root = ElementTree.fromstring(archive.read(path))
    return _element_text(root)


def _element_text(root: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in root.iter():
        if (node.tag.endswith("}t") or node.tag == "t") and node.text:
            parts.append(node.text)
    return "\n".join(part.strip() for part in parts if part.strip())


def _natural_sort_key(value: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


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
        ".css": "text/css",
        ".drawio": "application/vnd.jgraph.mxfile",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".graphql": "application/graphql",
        ".gif": "image/gif",
        ".htm": "text/html",
        ".html": "text/html",
        ".js": "text/javascript",
        ".jsx": "text/jsx",
        ".jpeg": "image/jpeg",
        ".jpg": "image/jpeg",
        ".json": "application/json",
        ".log": "text/plain",
        ".markdown": "text/markdown",
        ".md": "text/markdown",
        ".mjs": "text/javascript",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".py": "text/x-python",
        ".sql": "application/sql",
        ".svg": "image/svg+xml",
        ".text": "text/plain",
        ".toml": "application/toml",
        ".ts": "text/typescript",
        ".tsx": "text/tsx",
        ".txt": "text/plain",
        ".webp": "image/webp",
        ".xls": "application/vnd.ms-excel",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
                names = set(archive.namelist())
                if "word/document.xml" in names:
                    return ".docx"
                if "xl/workbook.xml" in names:
                    return ".xlsx"
                if any(name.startswith("ppt/slides/slide") for name in names):
                    return ".pptx"
        except zipfile.BadZipFile:
            return ""

    return ""


def plain_text_excerpt(value: str, *, max_chars: int = 240) -> str:
    """Collapse text into a short search/listing preview."""

    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars].rstrip()}..."
