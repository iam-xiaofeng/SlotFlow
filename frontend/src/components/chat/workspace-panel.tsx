"use client";

import {
  type PointerEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ChevronDown,
  Code2,
  Download,
  ExternalLink,
  Eye,
  FileText,
  Folder,
  LoaderCircle,
  PanelRightClose,
  RefreshCw,
  Upload,
  WandSparkles,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { entryName, formatFileSize } from "./chat-format";

const minPanelWidth = 560;
const maxPanelWidth = 1100;
const panelWidthVariable = "--slotflow-artifact-panel-width";

type WorkspacePanelProps = {
  open: boolean;
  selectedPath?: string | null;
  width: number;
  refreshKey?: number;
  onClose: () => void;
  onOpenFile?: (threadId: string, file: WorkspaceEntryRecord) => void;
  onWidthChange: (width: number) => void;
};

/** Unified workspace preview panel. The file directory lives in the title dropdown so
 *  preview width is not permanently consumed by a side tree. */
export function WorkspacePanel({
  open,
  selectedPath: externalSelectedPath,
  width,
  refreshKey,
  onClose,
  onOpenFile,
  onWidthChange,
}: WorkspacePanelProps) {
  const animationFrameRef = useRef<number | null>(null);
  const [threads, setThreads] = useState<ThreadWorkspaceRecord[] | null>(null);
  const [isLoadingThreads, setIsLoadingThreads] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  useEffect(() => {
    setViewMode("preview");
  }, [preview?.path]);

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

  useEffect(() => {
    if (!open || !externalSelectedPath || externalSelectedPath === selectedPath) {
      return;
    }
    void selectFile(externalSelectedPath);
  }, [externalSelectedPath, open, selectFile, selectedPath]);

  useEffect(() => {
    if (!open || selectedPath || externalSelectedPath || !threads) {
      return;
    }
    const firstFile = threads.flatMap((item) => [...item.generated, ...item.uploads])[0];
    if (firstFile?.kind === "file") {
      void selectFile(firstFile.path);
    }
  }, [externalSelectedPath, open, selectFile, selectedPath, threads]);

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
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-12 shrink-0 items-center gap-2 border-b bg-muted/40 px-2">
            <WorkspaceFileSelector
              threads={threads}
              isLoading={isLoadingThreads}
              error={error}
              selectedPath={selectedPath}
              previewPath={preview?.path ?? null}
              onRefresh={() => void refresh()}
              onSelectFile={(threadId, file) => {
                if (onOpenFile) {
                  onOpenFile(threadId, file);
                  return;
                }
                void selectFile(file.path);
              }}
            />
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

function WorkspaceFileSelector({
  threads,
  isLoading,
  error,
  selectedPath,
  previewPath,
  onRefresh,
  onSelectFile,
}: {
  threads: ThreadWorkspaceRecord[] | null;
  isLoading: boolean;
  error: string | null;
  selectedPath: string | null;
  previewPath: string | null;
  onRefresh: () => void;
  onSelectFile: (threadId: string, file: WorkspaceEntryRecord) => void;
}) {
  const currentPath = selectedPath ?? previewPath;
  const currentName = currentPath ? entryName(currentPath) : "选择工作区文件";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            className="min-w-0 flex-1 justify-start gap-2 px-2 text-left"
          />
        }
      >
        <FileText className="size-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-sm" title={currentPath ?? undefined}>
          {currentName}
        </span>
        <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        sideOffset={8}
        className="w-[min(28rem,calc(100vw-2rem))] rounded-xl p-0"
      >
        <div className="flex h-10 items-center justify-between gap-2 border-b px-3">
          <span className="text-sm font-medium">工作区</span>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            title="刷新"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onRefresh();
            }}
          >
            <RefreshCw className="size-4" />
            <span className="sr-only">刷新</span>
          </Button>
        </div>
        <WorkspaceFileMenu
          threads={threads}
          isLoading={isLoading}
          error={error}
          selectedPath={currentPath}
          onSelectFile={onSelectFile}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function WorkspaceFileMenu({
  threads,
  isLoading,
  error,
  selectedPath,
  onSelectFile,
}: {
  threads: ThreadWorkspaceRecord[] | null;
  isLoading: boolean;
  error: string | null;
  selectedPath: string | null;
  onSelectFile: (threadId: string, file: WorkspaceEntryRecord) => void;
}) {
  if (isLoading && !threads) {
    return (
      <div className="flex items-center gap-2 px-3 py-6 text-sm text-muted-foreground">
        <LoaderCircle className="size-4 animate-spin" />
        加载中…
      </div>
    );
  }

  if (error) {
    return <div className="px-3 py-3 text-sm text-destructive">{error}</div>;
  }

  if (!threads || threads.length === 0) {
    return <div className="px-3 py-6 text-sm text-muted-foreground">工作区暂无内容。</div>;
  }

  return (
    <ScrollArea className="max-h-[min(70vh,34rem)]">
      <div className="space-y-2 p-2">
        {threads.map((thread) => (
          <div key={thread.thread_id} className="rounded-lg border bg-background p-2">
            <div className="mb-1 flex items-center gap-2 px-1 py-1 text-sm font-medium">
              <Folder className="size-4 shrink-0 text-amber-500" />
              <span className="min-w-0 flex-1 truncate" title={thread.title}>
                {thread.title || "未命名会话"}
              </span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {thread.uploads.length + thread.generated.length}
              </span>
            </div>
            <WorkspaceMenuBucket
              icon={<Upload className="size-3.5" />}
              label="用户上传"
              threadId={thread.thread_id}
              files={thread.uploads}
              selectedPath={selectedPath}
              onSelectFile={onSelectFile}
            />
            <WorkspaceMenuBucket
              icon={<WandSparkles className="size-3.5" />}
              label="Agent 产物"
              threadId={thread.thread_id}
              files={thread.generated}
              selectedPath={selectedPath}
              onSelectFile={onSelectFile}
            />
          </div>
        ))}
      </div>
    </ScrollArea>
  );
}

function WorkspaceMenuBucket({
  icon,
  label,
  threadId,
  files,
  selectedPath,
  onSelectFile,
}: {
  icon: ReactNode;
  label: string;
  threadId: string;
  files: WorkspaceEntryRecord[];
  selectedPath: string | null;
  onSelectFile: (threadId: string, file: WorkspaceEntryRecord) => void;
}) {
  return (
    <div className="pt-1">
      <div className="flex items-center gap-1.5 px-1 py-1 text-xs font-medium text-muted-foreground">
        {icon}
        <span>{label}</span>
        <span className="ml-auto tabular-nums">{files.length}</span>
      </div>
      {files.length === 0 ? (
        <div className="rounded-md bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground">
          空
        </div>
      ) : (
        files.map((file) => (
          <DropdownMenuItem
            key={file.path}
            className={cn(
              "min-w-0 gap-2 rounded-md py-2",
              selectedPath === file.path && "bg-muted text-foreground",
            )}
            onClick={() => onSelectFile(threadId, file)}
          >
            <FileText className="size-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate" title={file.path}>
              {entryName(file.path)}
            </span>
            {typeof file.size_bytes === "number" ? (
              <span className="shrink-0 text-xs text-muted-foreground">
                {formatFileSize(file.size_bytes)}
              </span>
            ) : null}
          </DropdownMenuItem>
        ))
      )}
    </div>
  );
}
