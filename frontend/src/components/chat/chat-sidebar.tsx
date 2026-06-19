"use client";

import { useMemo } from "react";
import {
  Boxes,
  FileText,
  Folder,
  LibraryBig,
  MessageSquarePlus,
  MoreHorizontal,
  Trash2,
} from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
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
  type SkillRecord,
  type ThreadRecord,
  type WorkspaceEntryRecord,
} from "@/lib/chat-stream";

import { filterThreadArtifacts } from "./chat-format";
import { ContextPickerMenu } from "./chat-sidebar-context";
import { SearchMenu } from "./chat-sidebar-search";
import { SlotFlowLogo } from "./slotflow-logo";

type ThreadSidebarProps = {
  activeThreadId: string | null;
  artifacts: WorkspaceEntryRecord[];
  disabled: boolean;
  filteredThreads: ThreadRecord[];
  isLoading: boolean;
  memories: MemoryRecord[];
  mcpServers: McpServerRecord[];
  query: string;
  skills: SkillRecord[];
  threadArtifactPaths: Record<string, string[]>;
  threadListError: string | null;
  onAddHttpMcpServer: () => void;
  onAddMemory: (content: string, kind: MemoryKind) => void;
  onDeleteMcpServer: (server: McpServerRecord) => void;
  onDeleteMemory: (memory: MemoryRecord) => void;
  onDeleteSkill: (skill: SkillRecord) => void;
  onDeleteArtifact: (artifact: WorkspaceEntryRecord) => void;
  onDeleteThread: (thread: ThreadRecord) => void;
  onEditMemory: (memory: MemoryRecord, content: string, kind: MemoryKind) => void;
  onInstallSkill: () => void;
  onNewThread: () => void;
  onOpenArtifacts: () => void;
  onPinMcpServer: (server: McpServerRecord, pinned: boolean) => void;
  onPinSkill: (skill: SkillRecord, pinned: boolean) => void;
  onPreviewArtifact: (artifact: WorkspaceEntryRecord) => void;
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
  artifacts,
  disabled,
  filteredThreads,
  isLoading,
  memories,
  mcpServers,
  query,
  skills,
  threadArtifactPaths,
  threadListError,
  onAddHttpMcpServer,
  onAddMemory,
  onDeleteMcpServer,
  onDeleteMemory,
  onDeleteSkill,
  onDeleteArtifact,
  onDeleteThread,
  onEditMemory,
  onInstallSkill,
  onNewThread,
  onOpenArtifacts,
  onPinMcpServer,
  onPinSkill,
  onPreviewArtifact,
  onQueryChange,
  onReorderMcpServers,
  onReorderSkills,
  onSelectThread,
  onToggleMcpServer,
  onToggleSkill,
  onUploadSkill,
}: ThreadSidebarProps) {
  const { state } = useSidebar();

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
            <SidebarTrigger className="size-8 rounded-lg text-muted-foreground transition-all duration-150 hover:-translate-y-0.5 hover:bg-muted hover:text-foreground" />
          </div>
        </div>
      </SidebarHeader>

      <SidebarContent className="px-2">
        <SidebarGroup className="pb-3 pt-1">
          <SidebarGroupContent>
            <SidebarMenu className="gap-0.5">
              <SidebarMenuItem>
                <SidebarMenuButton
                  type="button"
                  tooltip="新聊天"
                  onClick={onNewThread}
                  disabled={disabled}
                  isActive={!activeThreadId}
                  className="h-9 rounded-lg px-2.5 text-[0.95rem] font-normal text-muted-foreground transition-all duration-150 hover:-translate-y-0.5 hover:bg-muted hover:text-foreground data-active:bg-muted data-active:text-foreground"
                >
                  <MessageSquarePlus className="size-4" />
                  <span>新聊天</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <ContextPickerMenu
                  kind="skills"
                  skills={skills}
                  onDeleteSkill={onDeleteSkill}
                  onInstallSkill={onInstallSkill}
                  onPinSkill={onPinSkill}
                  onReorderSkills={onReorderSkills}
                  onToggleSkill={onToggleSkill}
                  onUploadSkill={onUploadSkill}
                />
              </SidebarMenuItem>
              <SidebarMenuItem>
                <ContextPickerMenu
                  kind="mcp"
                  mcpServers={mcpServers}
                  onAddHttpMcpServer={onAddHttpMcpServer}
                  onDeleteMcpServer={onDeleteMcpServer}
                  onPinMcpServer={onPinMcpServer}
                  onReorderMcpServers={onReorderMcpServers}
                  onToggleMcpServer={onToggleMcpServer}
                />
              </SidebarMenuItem>
              <SidebarMenuItem>
                <ContextPickerMenu
                  kind="memory"
                  memories={memories}
                  onAddMemory={onAddMemory}
                  onDeleteMemory={onDeleteMemory}
                  onEditMemory={onEditMemory}
                />
              </SidebarMenuItem>
              <SidebarMenuItem>
                <ContextPickerMenu
                  kind="artifacts"
                  artifacts={artifacts}
                  onDeleteArtifact={onDeleteArtifact}
                  onOpenArtifacts={onOpenArtifacts}
                  onPreviewArtifact={onPreviewArtifact}
                />
              </SidebarMenuItem>
              <SidebarMenuItem>
                <MoreToolsMenu />
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <ThreadHistory
          activeThreadId={activeThreadId}
          disabled={disabled}
          filteredThreads={filteredThreads}
          artifacts={artifacts}
          isLoading={isLoading}
          query={query}
          threadArtifactPaths={threadArtifactPaths}
          threadListError={threadListError}
          onPreviewArtifact={onPreviewArtifact}
          onSelectThread={onSelectThread}
          onDeleteThread={onDeleteThread}
        />
      </SidebarContent>

      <SidebarFooter className="mt-auto px-2 pb-3 pt-2">
        <UserMenu />
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
        <div className="grid size-9 shrink-0 place-items-center rounded-full text-sm font-semibold text-blue-600">
          S
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

export function UserMenu() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-9 rounded-full text-muted-foreground transition-all duration-150 hover:-translate-y-0.5 hover:bg-muted hover:text-foreground"
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

function MoreToolsMenu() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <SidebarMenuButton
            type="button"
            className="h-9 rounded-lg px-2.5 text-[0.95rem] font-normal text-muted-foreground transition-all duration-150 hover:-translate-y-0.5 hover:bg-muted hover:text-foreground data-open:bg-muted data-open:text-foreground"
          />
        }
      >
        <MoreHorizontal className="size-4" />
        <span>更多</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="right" align="start" sideOffset={8} className="w-52 rounded-2xl p-2 shadow-xl">
        <DropdownMenuItem disabled className="min-h-11 gap-3 rounded-xl">
          <LibraryBig className="size-5" />
          库
        </DropdownMenuItem>
        <DropdownMenuItem disabled className="min-h-11 gap-3 rounded-xl">
          <Folder className="size-5" />
          项目
        </DropdownMenuItem>
        <DropdownMenuItem disabled className="min-h-11 gap-3 rounded-xl">
          <Boxes className="size-5" />
          应用
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

