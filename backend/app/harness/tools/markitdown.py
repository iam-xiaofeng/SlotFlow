"""Workspace-scoped MarkItDown conversion with optional LLM Vision OCR."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import convert_to_messages
from langchain_core.tools import BaseTool

from app.harness.sandbox import SlotFlowSandboxConfig, build_slotflow_workspace
from app.harness.tools.workspace import (
    normalize_artifact_path,
    threaded_structured_tool,
)
from app.harness.utils import message_content_text


_ARCHIVE_EXTENSIONS = {".docx", ".epub", ".pptx", ".xlsx", ".xlsm", ".zip"}
_IMAGE_EXTENSIONS = {".jpeg", ".jpg", ".png"}
_OPENXML_MEDIA_PREFIXES = ("word/media/", "ppt/media/", "xl/media/")


class MarkItDownConversionError(ValueError):
    """Raised when a local file violates conversion limits or cannot be converted."""


@dataclass(frozen=True, slots=True)
class SlotFlowMarkItDownConfig:
    """Limits and optional dedicated OpenAI-compatible Vision settings."""

    enabled: bool = True
    max_input_bytes: int = 50 * 1024 * 1024
    max_output_chars: int = 750_000
    max_archive_entries: int = 1_000
    max_archive_uncompressed_bytes: int = 250 * 1024 * 1024
    vision_enabled: bool = True
    vision_max_pages: int = 20
    vision_max_images: int = 20
    vision_timeout_seconds: int = 120
    vision_model: str | None = None
    vision_base_url: str | None = None
    vision_api_key: str | None = field(default=None, repr=False, compare=False)
    vision_prompt: str = (
        "Extract all visible text faithfully, preserving reading order and layout in Markdown. "
        "If the image contains no legible text, provide a concise factual description. "
        "Return only Markdown without commentary about the task."
    )


@dataclass(frozen=True, slots=True)
class MarkItDownConversion:
    """Small internal result projected into the model-facing tool response."""

    markdown: str
    title: str | None
    vision_used: bool
    warnings: tuple[str, ...] = ()


class TrackingVisionClient:
    """Record actual Vision calls/failures while preserving the expected client shape."""

    def __init__(self, client: Any) -> None:
        self.calls = 0
        self.failures: list[str] = []
        self.chat = SimpleNamespace(
            completions=_TrackingChatCompletions(self, client.chat.completions),
        )


class _TrackingChatCompletions:
    def __init__(self, owner: TrackingVisionClient, completions: Any) -> None:
        self._owner = owner
        self._completions = completions

    def create(self, **kwargs: Any) -> Any:
        self._owner.calls += 1
        try:
            return self._completions.create(**kwargs)
        except Exception as exc:
            self._owner.failures.append(type(exc).__name__)
            raise


class LangChainVisionClient:
    """Minimal OpenAI chat.completions facade over the run's existing chat model."""

    def __init__(self, model: BaseChatModel) -> None:
        self.chat = SimpleNamespace(completions=_LangChainChatCompletions(model))


class _LangChainChatCompletions:
    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    def create(self, *, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        del model, kwargs
        response = self._model.invoke(convert_to_messages(messages))
        content = message_content_text(getattr(response, "content", ""))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        )


