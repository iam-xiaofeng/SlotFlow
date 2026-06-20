"use client";

import { useEffect, useMemo, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import {
  ChevronRight,
  FileText,
  Folder,
  LoaderCircle,
  type LucideIcon,
  Search,
  Upload,
  WandSparkles,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  type ThreadWorkspaceRecord,
  type WorkspaceEntryRecord,
  listThreadWorkspaces,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

import { entryName, formatFileSize } from "./chat-format";

type WorkspaceDirectoryModalProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onOpenFile: (threadId: string, file: WorkspaceEntryRecord) => void;
};

export function WorkspaceDirectoryModal({
  open,
  onOpenChange,
  onOpenFile,
}: WorkspaceDirectoryModalProps) {
  const [threads, setThreads] = useState<ThreadWorkspaceRecord[]>([]);
  const [query, setQuery] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedThreadIds, setExpandedThreadIds] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    if (!open) {
      return;
    }
    let active = true;
    async function refresh() {
      setIsLoading(true);
      setError(null);
      setExpandedThreadIds(new Set());
      try {
        const nextThreads = await listThreadWorkspaces();
        if (active) {
          setThreads(nextThreads);
        }
      } catch (caught) {
        if (active) {
          setError(caught instanceof Error ? caught.message : "加载工作区失败");
          setThreads([]);
        }
      } finally {
        if (active) {
          setIsLoading(false);
        }
      }
    }
    void refresh();
    return () => {
      active = false;
    };
  }, [open]);

  const filteredThreads = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) {
      return threads;
    }
    return threads
      .map((thread) => ({
        ...thread,
        generated: thread.generated.filter(
          (file) =>
            file.path.toLowerCase().includes(q) ||
            entryName(file.path).toLowerCase().includes(q) ||
            thread.title.toLowerCase().includes(q),
        ),
        uploads: thread.uploads.filter(
          (file) =>
            file.path.toLowerCase().includes(q) ||
            entryName(file.path).toLowerCase().includes(q) ||
            thread.title.toLowerCase().includes(q),
        ),
      }))
      .filter((thread) => thread.generated.length > 0 || thread.uploads.length > 0);
  }, [query, threads]);

  const totalFiles = threads.reduce(
    (sum, thread) => sum + thread.generated.length + thread.uploads.length,
    0,
  );
  const hasQuery = query.trim().length > 0;

  function toggleThread(threadId: string) {
    setExpandedThreadIds((current) => {
      const next = new Set(current);
      if (next.has(threadId)) {
        next.delete(threadId);
      } else {
        next.add(threadId);
      }
      return next;
    });
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/45 transition-opacity duration-200 data-ending-style:opacity-0 data-starting-style:opacity-0 supports-backdrop-filter:backdrop-blur-sm" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-50 flex h-[min(86vh,52rem)] w-[min(92vw,64rem)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border bg-background text-foreground shadow-2xl transition-all duration-200 data-ending-style:scale-[0.98] data-ending-style:opacity-0 data-starting-style:scale-[0.98] data-starting-style:opacity-0">
          <div className="flex h-16 shrink-0 items-center gap-3 border-b px-5">
            <div className="min-w-0 flex-1">
              <Dialog.Title className="text-xl font-semibold">工作区</Dialog.Title>
              <p className="text-sm text-muted-foreground">
                {totalFiles > 0 ? `${totalFiles} 个文件` : "暂无文件"}
              </p>
            </div>
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索文件或对话…"
                className="h-10 w-full rounded-lg border border-input bg-background pl-9 pr-3 text-sm outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
              />
            </div>
            <Dialog.Close
              render={<Button type="button" size="icon-sm" variant="ghost" />}
            >
              <X className="size-4" />
              <span className="sr-only">关闭工作区</span>
            </Dialog.Close>
          </div>

          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-4 p-5">
              {isLoading ? (
                <div className="flex items-center justify-center gap-2 py-20 text-sm text-muted-foreground">
                  <LoaderCircle className="size-4 animate-spin" />
                  加载中…
                </div>
              ) : error ? (
                <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {error}
                </div>
              ) : filteredThreads.length === 0 ? (
                <div className="py-20 text-center text-sm text-muted-foreground">
                  {query.trim() ? "没有匹配的文件" : "工作区暂无文件"}
                </div>
              ) : (
                filteredThreads.map((thread) => (
                  <ThreadWorkspaceGroup
                    key={thread.thread_id}
                    thread={thread}
                    expanded={hasQuery || expandedThreadIds.has(thread.thread_id)}
                    onToggle={() => toggleThread(thread.thread_id)}
                    onOpenFile={(file) => {
                      onOpenFile(thread.thread_id, file);
                      onOpenChange(false);
                    }}
                  />
                ))
              )}
            </div>
          </ScrollArea>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ThreadWorkspaceGroup({
  thread,
  expanded,
  onToggle,
  onOpenFile,
}: {
  thread: ThreadWorkspaceRecord;
  expanded: boolean;
  onToggle: () => void;
  onOpenFile: (file: WorkspaceEntryRecord) => void;
}) {
  return (
    <section className="rounded-lg border bg-card">
      <button
        type="button"
        onClick={onToggle}
        className={cn(
          "flex min-h-11 w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-muted/50",
          expanded && "border-b",
        )}
      >
        <ChevronRight
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform",
            expanded && "rotate-90",
          )}
        />
        <Folder className="size-4 shrink-0 text-amber-500" />
        <h3 className="min-w-0 flex-1 truncate text-sm font-medium" title={thread.title}>
          {thread.title || "未命名会话"}
        </h3>
        <span className="shrink-0 text-xs text-muted-foreground">
          {thread.generated.length + thread.uploads.length}
        </span>
      </button>
      {expanded ? (
        <div className="grid gap-4 p-4 md:grid-cols-2">
          <FileBucket
            icon={Upload}
            title="用户上传"
            files={thread.uploads}
            onOpenFile={onOpenFile}
          />
          <FileBucket
            icon={WandSparkles}
            title="Agent 产物"
            files={thread.generated}
            onOpenFile={onOpenFile}
          />
        </div>
      ) : null}
    </section>
  );
}

function FileBucket({
  icon: Icon,
  title,
  files,
  onOpenFile,
}: {
  icon: LucideIcon;
  title: string;
  files: WorkspaceEntryRecord[];
  onOpenFile: (file: WorkspaceEntryRecord) => void;
}) {
  return (
    <div className="min-w-0">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <Icon className="size-3.5" />
        <span>{title}</span>
        <span className="ml-auto tabular-nums">{files.length}</span>
      </div>
      <div className="space-y-1">
        {files.length === 0 ? (
          <div className="rounded-md bg-muted/35 px-3 py-2 text-xs text-muted-foreground">
            空
          </div>
        ) : (
          files.map((file) => (
            <button
              key={file.path}
              type="button"
              onClick={() => onOpenFile(file)}
              className={cn(
                "flex min-h-9 w-full min-w-0 items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-muted",
                file.kind !== "file" && "opacity-60",
              )}
              disabled={file.kind !== "file"}
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
            </button>
          ))
        )}
      </div>
    </div>
  );
}
