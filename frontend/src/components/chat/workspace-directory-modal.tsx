"use client";

import { useCallback, useState } from "react";
import {
  ChevronRight,
  Download,
  ExternalLink,
  FileText,
  Folder,
  FolderTree,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { SidebarMenuButton } from "@/components/ui/sidebar";
import {
  type WorkspaceEntryRecord,
  listArtifacts,
  resolveArtifactRawUrl,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

import { formatFileSize } from "./chat-format";

const ROOT_PATH = "artifacts";

/** Basename for a workspace path, e.g. "artifacts/threadA/notes.md" -> "notes.md". */
function entryName(path: string): string {
  const segments = path.split("/").filter(Boolean);
  return segments[segments.length - 1] ?? path;
}

/** A modal that browses the artifacts workspace tree, drilling into folders on demand
 *  (the list API returns immediate children only). Files can be opened or downloaded. */
export function WorkspaceDirectoryModal({ disabled = false }: { disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [childrenByPath, setChildrenByPath] = useState<Record<string, WorkspaceEntryRecord[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([ROOT_PATH]));
  const [loadingPaths, setLoadingPaths] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);

  const loadDirectory = useCallback(async (path: string) => {
    setLoadingPaths((current) => new Set(current).add(path));
    setError(null);
    try {
      const entries = await listArtifacts({ path });
      setChildrenByPath((current) => ({ ...current, [path]: entries }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载目录失败");
    } finally {
      setLoadingPaths((current) => {
        const next = new Set(current);
        next.delete(path);
        return next;
      });
    }
  }, []);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      setOpen(next);
      if (next && !childrenByPath[ROOT_PATH]) {
        void loadDirectory(ROOT_PATH);
      }
    },
    [childrenByPath, loadDirectory],
  );

  const toggleDirectory = useCallback(
    (path: string) => {
      setExpanded((current) => {
        const next = new Set(current);
        if (next.has(path)) {
          next.delete(path);
        } else {
          next.add(path);
          if (!childrenByPath[path]) {
            void loadDirectory(path);
          }
        }
        return next;
      });
    },
    [childrenByPath, loadDirectory],
  );

  const refresh = useCallback(() => {
    setChildrenByPath({});
    setExpanded(new Set([ROOT_PATH]));
    void loadDirectory(ROOT_PATH);
  }, [loadDirectory]);

  function renderLevel(path: string, depth: number): React.ReactNode {
    const entries = childrenByPath[path];
    if (!entries) {
      if (loadingPaths.has(path)) {
        return <TreeMessage depth={depth} icon={<LoaderCircle className="size-3.5 animate-spin" />} text="加载中…" />;
      }
      return null;
    }
    if (entries.length === 0) {
      return <TreeMessage depth={depth} text="（空目录）" />;
    }
    return entries.map((entry) =>
      entry.kind === "directory" ? (
        <div key={entry.path}>
          <button
            type="button"
            onClick={() => toggleDirectory(entry.path)}
            style={{ paddingLeft: depth * 14 + 8 }}
            className="flex min-h-8 w-full items-center gap-1.5 rounded-md py-1 pr-2 text-left text-sm text-foreground transition-colors hover:bg-muted"
          >
            <ChevronRight
              className={cn(
                "size-3.5 shrink-0 text-muted-foreground transition-transform",
                expanded.has(entry.path) && "rotate-90",
              )}
            />
            <Folder className="size-4 shrink-0 text-blue-600" />
            <span className="truncate">{entryName(entry.path)}</span>
          </button>
          {expanded.has(entry.path) ? renderLevel(entry.path, depth + 1) : null}
        </div>
      ) : (
        <div
          key={entry.path}
          style={{ paddingLeft: depth * 14 + 8 }}
          className="group flex min-h-8 items-center gap-1.5 rounded-md py-1 pr-2 text-sm"
        >
          <FileText className="ml-[1.125rem] size-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate" title={entry.path}>
            {entryName(entry.path)}
          </span>
          {typeof entry.size_bytes === "number" ? (
            <span className="shrink-0 text-xs text-muted-foreground">
              {formatFileSize(entry.size_bytes)}
            </span>
          ) : null}
          <a
            href={resolveArtifactRawUrl(entry.path)}
            target="_blank"
            rel="noreferrer"
            title="在新标签打开"
            className="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
          >
            <ExternalLink className="size-3.5" />
          </a>
          <a
            href={resolveArtifactRawUrl(entry.path, { download: true })}
            title="下载"
            className="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
          >
            <Download className="size-3.5" />
          </a>
        </div>
      ),
    );
  }

  const rootEntries = childrenByPath[ROOT_PATH];
  const isRootLoading = loadingPaths.has(ROOT_PATH) && !rootEntries;
  const isRootEmpty = rootEntries?.length === 0;

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetTrigger
        render={
          <SidebarMenuButton
            type="button"
            disabled={disabled}
            tooltip="工作区目录"
            className="h-9 rounded-lg px-2.5 text-[0.95rem] font-normal text-muted-foreground transition-all duration-150 hover:-translate-y-0.5 hover:bg-muted hover:text-foreground data-open:bg-muted data-open:text-foreground"
          />
        }
      >
        <FolderTree className="size-4" />
        <span>工作区目录</span>
      </SheetTrigger>
      <SheetContent side="left" className="w-[22rem] gap-0 p-0 sm:max-w-md">
        <SheetHeader className="flex-row items-center justify-between gap-2 border-b">
          <div className="min-w-0">
            <SheetTitle>工作区目录</SheetTitle>
            <SheetDescription className="truncate">
              浏览 artifacts 产物目录
            </SheetDescription>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            title="刷新"
            onClick={refresh}
            className="mr-8 shrink-0"
          >
            <RefreshCw className="size-4" />
            <span className="sr-only">刷新</span>
          </Button>
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1">
          <div className="p-2">
            {error ? (
              <p className="px-2 py-3 text-sm text-destructive">{error}</p>
            ) : isRootLoading ? (
              <p className="flex items-center gap-2 px-2 py-3 text-sm text-muted-foreground">
                <LoaderCircle className="size-4 animate-spin" />
                正在加载目录…
              </p>
            ) : isRootEmpty ? (
              <p className="px-2 py-3 text-sm text-muted-foreground">
                工作区暂无产物。模型通过 artifact_write 生成的文件会出现在这里。
              </p>
            ) : (
              renderLevel(ROOT_PATH, 0)
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}

function TreeMessage({
  depth,
  icon,
  text,
}: {
  depth: number;
  icon?: React.ReactNode;
  text: string;
}) {
  return (
    <p
      style={{ paddingLeft: depth * 14 + 26 }}
      className="flex items-center gap-1.5 py-1 text-xs text-muted-foreground"
    >
      {icon}
      {text}
    </p>
  );
}
