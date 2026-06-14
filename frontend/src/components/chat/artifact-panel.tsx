"use client";

import { type PointerEvent } from "react";
import {
  ExternalLink,
  FileText,
  ImageIcon,
  LoaderCircle,
  PanelRightClose,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  type WorkspaceEntryRecord,
  type WorkspaceReadRecord,
  resolveArtifactRawUrl,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

type ArtifactWorkspacePanelProps = {
  artifacts: WorkspaceEntryRecord[];
  preview: WorkspaceReadRecord | null;
  previewError: string | null;
  isLoadingPreview: boolean;
  width: number;
  onClose: () => void;
  onPreviewArtifact: (artifact: WorkspaceEntryRecord) => void;
  onWidthChange: (width: number) => void;
};

const minPanelWidth = 360;
const maxPanelWidth = 960;

export function ArtifactWorkspacePanel({
  artifacts,
  preview,
  previewError,
  isLoadingPreview,
  width,
  onClose,
  onPreviewArtifact,
  onWidthChange,
}: ArtifactWorkspacePanelProps) {
  function beginResize(event: PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const maxWidth = Math.min(maxPanelWidth, Math.max(minPanelWidth, window.innerWidth - 420));

    function handleMove(moveEvent: globalThis.PointerEvent) {
      const nextWidth = clamp(startWidth - (moveEvent.clientX - startX), minPanelWidth, maxWidth);
      onWidthChange(nextWidth);
    }

    function stopResize() {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", stopResize);
    }

    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", stopResize);
  }

  return (
    <aside
      className="relative flex h-full min-w-0 shrink-0 flex-col border-l bg-background"
      style={{ width }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        title="拖拽调整产物面板宽度"
        className="absolute inset-y-0 left-0 w-2 -translate-x-1 cursor-col-resize"
        onPointerDown={beginResize}
      />
      <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b px-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">产物</div>
          <div className="truncate text-xs text-muted-foreground">
            {preview ? preview.path.replace(/^artifacts\//, "") : `${artifacts.length} 个文件`}
          </div>
        </div>
        <Button type="button" variant="ghost" size="icon-sm" title="关闭产物面板" onClick={onClose}>
          <PanelRightClose className="size-4" />
          <span className="sr-only">关闭产物面板</span>
        </Button>
      </header>

      <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)]">
        <ArtifactStrip
          artifacts={artifacts}
          activePath={preview?.path ?? null}
          onPreviewArtifact={onPreviewArtifact}
        />
        <ArtifactStage
          preview={preview}
          previewError={previewError}
          isLoadingPreview={isLoadingPreview}
        />
      </div>
    </aside>
  );
}

function ArtifactStrip({
  artifacts,
  activePath,
  onPreviewArtifact,
}: {
  artifacts: WorkspaceEntryRecord[];
  activePath: string | null;
  onPreviewArtifact: (artifact: WorkspaceEntryRecord) => void;
}) {
  if (artifacts.length === 0) {
    return (
      <div className="border-b px-3 py-3 text-sm text-muted-foreground">
        暂无产物文件
      </div>
    );
  }

  return (
    <div className="border-b p-2">
      <ScrollArea className="max-h-36">
        <div className="flex flex-col gap-1 pr-2">
          {artifacts.map((artifact) => {
            const isActive = artifact.path === activePath;
            return (
              <button
                key={artifact.path}
                type="button"
                disabled={artifact.kind !== "file"}
                className={cn(
                  "flex min-w-0 items-center gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-muted disabled:opacity-50",
                  isActive && "bg-muted",
                )}
                onClick={() => onPreviewArtifact(artifact)}
              >
                <ArtifactIcon path={artifact.path} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">
                    {artifact.path.replace(/^artifacts\//, "")}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {artifact.kind}
                    {typeof artifact.size_bytes === "number"
                      ? ` · ${formatBytes(artifact.size_bytes)}`
                      : ""}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </ScrollArea>
    </div>
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

function ArtifactIcon({ path }: { path: string }) {
  if (/\.(png|jpe?g|gif|webp)$/i.test(path)) {
    return <ImageIcon className="size-5 shrink-0 text-muted-foreground" />;
  }
  return <FileText className="size-5 shrink-0 text-muted-foreground" />;
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
