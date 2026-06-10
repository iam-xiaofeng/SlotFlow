"use client";

import {
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ArrowUp,
  Boxes,
  Brain,
  FileText,
  Folder,
  Globe2,
  History,
  ImageIcon,
  LibraryBig,
  LoaderCircle,
  MessageSquarePlus,
  Mic,
  MoreHorizontal,
  Paperclip,
  Plug,
  Plus,
  Search,
  Sparkles,
  Square,
  Telescope,
  Wrench,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { toast } from "sonner";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarInput,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSkeleton,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { Textarea } from "@/components/ui/textarea";
import { type ChatUiMessage, useChatStream } from "@/hooks/use-chat-stream";
import {
  type ChatMode,
  type ThreadRecord,
  type UploadedFileRecord,
  listThreads,
  uploadFile,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

const defaultModelName = "deepseek-v4-flash";
const defaultMode: ChatMode = "pro";

export function ChatApp() {
  const [input, setInput] = useState("");
  const [threads, setThreads] = useState<ThreadRecord[]>([]);
  const [threadQuery, setThreadQuery] = useState("");
  const [threadListError, setThreadListError] = useState<string | null>(null);
  const [isLoadingThreads, setIsLoadingThreads] = useState(true);
  const [attachments, setAttachments] = useState<UploadedFileRecord[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [isComposerExpanded, setIsComposerExpanded] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const {
    thread,
    messages,
    isStreaming,
    error,
    sendMessage,
    cancelStream,
    loadThread,
    resetThread,
    clearError,
  } = useChatStream({
    defaultThreadTitle: "New chat",
    defaultModelName,
    defaultMode,
    defaultAgentName: "default",
    defaultMetadata: {
      source: "chat-ui",
    },
    maxEventLogItems: 10,
  });

  const refreshThreads = useCallback(async () => {
    setThreadListError(null);
    try {
      const nextThreads = await listThreads();
      setThreads(nextThreads);
      return nextThreads;
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "load threads failed";
      setThreadListError(message);
      return [];
    }
  }, []);

  useEffect(() => {
    let active = true;

    async function bootstrap() {
      setIsLoadingThreads(true);
      const nextThreads = await refreshThreads();
      if (!active) {
        return;
      }
      if (nextThreads[0]) {
        await loadThread(nextThreads[0]);
      }
      if (active) {
        setIsLoadingThreads(false);
      }
    }

    void bootstrap();

    return () => {
      active = false;
    };
  }, [loadThread, refreshThreads]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const syncComposerSize = useCallback((nextValue: string) => {
    const textarea = textareaRef.current;
    if (!textarea) {
      setIsComposerExpanded(nextValue.includes("\n") || nextValue.length > 72);
      return;
    }

    textarea.style.height = "auto";
    const nextHeight = Math.min(textarea.scrollHeight, 176);
    textarea.style.height = `${Math.max(28, nextHeight)}px`;
    setIsComposerExpanded(textarea.scrollHeight > 38 || nextValue.includes("\n"));
  }, []);

  useEffect(() => {
    syncComposerSize(input);
  }, [input, syncComposerSize]);

  function handleInputChange(event: ChangeEvent<HTMLTextAreaElement>) {
    const nextValue = event.target.value;
    setInput(nextValue);
    syncComposerSize(nextValue);
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing) {
      return;
    }

    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitMessage();
    }
  }

  const filteredThreads = useMemo(() => {
    const query = threadQuery.trim().toLowerCase();
    if (!query) {
      return threads;
    }
    return threads.filter((item) => item.title.toLowerCase().includes(query));
  }, [threadQuery, threads]);

  async function handleSelectThread(nextThread: ThreadRecord) {
    if (isStreaming || nextThread.id === thread?.id) {
      return;
    }

    const loaded = await loadThread(nextThread);
    if (loaded) {
      setAttachments([]);
      clearError();
    }
  }

  function handleNewThread() {
    if (resetThread()) {
      setInput("");
      setAttachments([]);
    }
  }

  async function submitMessage() {
    const text = input.trim();
    if (!text || isStreaming || isUploading) {
      return;
    }

    const currentAttachments = attachments;
    setInput("");
    setAttachments([]);
    const result = await sendMessage(text, {
      mode: defaultMode,
      model_name: defaultModelName,
      agent_name: "default",
      files: currentAttachments.map((file) => file.id),
      metadata: {
        source: "chat-ui",
        uploaded_file_count: currentAttachments.length,
        uploaded_files: currentAttachments.map((file) => ({
          id: file.id,
          filename: file.filename,
          content_type: file.content_type,
          size_bytes: file.size_bytes,
        })),
      },
      threadTitle: makeThreadTitle(text),
    });

    if (result.accepted) {
      await refreshThreads();
    } else {
      setInput(text);
      setAttachments(currentAttachments);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await submitMessage();
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFiles = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (selectedFiles.length === 0) {
      return;
    }

    setIsUploading(true);
    const uploadedFiles: UploadedFileRecord[] = [];

    for (const file of selectedFiles) {
      try {
        uploadedFiles.push(await uploadFile(file));
      } catch (caught) {
        const message = caught instanceof Error ? caught.message : "upload failed";
        toast.error(message);
      }
    }

    if (uploadedFiles.length > 0) {
      setAttachments((current) => [...current, ...uploadedFiles]);
      toast.success(
        uploadedFiles.length === 1
          ? `${uploadedFiles[0].filename} uploaded`
          : `${uploadedFiles.length} files uploaded`,
      );
    }
    setIsUploading(false);
  }

  const composer = (
    <form onSubmit={handleSubmit} className="w-full">
      <div className="mx-auto w-full max-w-3xl">
        {error ? (
          <div className="mb-3 flex items-start justify-between gap-3 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-[0.95rem] text-destructive">
            <span className="min-w-0 break-words">{error}</span>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              title="Dismiss error"
              onClick={clearError}
            >
              <X className="size-4" />
              <span className="sr-only">Dismiss error</span>
            </Button>
          </div>
        ) : null}

        {attachments.length > 0 ? (
          <div className="mb-3 flex flex-wrap gap-2">
            {attachments.map((file) => (
              <Badge
                key={file.id}
                variant="outline"
                className="h-8 max-w-full gap-1.5 rounded-md pr-1"
              >
                <FileText className="size-4 shrink-0" />
                <span className="max-w-52 truncate">{file.filename}</span>
                <span className="text-muted-foreground">
                  {formatFileSize(file.size_bytes)}
                </span>
                <button
                  type="button"
                  className="grid size-5 place-items-center rounded-sm hover:bg-muted"
                  title="Remove file"
                  onClick={() =>
                    setAttachments((current) =>
                      current.filter((item) => item.id !== file.id),
                    )
                  }
                >
                  <X className="size-4" />
                  <span className="sr-only">Remove file</span>
                </button>
              </Badge>
            ))}
          </div>
        ) : null}

        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(event) => void handleFileChange(event)}
        />

        <div
          className={cn(
            "rounded-3xl border border-input bg-background shadow-sm",
            isComposerExpanded
              ? "px-4 py-3"
              : "flex min-h-14 items-center gap-2 px-3 py-2",
          )}
        >
          {isComposerExpanded ? (
            <>
              <Textarea
                ref={textareaRef}
                value={input}
                onChange={handleInputChange}
                disabled={isStreaming}
                rows={1}
                placeholder="有问题，尽管问"
                onKeyDown={handleComposerKeyDown}
                className="max-h-44 min-h-8 resize-none overflow-y-auto border-0 bg-transparent px-0 py-0 text-lg leading-7 shadow-none focus-visible:ring-0"
              />
              <div className="mt-3 flex items-center justify-between gap-2">
                <ComposerTools
                  disabled={isStreaming || isUploading}
                  isUploading={isUploading}
                  onAttachFiles={() => fileInputRef.current?.click()}
                />
                <ComposerActions
                  canSend={Boolean(input.trim()) && !isUploading}
                  isStreaming={isStreaming}
                  onCancel={cancelStream}
                  onSend={() => void submitMessage()}
                />
              </div>
            </>
          ) : (
            <>
              <ComposerTools
                disabled={isStreaming || isUploading}
                isUploading={isUploading}
                onAttachFiles={() => fileInputRef.current?.click()}
                compact
              />
              <Textarea
                ref={textareaRef}
                value={input}
                onChange={handleInputChange}
                disabled={isStreaming}
                rows={1}
                placeholder="有问题，尽管问"
                onKeyDown={handleComposerKeyDown}
                className="max-h-44 min-h-8 flex-1 resize-none overflow-y-auto border-0 bg-transparent px-0 py-0 text-lg leading-7 shadow-none focus-visible:ring-0"
              />
              <ComposerActions
                canSend={Boolean(input.trim()) && !isUploading}
                isStreaming={isStreaming}
                onCancel={cancelStream}
                onSend={() => void submitMessage()}
              />
            </>
          )}
        </div>
      </div>
    </form>
  );

  return (
    <SidebarProvider className="h-dvh min-h-0 overflow-hidden bg-background text-foreground">
      <Sidebar collapsible="icon" className="border-r-0">
        <ThreadSidebar
          activeThreadId={thread?.id ?? null}
          disabled={isStreaming}
          filteredThreads={filteredThreads}
          isLoading={isLoadingThreads}
          query={threadQuery}
          threadListError={threadListError}
          totalThreads={threads.length}
          onNewThread={handleNewThread}
          onQueryChange={setThreadQuery}
          onSelectThread={(nextThread) => void handleSelectThread(nextThread)}
        />
      </Sidebar>

      <SidebarInset className="h-dvh min-h-0 overflow-hidden">
        <header className="flex h-14 shrink-0 items-center justify-end bg-background px-3 sm:px-4">
          <UserMenu />
        </header>

        {messages.length === 0 ? (
          <section className="flex min-h-0 flex-1 flex-col items-center justify-center px-3 pb-16 sm:px-6">
            <div className="w-full max-w-3xl -translate-y-10">
              <EmptyState />
              {composer}
            </div>
          </section>
        ) : (
          <>
            <ScrollArea className="min-h-0 flex-1">
              <div className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-4 py-6 sm:px-6 lg:px-8">
                <div className="flex flex-col gap-5">
                  {messages.map((message) => (
                    <MessageBubble key={message.id} message={message} />
                  ))}
                </div>
                <div ref={messagesEndRef} />
              </div>
            </ScrollArea>

            <div className="shrink-0 bg-background px-3 pb-5 pt-3 sm:px-6">
              {composer}
            </div>
          </>
        )}
        </SidebarInset>
    </SidebarProvider>
  );
}

type ThreadSidebarProps = {
  activeThreadId: string | null;
  disabled: boolean;
  filteredThreads: ThreadRecord[];
  isLoading: boolean;
  query: string;
  threadListError: string | null;
  totalThreads: number;
  onNewThread: () => void;
  onQueryChange: (query: string) => void;
  onSelectThread: (thread: ThreadRecord) => void;
};

function ThreadSidebar({
  activeThreadId,
  disabled,
  filteredThreads,
  isLoading,
  query,
  threadListError,
  totalThreads,
  onNewThread,
  onQueryChange,
  onSelectThread,
}: ThreadSidebarProps) {
  return (
    <>
      <SidebarHeader>
        <div className="flex items-center justify-between gap-2 group-data-[collapsible=icon]:flex-col">
          <SidebarMenu className="min-w-0 flex-1 group-data-[collapsible=icon]:items-center">
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" tooltip="SlotFlow">
                <Sparkles className="size-5" />
                <span className="min-w-0 group-data-[collapsible=icon]:hidden">
                  <span className="block truncate text-base font-semibold">SlotFlow</span>
                  <span className="block truncate text-sm text-muted-foreground">
                    {totalThreads} 个聊天
                  </span>
                </span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
          <SidebarTrigger className="rounded-lg group-data-[collapsible=icon]:order-2" />
        </div>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup className="pb-1">
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  type="button"
                  tooltip="新聊天"
                  onClick={onNewThread}
                  disabled={disabled}
                  isActive={!activeThreadId}
                >
                  <MessageSquarePlus className="size-5" />
                  <span>新聊天</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <ContextPickerMenu kind="skills" />
              </SidebarMenuItem>
              <SidebarMenuItem>
                <ContextPickerMenu kind="mcp" />
              </SidebarMenuItem>
              <SidebarMenuItem>
                <ContextPickerMenu kind="artifacts" />
              </SidebarMenuItem>
              <SidebarMenuItem>
                <MoreToolsMenu />
              </SidebarMenuItem>
              <SidebarMenuItem className="group-data-[collapsible=icon]:hidden">
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3 top-1/2 size-5 -translate-y-1/2 text-muted-foreground" />
                  <SidebarInput
                    value={query}
                    onChange={(event) => onQueryChange(event.target.value)}
                    placeholder="搜索聊天"
                    className="h-11 rounded-xl pl-10"
                  />
                </div>
              </SidebarMenuItem>
              <SidebarMenuItem className="hidden group-data-[collapsible=icon]:block">
                <SidebarMenuButton type="button" tooltip="搜索聊天">
                  <Search className="size-5" />
                  <span>搜索聊天</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <ThreadHistory
          activeThreadId={activeThreadId}
          disabled={disabled}
          filteredThreads={filteredThreads}
          isLoading={isLoading}
          query={query}
          threadListError={threadListError}
          onSelectThread={onSelectThread}
        />
      </SidebarContent>
    </>
  );
}

function UserMenu() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button type="button" variant="ghost" size="icon" className="rounded-full" />
        }
      >
        <Avatar className="size-8">
          <AvatarFallback>U</AvatarFallback>
        </Avatar>
        <span className="sr-only">打开用户菜单</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={8} className="w-44">
        <DropdownMenuItem disabled>账号占位</DropdownMenuItem>
        <DropdownMenuItem disabled>偏好设置</DropdownMenuItem>
        <DropdownMenuItem disabled>退出登录</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

type ContextPickerKind = "skills" | "mcp" | "artifacts";

function ContextPickerMenu({ kind }: { kind: ContextPickerKind }) {
  const config = {
    skills: {
      icon: Wrench,
      label: "Skills",
      empty: "暂无已添加 Skill",
      actions: ["从路径添加", "拖拽添加"],
    },
    mcp: {
      icon: Plug,
      label: "MCP",
      empty: "暂无 MCP 连接",
      actions: ["通过 HTTP 添加", "管理连接"],
    },
    artifacts: {
      icon: FileText,
      label: "产物",
      empty: "暂无对话产物",
      actions: ["打开产物面板", "从本地添加"],
    },
  } satisfies Record<
    ContextPickerKind,
    {
      icon: typeof Wrench;
      label: string;
      empty: string;
      actions: string[];
    }
  >;
  const item = config[kind];
  const Icon = item.icon;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<SidebarMenuButton type="button" tooltip={item.label} />}
      >
        <Icon className="size-5" />
        <span>{item.label}</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="right" align="start" sideOffset={8} className="w-56">
        <DropdownMenuItem disabled className="gap-3">
          <Icon className="size-5" />
          {item.empty}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        {item.actions.map((action) => (
          <DropdownMenuItem key={action} disabled className="gap-3">
            <Plus className="size-5" />
            {action}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function MoreToolsMenu() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<SidebarMenuButton type="button" />}
      >
        <MoreHorizontal className="size-5" />
        <span>更多</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="right" align="start" sideOffset={8} className="w-44">
        <DropdownMenuItem disabled className="gap-3">
          <LibraryBig className="size-5" />
          库
        </DropdownMenuItem>
        <DropdownMenuItem disabled className="gap-3">
          <Folder className="size-5" />
          项目
        </DropdownMenuItem>
        <DropdownMenuItem disabled className="gap-3">
          <Boxes className="size-5" />
          应用
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

type ThreadHistoryProps = {
  activeThreadId: string | null;
  disabled: boolean;
  filteredThreads: ThreadRecord[];
  isLoading: boolean;
  query: string;
  threadListError: string | null;
  onSelectThread: (thread: ThreadRecord) => void;
};

function ThreadHistory({
  activeThreadId,
  disabled,
  filteredThreads,
  isLoading,
  query,
  threadListError,
  onSelectThread,
}: ThreadHistoryProps) {
  return (
    <SidebarGroup className="p-2 pt-1 group-data-[collapsible=icon]:hidden">
      <SidebarGroupLabel className="px-2">刚刚</SidebarGroupLabel>
      <SidebarGroupContent>
        <ScrollArea className="max-h-72">
          <SidebarMenu className="pr-1">
            {isLoading ? (
              <ThreadSkeletons />
            ) : threadListError ? (
              <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-[0.95rem] text-destructive">
                {threadListError}
              </div>
            ) : filteredThreads.length === 0 ? (
              <div className="px-2 py-4 text-[0.95rem] text-muted-foreground">
                {query.trim() ? "没有匹配的聊天" : "暂无刚刚的聊天"}
              </div>
            ) : (
              filteredThreads.map((item) => (
                <SidebarMenuItem key={item.id}>
                  <SidebarMenuButton
                    type="button"
                    tooltip={item.title}
                    isActive={item.id === activeThreadId}
                    disabled={disabled}
                    onClick={() => onSelectThread(item)}
                    className="h-9"
                  >
                    <History className="size-5" />
                    <span className="truncate">{item.title}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))
            )}
          </SidebarMenu>
        </ScrollArea>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

function ThreadSkeletons() {
  return (
    <>
      {Array.from({ length: 5 }).map((_, index) => (
        <SidebarMenuItem key={index}>
          <SidebarMenuSkeleton showIcon />
        </SidebarMenuItem>
      ))}
    </>
  );
}

type ComposerToolsProps = {
  compact?: boolean;
  disabled: boolean;
  isUploading: boolean;
  onAttachFiles: () => void;
};

function ComposerTools({
  compact = false,
  disabled,
  isUploading,
  onAttachFiles,
}: ComposerToolsProps) {
  return (
    <div className={cn("flex items-center gap-1", compact && "shrink-0")}>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              type="button"
              variant="ghost"
              size="icon"
              disabled={disabled}
              className="size-10 rounded-full"
            />
          }
        >
          {isUploading ? (
            <LoaderCircle className="size-6 animate-spin" />
          ) : (
            <Plus className="size-6" />
          )}
          <span className="sr-only">打开添加菜单</span>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          side="top"
          align="start"
          sideOffset={10}
          className="w-72 rounded-2xl p-2"
        >
          <DropdownMenuItem onClick={onAttachFiles} className="gap-3 px-3 py-2.5">
            <Paperclip className="size-5" />
            添加照片和文件
          </DropdownMenuItem>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger className="gap-3 px-3 py-2.5">
              <FileText className="size-5" />
              近期文件
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="w-48">
              <DropdownMenuItem disabled>暂无近期文件</DropdownMenuItem>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled className="gap-3 px-3 py-2.5">
            <ImageIcon className="size-5" />
            创建图片
          </DropdownMenuItem>
          <DropdownMenuItem disabled className="gap-3 px-3 py-2.5">
            <Brain className="size-5" />
            思考一下
          </DropdownMenuItem>
          <DropdownMenuItem disabled className="gap-3 px-3 py-2.5">
            <Telescope className="size-5" />
            深度研究
          </DropdownMenuItem>
          <DropdownMenuItem disabled className="gap-3 px-3 py-2.5">
            <Globe2 className="size-5" />
            网页搜索
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled className="gap-3 px-3 py-2.5">
            <MoreHorizontal className="size-5" />
            更多
          </DropdownMenuItem>
          <DropdownMenuItem disabled className="gap-3 px-3 py-2.5">
            <Folder className="size-5" />
            项目
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

type ComposerActionsProps = {
  canSend: boolean;
  isStreaming: boolean;
  onCancel: () => void;
  onSend: () => void;
};

function ComposerActions({
  canSend,
  isStreaming,
  onCancel,
  onSend,
}: ComposerActionsProps) {
  return (
    <div className="flex shrink-0 items-center gap-1">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        title="语音输入"
        className="size-10 rounded-full"
      >
        <Mic className="size-6" />
        <span className="sr-only">语音输入</span>
      </Button>

      {isStreaming ? (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          title="停止"
          onClick={onCancel}
          className="size-11 rounded-full bg-muted text-foreground hover:bg-muted/80"
        >
          <Square className="size-4 fill-current" />
          <span className="sr-only">停止</span>
        </Button>
      ) : (
        <Button
          type="button"
          size="icon"
          title="发送"
          disabled={!canSend}
          onClick={onSend}
          className="size-11 rounded-full bg-foreground text-background hover:bg-foreground/90 disabled:bg-muted disabled:text-muted-foreground"
        >
          <ArrowUp className="size-6" />
          <span className="sr-only">发送</span>
        </Button>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="grid flex-1 place-items-center px-4 py-16 text-center">
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
        有什么可以帮忙的？
      </h1>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatUiMessage }) {
  const isUser = message.role === "user";
  const files = getMessageFiles(message);
  const content = message.content || (message.status === "streaming" ? "..." : "");

  return (
    <article className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "flex min-w-0 flex-col gap-2 text-base leading-7",
          isUser
            ? "max-w-[82%] items-end text-foreground"
            : "w-full max-w-3xl px-1 text-foreground",
        )}
      >
        {files.length > 0 ? <MessageAttachments files={files} /> : null}
        <div
          className={cn(
            "min-w-0 break-words",
            isUser
              ? "rounded-2xl bg-muted px-4 py-2.5"
              : "w-full",
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{content}</p>
          ) : (
            <MarkdownContent content={content} />
          )}
        </div>
      </div>
    </article>
  );
}

function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="slotflow-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function MessageAttachments({ files }: { files: MessageFile[] }) {
  return (
    <div className="flex max-w-full flex-wrap justify-end gap-2">
      {files.map((file) => (
        <div
          key={file.id}
          className="flex max-w-72 items-center gap-3 rounded-2xl border border-border bg-card px-3 py-2 text-left shadow-sm"
        >
          <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-muted">
            <FileText className="size-5" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">{file.filename}</div>
            {typeof file.size_bytes === "number" ? (
              <div className="text-xs text-muted-foreground">
                {formatFileSize(file.size_bytes)}
              </div>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}

function makeThreadTitle(message: string) {
  const compact = message.replace(/\s+/g, " ").trim();
  if (compact.length <= 48) {
    return compact || "New chat";
  }
  return `${compact.slice(0, 45)}...`;
}

function formatFileSize(sizeBytes: number) {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

type MessageFile = {
  id: string;
  filename: string;
  size_bytes?: number;
};

function getMessageFiles(message: ChatUiMessage): MessageFile[] {
  const uploadedFiles = message.metadata?.uploaded_files;
  if (!Array.isArray(uploadedFiles)) {
    return [];
  }

  return uploadedFiles.flatMap((item) => {
    if (
      typeof item === "object" &&
      item !== null &&
      "id" in item &&
      "filename" in item &&
      typeof item.id === "string" &&
      typeof item.filename === "string"
    ) {
      return [
        {
          id: item.id,
          filename: item.filename,
          size_bytes:
            "size_bytes" in item && typeof item.size_bytes === "number"
              ? item.size_bytes
              : undefined,
        },
      ];
    }
    return [];
  });
}
