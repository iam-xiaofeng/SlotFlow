"use client";

import { type PointerEvent, useRef } from "react";
import {
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

import { MarkdownContent } from "./markdown-content";

type ArtifactWorkspacePanelProps = {
  preview: WorkspaceReadRecord | null;
  previewError: string | null;
  isLoadingPreview: boolean;
  width: number;
  onWidthChange: (width: number) => void;
};

type ArtifactWorkspaceToolbarProps = {
  artifacts: WorkspaceEntryRecord[];
  activePath: string | null;
  onClose: () => void;
  onPreviewArtifact: (artifact: WorkspaceEntryRecord) => void;
};

const minPanelWidth = 360;
const maxPanelWidth = 960;
const artifactPanelWidthVariable = "--slotflow-artifact-panel-width";

export function ArtifactWorkspaceToolbar({
  artifacts,
  activePath,
  onClose,
  onPreviewArtifact,
}: ArtifactWorkspaceToolbarProps) {
  const selectedPath = activePath ?? artifacts[0]?.path ?? "";

  return (
    <div className="flex h-14 min-w-0 items-center gap-2 border-l px-3">
      <div className="shrink-0 text-sm font-semibold">产物</div>
      <Select
        value={selectedPath}
        onValueChange={(path) => {
          const artifact = artifacts.find((item) => item.path === path);
          if (artifact) {
            onPreviewArtifact(artifact);
          }
        }}
      >
        <SelectTrigger className="min-w-0 flex-1" size="sm">
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
      <Button type="button" variant="ghost" size="icon-sm" title="关闭产物面板" onClick={onClose}>
        <PanelRightClose className="size-4" />
        <span className="sr-only">关闭产物面板</span>
      </Button>
    </div>
  );
}

export function ArtifactWorkspacePanel({
  preview,
  previewError,
  isLoadingPreview,
  width,
  onWidthChange,
}: ArtifactWorkspacePanelProps) {
  const animationFrameRef = useRef<number | null>(null);

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
      className="relative flex h-full min-w-0 shrink-0 flex-col border-l bg-background"
      style={{ width: `var(${artifactPanelWidthVariable}, ${width}px)` }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        title="拖拽调整产物面板宽度"
        className="absolute inset-y-0 left-0 w-2 -translate-x-1 cursor-col-resize"
        onPointerDown={beginResize}
      />
      <div className="grid min-h-0 flex-1">
        <ArtifactStage
          preview={preview}
          previewError={previewError}
          isLoadingPreview={isLoadingPreview}
        />
      </div>
    </aside>
  );
}

function ArtifactStage({
  preview,
  previewError,
  isLoadingPreview,
}: {
  preview: WorkspaceReadRecord | null;
  previewError: string | null;
  isLoadingPreview: boolean;
}) {
  if (isLoadingPreview) {
    return (
      <div className="grid place-items-center text-sm text-muted-foreground">
        <span className="inline-flex items-center gap-2">
          <LoaderCircle className="size-4 animate-spin" />
          正在读取产物
        </span>
      </div>
    );
  }

  if (previewError) {
    return (
      <div className="p-4">
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-3 text-sm text-destructive">
          {previewError}
        </div>
      </div>
    );
  }

  if (!preview) {
    return (
      <div className="grid place-items-center p-6 text-center text-sm text-muted-foreground">
        点击左侧产物文件开始浏览
      </div>
    );
  }

  const rawUrl = resolveArtifactRawUrl(preview.path);
  const isHtml = preview.media_type.includes("html") || /\.html?$/i.test(preview.path);
  const isImage = preview.kind === "image" || /\.(png|jpe?g|gif|webp)$/i.test(preview.path);
  const isMarkdown = preview.media_type.includes("markdown") || /\.(md|markdown)$/i.test(preview.path);
  const isPdf = preview.kind === "pdf" || /\.pdf$/i.test(preview.path);

  if (isHtml || isPdf) {
    return (
      <div className="min-h-0 p-3">
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
      <div className="grid min-h-0 place-items-center overflow-auto p-3">
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
      <div className="flex min-h-0 flex-col">
        <div className="flex shrink-0 items-center justify-between gap-2 border-b px-3 py-2 text-xs text-muted-foreground">
          <span className="truncate">
            {preview.kind} · {preview.media_type} · {formatBytes(preview.size_bytes)}
          </span>
          <a
            href={rawUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex shrink-0 items-center gap-1 hover:text-foreground"
          >
            <ExternalLink className="size-3.5" />
            打开
          </a>
        </div>
        {preview.content?.trim() ? (
          <MarkdownContent
            className="min-h-0 flex-1 overflow-auto p-4"
            compact
            content={preview.content}
          />
        ) : (
          <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-4 text-xs leading-5">
            {preview.warning || JSON.stringify(preview.metadata, null, 2)}
          </pre>
        )}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b px-3 py-2 text-xs text-muted-foreground">
        <span className="truncate">
          {preview.kind} · {preview.media_type} · {formatBytes(preview.size_bytes)}
        </span>
        <a
          href={rawUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex shrink-0 items-center gap-1 hover:text-foreground"
        >
          <ExternalLink className="size-3.5" />
          打开
        </a>
      </div>
      <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-4 text-xs leading-5">
        {preview.content?.trim()
          ? preview.content
          : preview.warning || JSON.stringify(preview.metadata, null, 2)}
      </pre>
    </div>
  );
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
