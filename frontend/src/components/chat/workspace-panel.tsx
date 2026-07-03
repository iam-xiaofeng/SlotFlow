"use client";

import {
  type PointerEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import type { FitAddon as XTermFitAddon } from "@xterm/addon-fit";
import type { Terminal as XTermInstance } from "@xterm/xterm";
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
  Square,
  Terminal,
  Trash2,
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
  resolveTerminalWebSocketUrl,
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

type WorkspacePanelMode = "files" | "terminal";

export type WorkspacePanelRequest = {
  mode: WorkspacePanelMode;
  nonce: number;
};

type WorkspacePanelProps = {
  open: boolean;
  selectedPath?: string | null;
  width: number;
  refreshKey?: number;
  requestedMode?: WorkspacePanelRequest | null;
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
  requestedMode,
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
  const [panelMode, setPanelMode] = useState<WorkspacePanelMode>("files");
  const [terminalEnabled, setTerminalEnabled] = useState(false);
  const terminal = useTerminalSession(terminalEnabled);

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

  // 外部(如聊天区顶部的终端按钮)请求切换面板模式;nonce 保证重复点击也能重新生效。
  useEffect(() => {
    if (!requestedMode) {
      return;
    }
    if (requestedMode.mode === "terminal") {
      setTerminalEnabled(true);
    }
    setPanelMode(requestedMode.mode);
  }, [requestedMode]);

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

  const canSwitchView =
    panelMode === "files" && preview ? getArtifactPreviewType(preview).canSwitchView : false;
  const rawUrl = panelMode === "files" && preview ? resolveArtifactRawUrl(preview.path) : "";
  const downloadUrl =
    panelMode === "files" && preview ? resolveArtifactRawUrl(preview.path, { download: true }) : "";

  return (
    <aside
      aria-hidden={!open}
      className={cn(
        "relative h-full min-w-0 shrink-0 flex-col bg-background py-4 pl-0 pr-4",
        open ? "flex" : "hidden",
      )}
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
            {panelMode === "files" ? (
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
            ) : (
              <div className="flex min-w-0 flex-1 items-center gap-2 px-2 text-sm font-medium">
                <Terminal className="size-4 shrink-0 text-muted-foreground" />
                <span className="truncate">终端</span>
              </div>
            )}
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
            <div className="flex shrink-0 items-center rounded-lg border bg-background p-0.5">
              <Button
                type="button"
                variant={panelMode === "files" ? "secondary" : "ghost"}
                size="icon-xs"
                title="文件"
                className="size-7 rounded-md"
                onClick={() => setPanelMode("files")}
              >
                <FileText className="size-4" />
                <span className="sr-only">文件</span>
              </Button>
              <Button
                type="button"
                variant={panelMode === "terminal" ? "secondary" : "ghost"}
                size="icon-xs"
                title="终端"
                className="size-7 rounded-md"
                onClick={() => {
                  setTerminalEnabled(true);
                  setPanelMode("terminal");
                }}
              >
                <Terminal className="size-4" />
                <span className="sr-only">终端</span>
              </Button>
            </div>
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
          <div className={cn("min-h-0 flex-1", panelMode !== "terminal" && "hidden")}>
            <TerminalView active={open && panelMode === "terminal"} terminal={terminal} />
          </div>
          <div className={cn("min-h-0 flex-1", panelMode !== "files" && "hidden")}>
            <ArtifactStage
              preview={preview}
              previewError={previewError}
              isLoadingPreview={isLoadingPreview}
              viewMode={viewMode}
            />
          </div>
        </div>
      </div>
    </aside>
  );
}

type TerminalStatus = "idle" | "connecting" | "connected" | "closed" | "error";

type TerminalSession = {
  status: TerminalStatus;
  cwd: string;
  clearOutput: () => void;
  reconnect: () => void;
  sendInput: (data: string) => void;
  attachTerminal: (element: HTMLDivElement) => void;
  fitTerminal: () => void;
};

function useTerminalSession(enabled: boolean): TerminalSession {
  const socketRef = useRef<WebSocket | null>(null);
  const terminalRef = useRef<XTermInstance | null>(null);
  const fitAddonRef = useRef<XTermFitAddon | null>(null);
  const pendingOutputRef = useRef<string[]>([]);
  const [status, setStatus] = useState<TerminalStatus>("idle");
  const [cwd, setCwd] = useState<string>("");
  const [reconnectToken, setReconnectToken] = useState(0);

  const sendResize = useCallback(() => {
    const socket = socketRef.current;
    const terminal = terminalRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN || !terminal) {
      return;
    }
    socket.send(
      JSON.stringify({
        type: "resize",
        rows: terminal.rows,
        columns: terminal.cols,
      }),
    );
  }, []);

  const sendInput = useCallback((data: string) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    socket.send(JSON.stringify({ type: "input", data }));
  }, []);

  const writeOutput = useCallback((chunk: string) => {
    const terminal = terminalRef.current;
    if (!terminal) {
      pendingOutputRef.current.push(chunk);
      return;
    }
    terminal.write(chunk);
  }, []);

  const clearOutput = useCallback(() => {
    pendingOutputRef.current = [];
    terminalRef.current?.clear();
  }, []);

  const reconnect = useCallback(() => {
    socketRef.current?.close();
    socketRef.current = null;
    terminalRef.current?.reset();
    pendingOutputRef.current = [];
    setReconnectToken((current) => current + 1);
  }, []);

  const fitTerminal = useCallback(() => {
    const fitAddon = fitAddonRef.current;
    if (!fitAddon) {
      return;
    }
    try {
      fitAddon.fit();
      sendResize();
    } catch {
      // xterm cannot measure while hidden; the next visible fit will recover.
    }
  }, [sendResize]);

  const attachTerminal = useCallback(
    (element: HTMLDivElement) => {
      if (terminalRef.current) {
        fitTerminal();
        terminalRef.current.focus();
        return;
      }

      void Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]).then(
        ([xtermModule, fitModule]) => {
          if (terminalRef.current || !element.isConnected) {
            return;
          }

          const terminal = new xtermModule.Terminal({
            cursorBlink: true,
            fontFamily:
              "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, monospace",
            fontSize: 12,
            scrollback: 10000,
            theme: {
              background: "#0b1020",
              foreground: "#e2e8f0",
              cursor: "#38bdf8",
              selectionBackground: "#334155",
            },
          });
          const fitAddon = new fitModule.FitAddon();
          terminal.loadAddon(fitAddon);
          terminal.onData(sendInput);
          terminal.open(element);
          terminalRef.current = terminal;
          fitAddonRef.current = fitAddon;

          const pendingOutput = pendingOutputRef.current.join("");
          pendingOutputRef.current = [];
          if (pendingOutput) {
            terminal.write(pendingOutput);
          }
          fitTerminal();
          terminal.focus();
        },
      );
    },
    [fitTerminal, sendInput],
  );

  useEffect(() => {
    if (!enabled || socketRef.current) {
      return;
    }
    const socket = new WebSocket(resolveTerminalWebSocketUrl());
    socketRef.current = socket;
    setStatus("connecting");

    socket.addEventListener("open", () => setStatus("connected"));
    socket.addEventListener("message", (event) => {
      const payload = parseTerminalPayload(event.data);
      if (!payload) {
        return;
      }
      if (payload.type === "ready") {
        const nextCwd = stringValue(payload.cwd);
        setCwd(nextCwd);
        writeOutput(`Connected to ${stringValue(payload.shell) || "shell"} at ${nextCwd}\r\n`);
        return;
      }
      if (payload.type === "output" && typeof payload.data === "string") {
        writeOutput(payload.data);
        return;
      }
      if (payload.type === "exit") {
        setStatus("closed");
        writeOutput(
          `\r\n[terminal exited${typeof payload.exit_code === "number" ? `: ${payload.exit_code}` : ""}]\r\n`,
        );
      }
    });
    socket.addEventListener("error", () => setStatus("error"));
    socket.addEventListener("close", () => {
      setStatus((current) => (current === "error" ? "error" : "closed"));
    });
    socket.addEventListener("open", sendResize);

    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [enabled, reconnectToken, sendResize, writeOutput]);

  useEffect(() => {
    return () => {
      terminalRef.current?.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
    };
  }, []);

  return {
    status,
    cwd,
    clearOutput,
    reconnect,
    sendInput,
    attachTerminal,
    fitTerminal,
  };
}

