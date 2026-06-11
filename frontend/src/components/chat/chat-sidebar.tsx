import {
  type LucideIcon,
  Boxes,
  FileText,
  Folder,
  History,
  LibraryBig,
  MessageSquarePlus,
  MoreHorizontal,
  Plug,
  Plus,
  Search,
  Sparkles,
  Wrench,
} from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInput,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSkeleton,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { type ThreadRecord } from "@/lib/chat-stream";

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

export function ThreadSidebar({
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

export function UserMenu() {
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

const contextPickerConfig = {
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
    icon: LucideIcon;
    label: string;
    empty: string;
    actions: string[];
  }
>;

function ContextPickerMenu({ kind }: { kind: ContextPickerKind }) {
  const item = contextPickerConfig[kind];
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
      <DropdownMenuTrigger render={<SidebarMenuButton type="button" />}>
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
