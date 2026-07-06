"use client";

import {
  FileArchive,
  FileSpreadsheet,
  FileText,
  FileWarning,
  LoaderCircle,
  Presentation,
  Workflow,
} from "lucide-react";

import {
  type WorkspaceReadRecord,
  resolveArtifactRawUrl,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

import { MarkdownContent } from "./markdown-content";
import { formatFileSize } from "./chat-format";

export type ArtifactViewMode = "code" | "preview";
type ArtifactPreviewType = {
  hasTextContent: boolean;
  isHtml: boolean;
  isImage: boolean;
  isDiagram: boolean;
  isMarkdown: boolean;
  isOfficeLike: boolean;
  isPdf: boolean;
  isSourceText: boolean;
  canSwitchView: boolean;
};

export function ArtifactStage({
  preview,
  previewError,
  isLoadingPreview,
  viewMode,
}: {
  preview: WorkspaceReadRecord | null;
  previewError: string | null;
  isLoadingPreview: boolean;
  viewMode: ArtifactViewMode;
}) {
  if (isLoadingPreview) {
    return (
      <div className="grid min-h-0 flex-1 place-items-center text-sm text-muted-foreground">
        <span className="inline-flex items-center gap-2">
          <LoaderCircle className="size-4 animate-spin" />
          正在读取产物
        </span>
      </div>
    );
  }

  if (previewError) {
    return (
      <div className="min-h-0 flex-1 p-4">
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-3 text-sm text-destructive">
          {previewError}
        </div>
      </div>
    );
  }

  if (!preview) {
    return (
      <div className="grid min-h-0 flex-1 place-items-center p-6 text-center text-sm text-muted-foreground">
        从上方选择一个工作区文件开始浏览
      </div>
    );
  }

  const rawUrl = resolveArtifactRawUrl(preview.path);
  const previewType = getArtifactPreviewType(preview);
  const {
    hasTextContent,
    isHtml,
    isImage,
    isDiagram,
    isMarkdown,
    isOfficeLike,
    isPdf,
  } = previewType;

  if (viewMode === "code" && hasTextContent) {
    return <CodePreview preview={preview} />;
  }

  if (isHtml) {
    const srcDoc = preview.content
      ? buildHtmlPreviewDocument(preview.content, rawUrl)
      : undefined;

    return (
      <div className="min-h-0 flex-1">
        <iframe
          title={preview.path}
          src={srcDoc ? undefined : rawUrl}
          srcDoc={srcDoc}
          sandbox="allow-scripts allow-forms allow-popups allow-downloads"
          className="size-full border-0 bg-background"
        />
      </div>
    );
  }

  if (isPdf) {
    return (
      <div className="min-h-0 flex-1 p-3">
        <iframe
          title={preview.path}
          src={rawUrl}
          sandbox="allow-scripts allow-forms allow-popups allow-downloads"
          className="size-full rounded-md border bg-background"
        />
      </div>
    );
  }

  if (isImage) {
    return (
      <div className="grid min-h-0 flex-1 place-items-center overflow-auto bg-[radial-gradient(circle_at_1px_1px,var(--border)_1px,transparent_0)] bg-[length:18px_18px] p-3">
        <img
          src={rawUrl}
          alt={preview.path.replace(/^artifacts\//, "")}
          className="max-h-full max-w-full rounded-md border bg-background object-contain shadow-sm"
        />
      </div>
    );
  }

  if (isMarkdown) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        {preview.content?.trim() ? (
          <MarkdownContent
            className="min-h-0 flex-1 overflow-auto px-5 py-4"
            compact
            content={preview.content}
          />
        ) : (
          <CodePreview preview={preview} />
        )}
      </div>
    );
  }

  if (isOfficeLike || isDiagram) {
    return <StructuredFilePreview preview={preview} />;
  }

  if (preview.kind === "binary") {
    return <FileDetailPreview preview={preview} />;
  }

  return <CodePreview preview={preview} />;
}

function CodePreview({ preview }: { preview: WorkspaceReadRecord }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b px-3 py-2 text-xs text-muted-foreground">
        <span className="truncate">
          {preview.kind} · {preview.media_type} · {formatFileSize(preview.size_bytes)}
        </span>
      </div>
      <pre
        className={cn(
          "min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words bg-muted/20 p-4 text-xs leading-5",
          "font-mono",
        )}
      >
        {preview.content?.trim()
          ? preview.content
          : preview.warning || JSON.stringify(preview.metadata, null, 2)}
      </pre>
    </div>
  );
}