type ThreadHistoryProps = {
  activeThreadId: string | null;
  artifacts: WorkspaceEntryRecord[];
  disabled: boolean;
  filteredThreads: ThreadRecord[];
  isLoading: boolean;
  query: string;
  threadArtifactPaths: Record<string, string[]>;
  threadListError: string | null;
  onPreviewArtifact: (artifact: WorkspaceEntryRecord) => void;
  onSelectThread: (thread: ThreadRecord) => void;
  onDeleteThread: (thread: ThreadRecord) => void;
};

function ThreadHistory({
  activeThreadId,
  artifacts,
  disabled,
  filteredThreads,
  isLoading,
  query,
  threadArtifactPaths,
  threadListError,
  onPreviewArtifact,
  onSelectThread,
  onDeleteThread,
}: ThreadHistoryProps) {
  const artifactFiles = useMemo(
    () => artifacts.filter((artifact) => artifact.kind === "file"),
    [artifacts],
  );

  return (
    <SidebarGroup className="px-0 pb-3 pt-5 group-data-[collapsible=icon]:hidden">
      <SidebarGroupLabel className="h-7 px-2 text-[0.95rem] font-semibold text-foreground">
        最近
      </SidebarGroupLabel>
      <SidebarGroupContent>
        <ScrollArea className="max-h-[calc(100dvh-18rem)] min-h-24">
          <SidebarMenu className="gap-0.5 pr-1">
            {isLoading ? (
              <ThreadSkeletons />
            ) : threadListError ? (
              <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-2.5 py-2 text-[0.9rem] text-destructive">
                {threadListError}
              </div>
            ) : filteredThreads.length === 0 ? (
              <div className="px-2 py-4 text-[0.92rem] text-muted-foreground">
                {query.trim() ? "没有匹配的聊天" : "暂无最近聊天"}
              </div>
            ) : (
              filteredThreads.map((item) => {
                const threadArtifacts = filterThreadArtifacts(
                  item.id,
                  artifactFiles,
                  threadArtifactPaths,
                );
                return (
                  <SidebarMenuItem key={item.id} className="group/thread-row">
                    <SidebarMenuButton
                      type="button"
                      isActive={item.id === activeThreadId}
                      disabled={disabled}
                      onClick={() => onSelectThread(item)}
                      className="h-8 rounded-md px-2 text-[0.94rem] font-normal text-muted-foreground transition-all duration-150 hover:-translate-y-0.5 hover:bg-muted hover:text-foreground data-active:bg-muted data-active:text-foreground"
                    >
                      <span className="block min-w-0 truncate" title={item.title}>
                        {item.title}
                      </span>
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
                        <ThreadArtifactSubmenu
                          artifacts={threadArtifacts}
                          onPreviewArtifact={onPreviewArtifact}
                        />
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          disabled={disabled}
                          variant="destructive"
                          className="gap-2 rounded-md"
                          onClick={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            if (!disabled) {
                              onDeleteThread(item);
                            }
                          }}
                        >
                          <Trash2 className="size-4" />
                          删除
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </SidebarMenuItem>
                );
              })
            )}
          </SidebarMenu>
        </ScrollArea>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

function ThreadArtifactSubmenu({
  artifacts,
  onPreviewArtifact,
}: {
  artifacts: WorkspaceEntryRecord[];
  onPreviewArtifact: (artifact: WorkspaceEntryRecord) => void;
}) {
  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger disabled={artifacts.length === 0}>
        <FileText className="size-4" />
        产物
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent className="w-72">
        {artifacts.length === 0 ? (
          <DropdownMenuItem disabled>这个聊天暂无产物</DropdownMenuItem>
        ) : (
          artifacts.map((artifact) => (
            <DropdownMenuItem
              key={artifact.path}
              className="min-w-0 gap-2"
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onPreviewArtifact(artifact);
              }}
            >
              <FileText className="size-4" />
              <span className="min-w-0 truncate">
                {artifact.path.replace(/^artifacts\//, "")}
              </span>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuSubContent>
    </DropdownMenuSub>
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
