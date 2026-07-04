"use client";

import { LoaderCircle } from "lucide-react";

import {
  type WorkspaceReadRecord,
  resolveArtifactRawUrl,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

import { MarkdownContent } from "./markdown-content";
import { formatFileSize } from "./chat-format";

export type ArtifactViewMode = "code" | "preview";
type ArtifactPreviewType = {
  isHtml: boolean;
  isImage: boolean;
  isMarkdown: boolean;
  isPdf: boolean;
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
  const { isHtml, isImage, isMarkdown, isPdf } = getArtifactPreviewType(preview);

  if ((isHtml || isMarkdown) && viewMode === "code") {
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
      <div className="grid min-h-0 flex-1 place-items-center overflow-auto p-3">
        <img
          src={rawUrl}
          alt={preview.path.replace(/^artifacts\//, "")}
          className="max-h-full max-w-full rounded-md border object-contain"
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

export function getArtifactPreviewType(preview: WorkspaceReadRecord): ArtifactPreviewType {
  const path = preview.path;
  const mediaType = preview.media_type;
  const isHtml = mediaType.includes("html") || /\.html?$/i.test(path);
  const isMarkdown = mediaType.includes("markdown") || /\.(md|markdown)$/i.test(path);
  return {
    isHtml,
    isMarkdown,
    isImage: preview.kind === "image" || /\.(png|jpe?g|gif|webp)$/i.test(path),
    isPdf: preview.kind === "pdf" || /\.pdf$/i.test(path),
    canSwitchView: isHtml || isMarkdown,
  };
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