function StructuredFilePreview({ preview }: { preview: WorkspaceReadRecord }) {
  const title = structuredPreviewTitle(preview);
  const Icon = structuredPreviewIcon(preview);
  const metadataEntries = Object.entries(preview.metadata ?? {}).filter(
    ([, value]) => value !== null && value !== undefined && value !== "",
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-muted/15">
      <div className="shrink-0 border-b bg-background/85 px-4 py-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-md border bg-muted/40 text-muted-foreground">
            <Icon className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h2 className="truncate text-sm font-semibold">{title}</h2>
              <span className="rounded-md border bg-background px-1.5 py-0.5 text-[11px] uppercase text-muted-foreground">
                {formatFileSize(preview.size_bytes)}
              </span>
            </div>
            <p className="mt-1 truncate text-xs text-muted-foreground" title={preview.path}>
              {preview.path}
            </p>
          </div>
        </div>
        {metadataEntries.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {metadataEntries.slice(0, 8).map(([key, value]) => (
              <span
                key={key}
                className="rounded-md border bg-background px-2 py-1 text-xs text-muted-foreground"
              >
                {key}: {metadataValueLabel(value)}
              </span>
            ))}
          </div>
        ) : null}
        {preview.warning ? (
          <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs leading-5 text-amber-800 dark:text-amber-200">
            <FileWarning className="mt-0.5 size-4 shrink-0" />
            <span>{preview.warning}</span>
          </div>
        ) : null}
      </div>
      {preview.content?.trim() ? (
        <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-xs leading-5">
          {preview.content}
        </pre>
      ) : (
        <div className="grid min-h-0 flex-1 place-items-center p-6 text-center text-sm text-muted-foreground">
          未提取到可预览文本，可使用打开或下载查看原文件。
        </div>
      )}
    </div>
  );
}

function FileDetailPreview({ preview }: { preview: WorkspaceReadRecord }) {
  const metadataEntries = Object.entries(preview.metadata ?? {}).filter(
    ([, value]) => value !== null && value !== undefined && value !== "",
  );

  return (
    <div className="grid min-h-0 flex-1 place-items-center bg-muted/15 p-5">
      <div className="w-full max-w-md rounded-lg border bg-background p-4 shadow-sm">
        <div className="flex items-start gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-md border bg-muted/40 text-muted-foreground">
            <FileArchive className="size-5" />
          </span>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold" title={preview.path}>
              {preview.path.replace(/^artifacts\//, "")}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {preview.media_type} · {formatFileSize(preview.size_bytes)}
            </p>
          </div>
        </div>
        {preview.warning ? (
          <div className="mt-4 rounded-md border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs leading-5 text-amber-800 dark:text-amber-200">
            {preview.warning}
          </div>
        ) : null}
        {metadataEntries.length > 0 ? (
          <dl className="mt-4 grid gap-2 text-xs">
            {metadataEntries.map(([key, value]) => (
              <div key={key} className="flex min-w-0 items-center justify-between gap-3">
                <dt className="shrink-0 text-muted-foreground">{key}</dt>
                <dd className="min-w-0 truncate font-mono">{metadataValueLabel(value)}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>
    </div>
  );
}

export function getArtifactPreviewType(preview: WorkspaceReadRecord): ArtifactPreviewType {
  const path = preview.path;
  const mediaType = preview.media_type;
  const hasTextContent = Boolean(preview.content?.trim());
  const isHtml = mediaType.includes("html") || /\.html?$/i.test(path);
  const isMarkdown = mediaType.includes("markdown") || /\.(md|markdown)$/i.test(path);
  const isDiagram = preview.kind === "diagram" || /\.(drawio)$/i.test(path);
  const isOfficeLike =
    preview.kind === "document" ||
    preview.kind === "spreadsheet" ||
    preview.kind === "presentation";
  const isSourceText =
    preview.kind === "text" ||
    /\.(css|csv|graphql|jsx?|json|log|mjs|sql|toml|tsx?|ya?ml)$/i.test(path);
  const isImage = preview.kind === "image" || /\.(png|jpe?g|gif|webp|svg)$/i.test(path);
  return {
    hasTextContent,
    isHtml,
    isMarkdown,
    isDiagram,
    isOfficeLike,
    isImage,
    isPdf: preview.kind === "pdf" || /\.pdf$/i.test(path),
    isSourceText,
    canSwitchView:
      hasTextContent &&
      (isHtml || isMarkdown || isDiagram || isOfficeLike || isImage),
  };
}

function structuredPreviewTitle(preview: WorkspaceReadRecord): string {
  if (preview.kind === "spreadsheet") {
    return "Spreadsheet Preview";
  }
  if (preview.kind === "presentation") {
    return "Presentation Preview";
  }
  if (preview.kind === "diagram") {
    return "Diagram Source";
  }
  return "Document Preview";
}

function structuredPreviewIcon(preview: WorkspaceReadRecord) {
  if (preview.kind === "spreadsheet") {
    return FileSpreadsheet;
  }
  if (preview.kind === "presentation") {
    return Presentation;
  }
  if (preview.kind === "diagram") {
    return Workflow;
  }
  return FileText;
}

function metadataValueLabel(value: unknown): string {
  if (Array.isArray(value)) {
    return value.join(", ");
  }
  if (typeof value === "object" && value !== null) {
    return JSON.stringify(value);
  }
  return String(value);
}

function buildHtmlPreviewDocument(content: string, rawUrl: string): string {
  const previewStyle = [
    "<style data-slotflow-preview>",
    "html,body{margin:0;min-width:0;width:100%;background:transparent;}",
    "body{box-sizing:border-box;overflow-wrap:anywhere;}",
    "*,*::before,*::after{box-sizing:border-box;}",
    "img,svg,canvas,video,iframe,table{max-width:100%;}",
    "</style>",
  ].join("");
  const baseTag = `<base href="${escapeHtmlAttribute(rawUrl)}">`;

  if (/<head[\s>]/i.test(content)) {
    return content.replace(/<head([^>]*)>/i, `<head$1>${baseTag}${previewStyle}`);
  }

  return `<!doctype html><html><head>${baseTag}${previewStyle}</head><body>${content}</body></html>`;
}

function escapeHtmlAttribute(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
