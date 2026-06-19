"use client";

import { type PointerEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  ChevronRight,
  Code2,
  Download,
  ExternalLink,
  Eye,
  FileText,
  Folder,
  LoaderCircle,
  PanelRightClose,
  RefreshCw,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  type ThreadWorkspaceRecord,
  type WorkspaceEntryRecord,
  type WorkspaceReadRecord,
  listThreadWorkspaces,
  readArtifact,
  resolveArtifactRawUrl,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

import {
  ArtifactStage,
  getArtifactPreviewType,
  type ArtifactViewMode,
} from "./artifact-panel";
import { formatFileSize } from "./chat-format";

const minPanelWidth = 720;
const maxPanelWidth = 1100;
const panelWidthVariable = "--slotflow-artifact-panel-width";

function entryName(path: string): string {
  const segments = path.split("/").filter(Boolean);
  return segments[segments.length - 1] ?? path;
}

type WorkspacePanelProps = {
  open: boolean;
  activeThreadId: string | null;
  width: number;
  refreshKey?: number;
  onClose: () => void;
  onWidthChange: (width: number) => void;
};

/** Unified workspace panel: a left tree of threads (by title) → 用户上传 / 模型生成,
 *  with a right preview pane. Replaces the old artifact panel + directory modal. */
export function WorkspacePanel({
  open,
  activeThreadId,
  width,
  refreshKey,
  onClose,
  onWidthChange,
}: WorkspacePanelProps) {
  const animationFrameRef = useRef<number | null>(null);
  const [threads, setThreads] = useState<ThreadWorkspaceRecord[] | null>(null);
  const [isLoadingThreads, setIsLoadingThreads] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [preview, setPreview] = useState<WorkspaceReadRecord | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [viewMode, setViewMode] = useState<ArtifactViewMode>("preview");

  const visibleWidth = Math.max(width, minPanelWidth);

  const refresh = useCallback(async () => {
    setIsLoadingThreads(true);
    setError(null);
    try {
      setThreads(await listThreadWorkspaces());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载工作区失败");
    } finally {
      setIsLoadingThreads(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      void refresh();
    }
  }, [open, refreshKey, refresh]);

  // Auto-expand the active thread + its two groups when the panel opens.
  useEffect(() => {
    if (open && activeThreadId) {
      setExpanded((current) => {
        const next = new Set(current);
        next.add(activeThreadId);
        next.add(`${activeThreadId}:uploads`);
        next.add(`${activeThreadId}:generated`);
        return next;
      });
    }
  }, [open, activeThreadId, threads]);

  useEffect(() => {
    setViewMode("preview");
  }, [preview?.path]);

  const toggle = useCallback((key: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  const selectFile = useCallback(async (path: string) => {
    setSelectedPath(path);
    setIsLoadingPreview(true);
    setPreviewError(null);
    try {
      setPreview(await readArtifact(path));
    } catch (caught) {
      setPreview(null);
      setPreviewError(caught instanceof Error ? caught.message : "读取文件失败");
    } finally {
      setIsLoadingPreview(false);
    }
  }, []);

  function beginResize(event: PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const maxWidth = Math.min(maxPanelWidth, Math.max(minPanelWidth, window.innerWidth - 360));
    let nextWidth = width;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    function paint(value: number) {
      nextWidth = value;
      if (animationFrameRef.current !== null) {
        return;
      }
      animationFrameRef.current = window.requestAnimationFrame(() => {
        document.documentElement.style.setProperty(panelWidthVariable, `${nextWidth}px`);
        animationFrameRef.current = null;
      });
    }

    function move(moveEvent: globalThis.PointerEvent) {
      paint(
        Math.min(maxWidth, Math.max(minPanelWidth, startWidth - (moveEvent.clientX - startX))),
      );
    }

    function stop() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      if (animationFrameRef.current !== null) {
        window.cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = null;
      }
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      onWidthChange(nextWidth);
    }

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  if (!open) {
    return null;
  }

  const canSwitchView = preview ? getArtifactPreviewType(preview).canSwitchView : false;
  const rawUrl = preview ? resolveArtifactRawUrl(preview.path) : "";
  const downloadUrl = preview ? resolveArtifactRawUrl(preview.path, { download: true }) : "";

  return (
    <aside
      className="relative flex h-full min-w-0 shrink-0 flex-col bg-background py-4 pl-0 pr-4"
      style={{ width: `var(${panelWidthVariable}, ${visibleWidth}px)` }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        title="拖拽调整工作区面板宽度"
        className="group/resize-handle absolute inset-y-4 left-0 z-30 w-5 -translate-x-2 cursor-col-resize"
        onPointerDown={beginResize}
      >
        <span className="absolute left-1/2 top-1/2 h-16 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-border opacity-0 shadow-sm transition-opacity group-hover/resize-handle:opacity-100" />
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden rounded-lg border bg-background shadow-lg">
        <div className="flex w-64 shrink-0 flex-col border-r bg-muted/20">
          <div className="flex h-12 shrink-0 items-center justify-between gap-2 border-b px-3">
            <span className="text-sm font-medium">工作区</span>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              title="刷新"
              onClick={() => void refresh()}
            >
              <RefreshCw className="size-4" />
              <span className="sr-only">刷新</span>
            </Button>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <div className="p-2">
              {isLoadingThreads && !threads ? (
                <p className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
                  <LoaderCircle className="size-3.5 animate-spin" /> 加载中…
                </p>
              ) : error ? (
                <p className="px-2 py-3 text-xs text-destructive">{error}</p>
              ) : !threads || threads.length === 0 ? (
                <p className="px-2 py-3 text-xs text-muted-foreground">工作区暂无内容。</p>
              ) : (
                threads.map((thread) => (
                  <ThreadNode
                    key={thread.thread_id}
                    thread={thread}
                    expanded={expanded}
                    selectedPath={selectedPath}
                    onToggle={toggle}
                    onSelectFile={(path) => void selectFile(path)}
                  />
                ))
              )}
            </div>
          </ScrollArea>
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-12 shrink-0 items-center gap-1 border-b bg-muted/40 px-2">
            <span className="min-w-0 flex-1 truncate px-1 text-sm text-muted-foreground">
              {preview ? entryName(preview.path) : "选择左侧文件预览"}
            </span>
            {canSwitchView ? (
              <div className="flex shrink-0 items-center rounded-lg border bg-background p-0.5">
                <Button
                  type="button"
                  variant={viewMode === "code" ? "secondary" : "ghost"}
                  size="icon-xs"
                  title="源码"
                  className="size-7 rounded-md"
                  onClick={() => setViewMode("code")}
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
                  onClick={() => setViewMode("preview")}
                >
                  <Eye className="size-4" />
                  <span className="sr-only">预览</span>
                </Button>
              </div>
            ) : null}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              title="在新窗口打开"
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
            <a
              href={downloadUrl || undefined}
              title="下载"
              download
              className={cn(
                "inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground",
                !downloadUrl && "pointer-events-none opacity-50",
              )}
            >
              <Download className="size-4" />
              <span className="sr-only">下载</span>
            </a>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              title="关闭工作区面板"
              onClick={onClose}
            >
              <PanelRightClose className="size-4" />
              <span className="sr-only">关闭工作区面板</span>
            </Button>
          </div>
          <ArtifactStage
            preview={preview}
            previewError={previewError}
            isLoadingPreview={isLoadingPreview}
            viewMode={viewMode}
          />
        </div>
      </div>
    </aside>
  );
}

function ThreadNode({
  thread,
  expanded,
  selectedPath,
  onToggle,
  onSelectFile,
}: {
  thread: ThreadWorkspaceRecord;
  expanded: Set<string>;
  selectedPath: string | null;
  onToggle: (key: string) => void;
  onSelectFile: (path: string) => void;
}) {
  const open = expanded.has(thread.thread_id);
  return (
    <div>
      <button
        type="button"
        onClick={() => onToggle(thread.thread_id)}
        className="flex min-h-8 w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-sm hover:bg-muted"
      >
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
        />
        <Folder className="size-4 shrink-0 text-amber-500" />
        <span className="truncate" title={thread.title}>
          {thread.title || "未命名会话"}
        </span>
      </button>
      {open ? (
        <div className="pl-3">
          <GroupNode
            label="用户上传"
            groupKey={`${thread.thread_id}:uploads`}
            files={thread.uploads}
            expanded={expanded}
            selectedPath={selectedPath}
            onToggle={onToggle}
            onSelectFile={onSelectFile}
          />
          <GroupNode
            label="模型生成"
            groupKey={`${thread.thread_id}:generated`}
            files={thread.generated}
            expanded={expanded}
            selectedPath={selectedPath}
            onToggle={onToggle}
            onSelectFile={onSelectFile}
          />
        </div>
      ) : null}
    </div>
  );
}

function GroupNode({
  label,
  groupKey,
  files,
  expanded,
  selectedPath,
  onToggle,
  onSelectFile,
}: {
  label: string;
  groupKey: string;
  files: WorkspaceEntryRecord[];
  expanded: Set<string>;
  selectedPath: string | null;
  onToggle: (key: string) => void;
  onSelectFile: (path: string) => void;
}) {
  const open = expanded.has(groupKey);
  return (
    <div>
      <button
        type="button"
        onClick={() => onToggle(groupKey)}
        className="flex min-h-7 w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-xs text-muted-foreground hover:bg-muted"
      >
        <ChevronRight className={cn("size-3 shrink-0 transition-transform", open && "rotate-90")} />
        <span>{label}</span>
        <span className="ml-auto tabular-nums">{files.length}</span>
      </button>
      {open
        ? files.length === 0
          ? <p className="py-1 pl-7 text-xs text-muted-foreground/70">（空）</p>
          : files.map((file) => (
              <button
                key={file.path}
                type="button"
                onClick={() => onSelectFile(file.path)}
                className={cn(
                  "flex min-h-7 w-full items-center gap-1.5 rounded-md py-1 pl-7 pr-2 text-left text-sm hover:bg-muted",
                  selectedPath === file.path && "bg-muted text-foreground",
                )}
              >
                <FileText className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate" title={file.path}>
                  {entryName(file.path)}
                </span>
                {typeof file.size_bytes === "number" ? (
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatFileSize(file.size_bytes)}
                  </span>
                ) : null}
              </button>
            ))
        : null}
    </div>
  );
}