def convert_file_to_markdown(
    source: Path,
    *,
    config: SlotFlowMarkItDownConfig,
    use_vision: bool = True,
    llm_client: Any | None = None,
    llm_model: str | None = None,
) -> MarkItDownConversion:
    """Convert one validated local file through MarkItDown's narrow local API."""

    if not source.is_file():
        raise MarkItDownConversionError(f"file does not exist: {source.name}")
    size = source.stat().st_size
    if size > config.max_input_bytes:
        raise MarkItDownConversionError(
            f"input exceeds max_input_bytes: {size} > {config.max_input_bytes}",
        )

    _validate_archive_limits(source, config=config)
    vision_requested = bool(use_vision and config.vision_enabled)
    vision_allowed_for_format = source.suffix.lower() != ".zip"
    vision_active = bool(
        vision_requested
        and vision_allowed_for_format
        and llm_client is not None
        and llm_model
    )
    if vision_active:
        _validate_vision_workload(source, config=config)

    from markitdown import MarkItDown

    tracked_client = TrackingVisionClient(llm_client) if vision_active else None
    kwargs: dict[str, Any] = {}
    if vision_active:
        kwargs.update(
            llm_client=tracked_client,
            llm_model=llm_model,
            llm_prompt=config.vision_prompt,
        )
    converter = MarkItDown(enable_plugins=vision_active, **kwargs)
    try:
        result = converter.convert_local(source, **kwargs)
    except Exception as exc:
        raise MarkItDownConversionError(
            f"MarkItDown could not convert {source.name}: {type(exc).__name__}: {exc}",
        ) from exc

    markdown = result.markdown or ""
    if len(markdown) > config.max_output_chars:
        raise MarkItDownConversionError(
            "converted Markdown exceeds max_output_chars: "
            f"{len(markdown)} > {config.max_output_chars}; provide a smaller file",
        )

    warnings: list[str] = []
    if tracked_client is not None and tracked_client.failures:
        warnings.append(
            "Vision OCR failed for "
            f"{len(tracked_client.failures)} image(s): "
            + ", ".join(tracked_client.failures),
        )
    if "[No text could be extracted from this page]" in markdown:
        warnings.append("Vision OCR returned no text for at least one PDF page.")
    if "[Error processing page " in markdown:
        warnings.append("At least one PDF page failed during Vision OCR processing.")
    if vision_requested and not vision_allowed_for_format:
        warnings.append(
            "Vision OCR is disabled for generic ZIP archives; extract the target file "
            "into the workspace and convert it directly.",
        )
    if vision_requested and not vision_active and _may_need_vision(source, markdown):
        warnings.append(
            "Vision OCR was requested but no compatible Vision client is configured; "
            "the result contains only non-Vision extraction.",
        )
    if not markdown.strip():
        warnings.append("MarkItDown produced no textual content.")
    return MarkItDownConversion(
        markdown=markdown,
        title=result.title,
        vision_used=bool(tracked_client and tracked_client.calls),
        warnings=tuple(warnings),
    )


def build_markitdown_tools(
    config: SlotFlowMarkItDownConfig,
    *,
    sandbox_config: SlotFlowSandboxConfig,
    model: str | BaseChatModel | None,
    thread_id: str | None,
    vision_client: Any | None = None,
    vision_model: str | None = None,
) -> list[BaseTool]:
    """Expose one local conversion tool under the existing workspace boundary."""

    if not config.enabled:
        return []

    workspace = build_slotflow_workspace(sandbox_config)
    resolved_client, resolved_model = _resolve_vision_client(
        config,
        model=model,
        client_override=vision_client,
        model_override=vision_model,
    )

    def convert_workspace_file_to_markdown(
        path: str,
        output_path: str | None = None,
        use_vision: bool = True,
    ) -> str:
        """Convert a workspace-local document/image/audio/archive to Markdown.

        `path` must be relative to the SlotFlow workspace. Set `output_path` to write
        the complete Markdown into this conversation's artifact directory; otherwise
        the Markdown is returned inline. Vision OCR is used only when the selected run
        model supports images or dedicated OpenAI-compatible Vision settings exist.
        """

        source = workspace.resolve_path(path)
        conversion = convert_file_to_markdown(
            source,
            config=config,
            use_vision=use_vision,
            llm_client=resolved_client,
            llm_model=resolved_model,
        )
        payload: dict[str, Any] = {
            "source_path": source.relative_to(workspace.root).as_posix(),
            "title": conversion.title,
            "characters": len(conversion.markdown),
            "vision_used": conversion.vision_used,
            "warnings": list(conversion.warnings),
        }
        if output_path:
            artifact_path = normalize_artifact_path(output_path, thread_id=thread_id)
            target = workspace.write_text(artifact_path, conversion.markdown)
            payload["output_path"] = target.relative_to(workspace.root).as_posix()
        else:
            payload["markdown"] = conversion.markdown
        return json.dumps(payload, ensure_ascii=False)

    return [
        threaded_structured_tool(
            convert_workspace_file_to_markdown,
            name="convert_file_to_markdown",
        )
    ]


