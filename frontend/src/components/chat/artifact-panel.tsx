"use client";

import { type PointerEvent, useEffect, useRef, useState } from "react";
import {
  Code2,
  Download,
  Eye,
  ExternalLink,
  LoaderCircle,
  PanelRightClose,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  type WorkspaceEntryRecord,
  type WorkspaceReadRecord,
  resolveArtifactRawUrl,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

import { MarkdownContent } from "./markdown-content";
import { formatFileSize } from "./chat-format";

type ArtifactWorkspacePanelProps = {
  activePath: string | null;
  artifacts: WorkspaceEntryRecord[];
  preview: WorkspaceReadRecord | null;
  previewError: string | null;
  isLoadingPreview: boolean;
  width: number;
  onClose: () => void;
  onPreviewArtifact: (artifact: WorkspaceEntryRecord) => void;
  onWidthChange: (width: number) => void;
};

type ArtifactWorkspaceToolbarProps = {
  activePath: string | null;
  artifacts: WorkspaceEntryRecord[];
  preview: WorkspaceReadRecord | null;
  viewMode: ArtifactViewMode;
  onClose: () => void;
  onPreviewArtifact: (artifact: WorkspaceEntryRecord) => void;
  onViewModeChange: (mode: ArtifactViewMode) => void;
};

export type ArtifactViewMode = "code" | "preview";
type ArtifactPreviewType = {
  isHtml: boolean;
  isImage: boolean;
  isMarkdown: boolean;
  isPdf: boolean;
  canSwitchView: boolean;
};

const minPanelWidth = 640;
const maxPanelWidth = 960;
const artifactPanelWidthVariable = "--slotflow-artifact-panel-width";

export function ArtifactWorkspacePanel({
  activePath,
  artifacts,
  preview,
  previewError,
  isLoadingPreview,
  width,
  onClose,
  onPreviewArtifact,
  onWidthChange,
}: ArtifactWorkspacePanelProps) {
  const animationFrameRef = useRef<number | null>(null);
  const [viewMode, setViewMode] = useState<ArtifactViewMode>("preview");
  const visibleWidth = Math.max(width, minPanelWidth);

  useEffect(() => {
    setViewMode("preview");
  }, [preview?.path]);

  function beginResize(event: PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const maxWidth = Math.min(maxPanelWidth, Math.max(minPanelWidth, window.innerWidth - 420));
    let nextWidth = width;

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    function paintWidth(value: number) {
      nextWidth = value;
      if (animationFrameRef.current !== null) {
        return;
      }
      animationFrameRef.current = window.requestAnimationFrame(() => {
        document.documentElement.style.setProperty(
          artifactPanelWidthVariable,
          `${nextWidth}px`,
        );
        animationFrameRef.current = null;
      });
    }

    function handleMove(moveEvent: globalThis.PointerEvent) {
      paintWidth(clamp(startWidth - (moveEvent.clientX - startX), minPanelWidth, maxWidth));
    }

    function stopResize() {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", stopResize);
      if (animationFrameRef.current !== null) {
        window.cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = null;
      }
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      onWidthChange(nextWidth);
    }

    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", stopResize);
  }

  return (
    <aside
      className="relative flex h-full min-w-0 shrink-0 flex-col bg-background py-4 pl-0 pr-4"
      style={{ width: `var(${artifactPanelWidthVariable}, ${visibleWidth}px)` }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        title="拖拽调整产物面板宽度"
        className="group/resize-handle absolute inset-y-4 left-0 z-30 w-5 -translate-x-2 cursor-col-resize"
        onPointerDown={beginResize}
      >
        <span className="absolute left-1/2 top-1/2 h-16 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-border opacity-0 shadow-sm transition-opacity group-hover/resize-handle:opacity-100" />
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border bg-background shadow-lg">
        <ArtifactWorkspaceToolbar
          activePath={activePath}
          artifacts={artifacts}
          preview={preview}
          viewMode={viewMode}
          onClose={onClose}
          onPreviewArtifact={onPreviewArtifact}
          onViewModeChange={setViewMode}
        />
        <ArtifactStage
          preview={preview}
          previewError={previewError}
          isLoadingPreview={isLoadingPreview}
          viewMode={viewMode}
        />
      </div>
    </aside>
  );
}

export function ArtifactWorkspaceToolbar({
  activePath,
  artifacts,
  preview,
  viewMode,
  onClose,
  onPreviewArtifact,
  onViewModeChange,
}: ArtifactWorkspaceToolbarProps) {
  const selectedPath = activePath ?? artifacts[0]?.path ?? "";
  const rawUrl = preview ? resolveArtifactRawUrl(preview.path) : "";
  const downloadUrl = preview ? resolveArtifactRawUrl(preview.path, { download: true }) : "";
  const canSwitchView = preview ? getArtifactPreviewType(preview).canSwitchView : false;

  return (
    <div className="flex h-14 shrink-0 items-center gap-2 border-b bg-muted/50 px-2">
      <Select
        value={selectedPath}
        onValueChange={(path) => {
          const artifact = artifacts.find((item) => item.path === path);
          if (artifact) {
            onPreviewArtifact(artifact);
          }
        }}
      >
        <SelectTrigger
          className="min-w-0 flex-1 border-none bg-transparent shadow-none"
          size="sm"
        >
          <SelectValue placeholder={`${artifacts.length} 个文件`} />
        </SelectTrigger>
        <SelectContent align="start" className="max-w-96">
          <SelectGroup>
            {artifacts.map((artifact) => (
              <SelectItem key={artifact.path} value={artifact.path}>
                <span className="min-w-0 truncate">
                  {artifact.path.replace(/^artifacts\//, "")}
                </span>
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      {canSwitchView ? (
        <div className="mx-auto flex shrink-0 items-center rounded-lg border bg-background p-0.5">
          <Button
            type="button"
            variant={viewMode === "code" ? "secondary" : "ghost"}
            size="icon-xs"
            title="源码"
            className="size-7 rounded-md"
            onClick={() => onViewModeChange("code")}
          >
            <Code2 className="size-4" />
            <span className="sr-only">源码</span>
          </Button>
          <Button
            type="button"
            variant={viewMode === "preview" ? "secondary" : "ghost"}
            size="icon-xs"
            title="预览"
            className="size-7 rounded-md"
            onClick={() => onViewModeChange("preview")}
          >
            <Eye className="size-4" />
            <span className="sr-only">预览</span>
          </Button>
        </div>
      ) : null}

      <div className="ml-auto flex shrink-0 items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          title="在新窗口打开"
          className="size-8 text-muted-foreground hover:text-foreground"
          disabled={!rawUrl}
          onClick={() => {
            if (rawUrl) {
              window.open(rawUrl, "_blank", "noopener,noreferrer");
            }
          }}
        >
          <ExternalLink className="size-4" />
          <span className="sr-only">在新窗口打开</span>
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          title="下载"
          className="size-8 text-muted-foreground hover:text-foreground"
          disabled={!downloadUrl}
          onClick={() => {
            if (preview && downloadUrl) {
              downloadArtifact(downloadUrl, preview.path);
            }
          }}
        >
          <Download className="size-4" />
          <span className="sr-only">下载</span>
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          title="关闭产物面板"
          className="size-8 text-muted-foreground hover:text-foreground"
          onClick={onClose}
        >
          <PanelRightClose className="size-4" />
          <span className="sr-only">关闭产物面板</span>
        </Button>
      </div>
    </div>
  );
}

function downloadArtifact(url: string, path: string) {
  const link = document.createElement("a");
  link.href = url;
  link.download = path.split("/").pop() || "artifact";
  link.rel = "noreferrer";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

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
        点击产物文件开始浏览
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

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