function TerminalView({ active, terminal }: { active: boolean; terminal: TerminalSession }) {
  const terminalElementRef = useRef<HTMLDivElement | null>(null);
  const { status, cwd, clearOutput, reconnect, sendInput, attachTerminal, fitTerminal } = terminal;

  useEffect(() => {
    const element = terminalElementRef.current;
    if (!element) {
      return;
    }
    attachTerminal(element);
  }, [attachTerminal]);

  useEffect(() => {
    if (!active) {
      return;
    }
    fitTerminal();
    const handleResize = () => fitTerminal();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [active, fitTerminal]);

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-[#0b1020] text-slate-100">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-white/10 bg-black/20 px-3 text-xs text-slate-300">
        <span
          className={cn(
            "size-2 rounded-full",
            status === "connected" && "bg-emerald-400",
            status === "connecting" && "bg-amber-400",
            status === "closed" && "bg-slate-500",
            status === "error" && "bg-red-400",
            status === "idle" && "bg-slate-500",
          )}
        />
        <span className="font-medium">Host terminal</span>
        <span className="min-w-0 flex-1 truncate" title={cwd || undefined}>
          {cwd || (status === "idle" ? "未连接" : "连接中…")}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          title="发送 Ctrl+C"
          className="size-7 text-slate-300 hover:bg-white/10 hover:text-white"
          disabled={status !== "connected"}
          onClick={() => sendInput("\u0003")}
        >
          <Square className="size-3" />
          <span className="sr-only">发送 Ctrl+C</span>
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          title="清空终端"
          className="size-7 text-slate-300 hover:bg-white/10 hover:text-white"
          onClick={clearOutput}
        >
          <Trash2 className="size-3.5" />
          <span className="sr-only">清空终端</span>
        </Button>
      </div>
      <div
        ref={terminalElementRef}
        className="min-h-0 flex-1 overflow-hidden px-2 py-2"
        onClick={() => terminalElementRef.current?.focus()}
      />
      <div className="flex h-10 shrink-0 items-center justify-between gap-2 border-t border-white/10 bg-black/25 px-3 text-xs text-slate-400">
        <span>点击终端区域后直接输入；这是用户手动 Host terminal，不是 agent 工具。</span>
        {status === "closed" || status === "error" ? (
          <Button type="button" size="sm" variant="secondary" onClick={reconnect}>
            重连
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function parseTerminalPayload(data: unknown): Record<string, unknown> | null {
  if (typeof data !== "string") {
    return null;
  }
  try {
    const parsed = JSON.parse(data);
    return typeof parsed === "object" && parsed !== null
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
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