def _resolve_vision_client(
    config: SlotFlowMarkItDownConfig,
    *,
    model: str | BaseChatModel | None,
    client_override: Any | None,
    model_override: str | None,
) -> tuple[Any | None, str | None]:
    if not config.vision_enabled:
        return None, None
    if client_override is not None and model_override:
        return client_override, model_override
    if config.vision_model and config.vision_api_key:
        from openai import OpenAI

        return (
            OpenAI(
                api_key=config.vision_api_key,
                base_url=config.vision_base_url,
                timeout=config.vision_timeout_seconds,
            ),
            config.vision_model,
        )
    if isinstance(model, BaseChatModel):
        model_id = _chat_model_id(model)
        if model_id and _model_supports_vision(model_id, model):
            return LangChainVisionClient(model), model_id
    return None, None


def _chat_model_id(model: BaseChatModel) -> str | None:
    for attribute in ("model", "model_name"):
        value = getattr(model, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _model_supports_vision(model_id: str, model: BaseChatModel) -> bool:
    try:
        from litellm import supports_vision

        provider = getattr(model, "custom_llm_provider", None)
        return bool(supports_vision(model=model_id, custom_llm_provider=provider))
    except Exception:
        return False


def _validate_archive_limits(source: Path, *, config: SlotFlowMarkItDownConfig) -> None:
    if source.suffix.lower() not in _ARCHIVE_EXTENSIONS or not zipfile.is_zipfile(source):
        return
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > config.max_archive_entries:
            raise MarkItDownConversionError(
                f"archive has too many entries: {len(entries)} > {config.max_archive_entries}",
            )
        nested_archives = [
            entry.filename
            for entry in entries
            if Path(entry.filename).suffix.lower() == ".zip"
        ]
        if nested_archives:
            raise MarkItDownConversionError(
                "nested ZIP archives are not converted; extract them into the workspace first",
            )
        total = sum(entry.file_size for entry in entries)
        if total > config.max_archive_uncompressed_bytes:
            raise MarkItDownConversionError(
                "archive uncompressed size exceeds limit: "
                f"{total} > {config.max_archive_uncompressed_bytes}",
            )


def _validate_vision_workload(source: Path, *, config: SlotFlowMarkItDownConfig) -> None:
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        pages = len(PdfReader(source).pages)
        if pages > config.vision_max_pages:
            raise MarkItDownConversionError(
                f"PDF has too many pages for Vision OCR: {pages} > {config.vision_max_pages}",
            )
        try:
            import fitz

            with fitz.open(source) as document:
                images = sum(len(page.get_images(full=True)) for page in document)
            if images > config.vision_max_images:
                raise MarkItDownConversionError(
                    "PDF has too many embedded images for Vision OCR: "
                    f"{images} > {config.vision_max_images}",
                )
        except MarkItDownConversionError:
            raise
        except Exception:
            pass
    elif suffix in _ARCHIVE_EXTENSIONS and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            media = sum(
                1
                for entry in archive.infolist()
                if entry.filename.lower().startswith(_OPENXML_MEDIA_PREFIXES)
            )
        if media > config.vision_max_images:
            raise MarkItDownConversionError(
                f"document has too many embedded images for Vision OCR: "
                f"{media} > {config.vision_max_images}",
            )


def _may_need_vision(source: Path, markdown: str) -> bool:
    if source.suffix.lower() in _IMAGE_EXTENSIONS:
        return True
    if source.suffix.lower() == ".pdf" and not markdown.strip():
        return True
    return False
