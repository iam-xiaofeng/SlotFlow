"use client";

import { useMemo, useState } from "react";
import {
  Blocks,
  Brain,
  Folder,
  Loader2,
  MessageSquarePlus,
  MoreHorizontal,
  Plug,
  Trash2,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSkeleton,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import {
  type McpServerRecord,
  type MemoryKind,
  type MemoryRecord,
  type SkillInstallRequest,
  type SkillRecord,
  type ThreadRecord,
} from "@/lib/chat-stream";
import { type ThreadRunStatus } from "@/hooks/use-chat-stream";

import { DirectoryModal, type DirectoryTab } from "./directory-modal";
import { SearchMenu } from "./chat-sidebar-search";
import { SlotFlowLogo } from "./slotflow-logo";

type ThreadSidebarProps = {
  activeThreadId: string | null;
  disabled: boolean;
  runStates: Record<string, ThreadRunStatus>;
  filteredThreads: ThreadRecord[];
  isLoading: boolean;
  memories: MemoryRecord[];
  mcpServers: McpServerRecord[];
  query: string;
  skills: SkillRecord[];
  threadListError: string | null;
  onAddHttpMcpServer: () => void;
  onAddMemory: (content: string, kind: MemoryKind) => void;
  onDeleteMcpServer: (server: McpServerRecord) => void;
  onDeleteMemory: (memory: MemoryRecord) => void;
  onDeleteSkill: (skill: SkillRecord) => void;
  onDeleteThread: (thread: ThreadRecord) => void;
  onEditMemory: (memory: MemoryRecord, content: string, kind: MemoryKind) => void;
  onGroupSkills: (input: {
    name: string;
    description: string;
    content: string;
    members: string[];
  }) => Promise<void> | void;
  onInstallSkill: (request?: SkillInstallRequest) => Promise<void> | void;
  onNewThread: () => void;
  onOpenWorkspaceDirectory: () => void;
  onPinMcpServer: (server: McpServerRecord, pinned: boolean) => void;
  onPinSkill: (skill: SkillRecord, pinned: boolean) => void;
  onQueryChange: (query: string) => void;
  onReorderMcpServers: (names: string[]) => void;
  onReorderSkills: (names: string[]) => void;
  onSelectThread: (thread: ThreadRecord) => void;
  onToggleMcpServer: (server: McpServerRecord, enabled: boolean) => void;
  onToggleSkill: (skill: SkillRecord, enabled: boolean) => void;
  onUploadSkill: () => void;
};

export function ThreadSidebar({
  activeThreadId,
  disabled,
  runStates,
  filteredThreads,
  isLoading,
  memories,
  mcpServers,
  query,
  skills,
  threadListError,
  onAddHttpMcpServer,
  onAddMemory,
  onDeleteMcpServer,
  onDeleteMemory,
  onDeleteSkill,
  onDeleteThread,
  onEditMemory,
  onGroupSkills,
  onInstallSkill,
  onNewThread,
  onOpenWorkspaceDirectory,
  onPinMcpServer,
  onPinSkill,
  onQueryChange,
  onReorderMcpServers,
  onReorderSkills,
  onSelectThread,
  onToggleMcpServer,
  onToggleSkill,
  onUploadSkill,
}: ThreadSidebarProps) {
  const { state } = useSidebar();
  const [directoryOpen, setDirectoryOpen] = useState(false);
  const [directoryTab, setDirectoryTab] = useState<DirectoryTab>("skills");

  function openDirectory(tab: DirectoryTab) {
    setDirectoryTab(tab);
    setDirectoryOpen(true);
  }

  if (state === "collapsed") {
    return (
      <CollapsedSidebarControls
        disabled={disabled}
        query={query}
        onNewThread={onNewThread}
        onQueryChange={onQueryChange}
        onSelectThread={onSelectThread}
      />
    );
  }

  return (
    <>
      <DirectoryModal
        open={directoryOpen}
        tab={directoryTab}
        onOpenChange={setDirectoryOpen}
        onTabChange={setDirectoryTab}
        skills={skills}
        mcpServers={mcpServers}
        memories={memories}
        onInstallSkill={onInstallSkill}
        onGroupSkills={onGroupSkills}
        onUploadSkill={onUploadSkill}
        onToggleSkill={onToggleSkill}
        onPinSkill={onPinSkill}
        onReorderSkills={onReorderSkills}
        onDeleteSkill={onDeleteSkill}
        onAddHttpMcpServer={onAddHttpMcpServer}
        onToggleMcpServer={onToggleMcpServer}
        onPinMcpServer={onPinMcpServer}
        onReorderMcpServers={onReorderMcpServers}
        onDeleteMcpServer={onDeleteMcpServer}
        onAddMemory={onAddMemory}
        onEditMemory={onEditMemory}
        onDeleteMemory={onDeleteMemory}
      />
      <SidebarHeader className="px-2 py-0">
        <div className="group/slotflow-sidebar-header flex h-12 items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2 pl-2 group-data-[collapsible=icon]:pl-0">
            <SlotFlowLogo className="size-6 shrink-0 rounded-md" />
            <span className="min-w-0 truncate font-serif text-[1.05rem] font-semibold leading-none text-foreground group-data-[collapsible=icon]:hidden">
              SlotFlow
            </span>
          </div>
          <div className="flex items-center gap-1 group-data-[collapsible=icon]:w-full group-data-[collapsible=icon]:justify-center">
            <SearchMenu
              query={query}
              onQueryChange={onQueryChange}
              onSelectThread={onSelectThread}
            />
            <SidebarTrigger className="size-8 rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground" />
          </div>
        </div>
      </SidebarHeader>

      <SidebarContent className="px-2">
        <SidebarGroup className="pb-1 pt-2">
          <SidebarGroupContent>
            <Button
              type="button"
              disabled={disabled}
              onClick={onNewThread}
              className="h-10 w-full justify-start gap-2.5 rounded-xl bg-accent px-3 text-[0.95rem] font-medium text-accent-foreground shadow-none transition-colors hover:bg-accent/75"
            >
              <MessageSquarePlus className="size-4" />
              新聊天
            </Button>
          </SidebarGroupContent>
        </SidebarGroup>

        <ThreadHistory
          activeThreadId={activeThreadId}
          disabled={disabled}
          runStates={runStates}
          filteredThreads={filteredThreads}
          isLoading={isLoading}
          query={query}
          threadListError={threadListError}
          onSelectThread={onSelectThread}
          onDeleteThread={onDeleteThread}
        />
      </SidebarContent>

      <SidebarFooter className="mt-auto gap-2 px-2 pb-3 pt-2">
        <div className="flex items-center justify-between gap-1 border-t border-sidebar-border pt-2">
          {(
            [
              { label: "Skills", icon: Blocks, onClick: () => openDirectory("skills") },
              { label: "MCP", icon: Plug, onClick: () => openDirectory("mcp") },
              { label: "记忆", icon: Brain, onClick: () => openDirectory("memory") },
              { label: "工作区", icon: Folder, onClick: onOpenWorkspaceDirectory },
            ] as const
          ).map(({ label, icon: Icon, onClick }) => (
            <Button
              key={label}
              type="button"
              variant="ghost"
              size="icon"
              title={label}
              disabled={disabled}
              onClick={onClick}
              className="size-9 flex-1 rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <Icon className="size-4" />
              <span className="sr-only">{label}</span>
            </Button>
          ))}
          <UserMenu />
        </div>
      </SidebarFooter>
    </>
  );
}

function CollapsedSidebarControls({
  disabled,
  query,
  onNewThread,
  onQueryChange,
  onSelectThread,
}: {
  disabled: boolean;
  query: string;
  onNewThread: () => void;
  onQueryChange: (query: string) => void;
  onSelectThread: (thread: ThreadRecord) => void;
}) {
  return (
    <SidebarHeader className="px-4 py-3">
      <div className="flex h-12 items-center gap-4">
        <div className="grid size-9 shrink-0 place-items-center">
          <SlotFlowLogo className="size-7 rounded-md" />
        </div>
        <div className="flex h-12 items-center gap-1 rounded-full border border-border/80 bg-background px-2 shadow-sm">
          <SidebarTrigger className="size-9 rounded-full text-foreground transition-all duration-150 hover:-translate-y-0.5 hover:bg-muted" />
          <SearchMenu
            query={query}
            onQueryChange={onQueryChange}
            onSelectThread={onSelectThread}
            triggerClassName="size-9 rounded-full text-foreground transition-all duration-150 hover:-translate-y-0.5 hover:bg-muted"
          />
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            title="新聊天"
            disabled={disabled}
            className="size-9 rounded-full text-foreground transition-all duration-150 hover:-translate-y-0.5 hover:bg-muted"
            onClick={onNewThread}
          >
            <MessageSquarePlus className="size-4" />
            <span className="sr-only">新聊天</span>
          </Button>
        </div>
      </div>
    </SidebarHeader>
  );
}

function UserMenu() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-9 rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          />
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

type ThreadHistoryProps = {
  activeThreadId: string | null;
  disabled: boolean;
  runStates: Record<string, ThreadRunStatus>;
  filteredThreads: ThreadRecord[];
  isLoading: boolean;
  query: string;
  threadListError: string | null;
  onSelectThread: (thread: ThreadRecord) => void;
  onDeleteThread: (thread: ThreadRecord) => void;
};

/** 线程行右侧的运行状态指示:转圈=生成中,蓝点=有新结果,闪烁蓝点=等待输入,红点=出错。 */
function ThreadRunBadge({ status }: { status: ThreadRunStatus | undefined }) {
  if (!status) {
    return null;
  }
  if (status === "streaming") {
    return (
      <Loader2
        className="size-3.5 shrink-0 animate-spin text-primary"
        aria-label="正在生成"
      />
    );
  }
  return (
    <span
      aria-label={
        status === "needs_input"
          ? "等待你的输入"
          : status === "error"
            ? "运行出错"
            : "有新结果"
      }
      className={cn(
        "size-2 shrink-0 rounded-full",
        status === "error" ? "bg-destructive" : "bg-primary",
        status === "needs_input" && "animate-pulse",
      )}
    />
  );
}

function groupThreadsByTime(threads: ThreadRecord[]): Array<{
  label: string;
  threads: ThreadRecord[];
}> {
  const now = Date.now();
  const dayMs = 24 * 60 * 60 * 1000;
  const buckets: Array<{ label: string; max: number; threads: ThreadRecord[] }> = [
    { label: "今天", max: dayMs, threads: [] },
    { label: "近 7 天", max: 7 * dayMs, threads: [] },
    { label: "近 30 天", max: 30 * dayMs, threads: [] },
    { label: "更早", max: Infinity, threads: [] },
  ];

  for (const thread of threads) {
    const updatedAt = Date.parse(thread.updated_at || thread.created_at);
    const age = Number.isNaN(updatedAt) ? Infinity : now - updatedAt;
    const bucket = buckets.find((item) => age < item.max) ?? buckets[buckets.length - 1];
    bucket.threads.push(thread);
  }

  return buckets
    .filter((bucket) => bucket.threads.length > 0)
    .map(({ label, threads: bucketThreads }) => ({ label, threads: bucketThreads }));
}

function ThreadHistory({
  activeThreadId,
  disabled,
  runStates,
  filteredThreads,
  isLoading,
  query,
  threadListError,
  onSelectThread,
  onDeleteThread,
}: ThreadHistoryProps) {
  const groups = useMemo(
    () => groupThreadsByTime(filteredThreads),
    [filteredThreads],
  );

  return (
    <SidebarGroup className="px-0 pb-3 pt-3 group-data-[collapsible=icon]:hidden">
      <SidebarGroupContent>
        <ScrollArea className="max-h-[calc(100dvh-14rem)] min-h-24">
          {isLoading ? (
            <SidebarMenu className="gap-0.5 pr-1">
              <ThreadSkeletons />
            </SidebarMenu>
          ) : threadListError ? (
            <div className="mx-1 rounded-lg border border-destructive/30 bg-destructive/10 px-2.5 py-2 text-[0.9rem] text-destructive">
              {threadListError}
            </div>
          ) : filteredThreads.length === 0 ? (
            <div className="px-2 py-4 text-[0.92rem] text-muted-foreground">
              {query.trim() ? "没有匹配的聊天" : "暂无最近聊天"}
            </div>
          ) : (
            groups.map((group) => (
              <div key={group.label} className="mb-2">
                <SidebarGroupLabel className="h-7 px-2 text-[0.78rem] font-medium uppercase tracking-wide text-muted-foreground/70">
                  {group.label}
                </SidebarGroupLabel>
                <SidebarMenu className="gap-0.5 pr-1">
                  {group.threads.map((item) => {
                    const runStatus = runStates[item.id];
                    return (
                      <SidebarMenuItem key={item.id} className="group/thread-row">
                        <SidebarMenuButton
                          type="button"
                          isActive={item.id === activeThreadId}
                          disabled={disabled}
                          onClick={() => onSelectThread(item)}
                          className="h-8 rounded-md px-2 text-[0.94rem] font-normal text-muted-foreground transition-colors hover:bg-muted hover:text-foreground data-active:bg-accent data-active:font-medium data-active:text-accent-foreground"
                        >
                          <span className="block min-w-0 flex-1 truncate" title={item.title}>
                            {item.title}
                          </span>
                          {/* 悬停时菜单按钮会盖住行尾;徽标留在行尾但悬停时让位隐藏 */}
                          {runStatus ? (
                            <span className="ml-auto flex shrink-0 items-center pr-0.5 group-hover/thread-row:opacity-0">
                              <ThreadRunBadge status={runStatus} />
                            </span>
                          ) : null}
                        </SidebarMenuButton>
                        <DropdownMenu>
                          <DropdownMenuTrigger
                            render={
                              <SidebarMenuAction
                                showOnHover
                                className="right-1 top-1 size-6 rounded-md bg-sidebar/80 text-muted-foreground hover:bg-background hover:text-foreground"
                                onClick={(event) => {
                                  event.preventDefault();
                                  event.stopPropagation();
                                }}
                              />
                            }
                          >
                            <MoreHorizontal className="size-4" />
                            <span className="sr-only">更多操作</span>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="start" side="right" sideOffset={8} className="w-44 rounded-lg p-1.5">
                            <DropdownMenuItem
                              disabled={disabled || runStatus === "streaming"}
                              variant="destructive"
                              className="gap-2 rounded-md"
                              onClick={(event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                if (!disabled && runStatus !== "streaming") {
                                  onDeleteThread(item);
                                }
                              }}
                            >
                              <Trash2 className="size-4" />
                              {runStatus === "streaming" ? "生成中，不能删除" : "删除"}
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </SidebarMenuItem>
                    );
                  })}
                </SidebarMenu>
              </div>
            ))
          )}
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
          <SidebarMenuSkeleton />
        </SidebarMenuItem>
      ))}
    </>
  );
}
