"use client";

import { type ReactNode, useMemo, useState } from "react";
import {
  type LucideIcon,
  Brain,
  Boxes,
  Check,
  ChevronDown,
  ChevronRight,
  Download,
  FileText,
  Folder,
  GripVertical,
  LibraryBig,
  MessageSquarePlus,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Plug,
  Plus,
  Power,
  PowerOff,
  Search,
  Sparkles,
  Trash2,
  Wrench,
  X,
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
import { Input } from "@/components/ui/input";
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
import {
  type McpServerRecord,
  type MemoryKind,
  type MemoryRecord,
  type SkillRecord,
  type ThreadRecord,
  type WorkspaceEntryRecord,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

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
  totalThreads: number;
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
  totalThreads,
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

type ContextPickerKind = "skills" | "mcp" | "memory" | "artifacts";

type ContextRecordAction = {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  icon?: LucideIcon;
  variant?: "default" | "destructive";
};

const contextPickerConfig = {
  skills: {
    icon: Wrench,
    label: "Skills",
    empty: "暂无已添加 Skill",
    actions: ["上传 Skills 文件夹"],
  },
  mcp: {
    icon: Plug,
    label: "MCP",
    empty: "暂无 MCP 连接",
    actions: ["添加 HTTP MCP"],
  },
  memory: {
    icon: Brain,
    label: "记忆",
    empty: "暂无长期记忆",
    actions: ["添加记忆"],
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

function ContextPickerMenu({
  kind,
  artifacts = [],
  memories = [],
  skills = [],
  mcpServers = [],
  onAddMemory,
  onAddHttpMcpServer,
  onDeleteMcpServer,
  onDeleteMemory,
  onDeleteSkill,
  onDeleteArtifact,
  onEditMemory,
  onInstallSkill,
  onOpenArtifacts,
  onPinMcpServer,
  onPinSkill,
  onPreviewArtifact,
  onReorderMcpServers,
  onReorderSkills,
  onToggleMcpServer,
  onToggleSkill,
  onUploadSkill,
}: {
  kind: ContextPickerKind;
  artifacts?: WorkspaceEntryRecord[];
  memories?: MemoryRecord[];
  skills?: SkillRecord[];
  mcpServers?: McpServerRecord[];
  onAddMemory?: (content: string, kind: MemoryKind) => void;
  onAddHttpMcpServer?: () => void;
  onDeleteMcpServer?: (server: McpServerRecord) => void;
  onDeleteMemory?: (memory: MemoryRecord) => void;
  onDeleteSkill?: (skill: SkillRecord) => void;
  onDeleteArtifact?: (artifact: WorkspaceEntryRecord) => void;
  onEditMemory?: (memory: MemoryRecord, content: string, kind: MemoryKind) => void;
  onInstallSkill?: () => void;
  onOpenArtifacts?: () => void;
  onPinMcpServer?: (server: McpServerRecord, pinned: boolean) => void;
  onPinSkill?: (skill: SkillRecord, pinned: boolean) => void;
  onPreviewArtifact?: (artifact: WorkspaceEntryRecord) => void;
  onReorderMcpServers?: (names: string[]) => void;
  onReorderSkills?: (names: string[]) => void;
  onToggleMcpServer?: (server: McpServerRecord, enabled: boolean) => void;
  onToggleSkill?: (skill: SkillRecord, enabled: boolean) => void;
  onUploadSkill?: () => void;
}) {
  const item = contextPickerConfig[kind];
  const Icon = item.icon;
  const isArtifacts = kind === "artifacts";
  const isSkills = kind === "skills";
  const isMcp = kind === "mcp";
  const isMemory = kind === "memory";
  const hasArtifacts = isArtifacts && artifacts.length > 0;
  const hasSkills = isSkills && skills.length > 0;
  const hasMcpServers = isMcp && mcpServers.length > 0;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<SidebarMenuButton type="button" tooltip={item.label} />}
      >
        <Icon className="size-5" />
        <span>{item.label}</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        side="right"
        align="start"
        sideOffset={8}
        className={kind === "memory" ? "w-[36rem] p-2" : "w-80 p-2"}
      >
        {hasSkills ? (
          <SkillContextList
            skills={skills}
            onDeleteSkill={onDeleteSkill}
            onPinSkill={onPinSkill}
            onReorderSkills={onReorderSkills}
            onToggleSkill={onToggleSkill}
          />
        ) : hasMcpServers ? (
          <McpContextList
            servers={mcpServers}
            onDeleteMcpServer={onDeleteMcpServer}
            onPinMcpServer={onPinMcpServer}
            onReorderMcpServers={onReorderMcpServers}
            onToggleMcpServer={onToggleMcpServer}
          />
        ) : isMemory ? (
          <MemoryTable
            memories={memories}
            onAddMemory={onAddMemory}
            onDeleteMemory={onDeleteMemory}
            onEditMemory={onEditMemory}
          />
        ) : hasArtifacts ? (
          <ArtifactList
            artifacts={artifacts}
            onDeleteArtifact={onDeleteArtifact}
            onPreviewArtifact={onPreviewArtifact}
          />
        ) : (
          <DropdownMenuItem disabled>{item.empty}</DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        {kind === "skills" ? (
          <>
            <DropdownMenuItem onClick={onInstallSkill} className="gap-3">
              <Download className="size-5" />
              从 skills.sh 安装
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onUploadSkill} className="gap-3">
              <Folder className="size-5" />
              上传 Skills 文件夹
            </DropdownMenuItem>
          </>
        ) : kind === "mcp" ? (
          <DropdownMenuItem onClick={onAddHttpMcpServer} className="gap-3">
            <Plus className="size-5" />
            添加 HTTP MCP
          </DropdownMenuItem>
        ) : kind === "memory" ? (
          null
        ) : kind === "artifacts" ? (
          <DropdownMenuItem onClick={onOpenArtifacts} className="gap-3">
            <FileText className="size-5" />
            打开产物面板
          </DropdownMenuItem>
        ) : (
          item.actions.map((action) => (
            <DropdownMenuItem key={action} disabled className="gap-3">
              <Plus className="size-5" />
              {action}
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function SkillContextList({
  skills,
  onDeleteSkill,
  onPinSkill,
  onReorderSkills,
  onToggleSkill,
}: {
  skills: SkillRecord[];
  onDeleteSkill?: (skill: SkillRecord) => void;
  onPinSkill?: (skill: SkillRecord, pinned: boolean) => void;
  onReorderSkills?: (names: string[]) => void;
  onToggleSkill?: (skill: SkillRecord, enabled: boolean) => void;
}) {
  const [draggedName, setDraggedName] = useState<string | null>(null);
  const [expandedParents, setExpandedParents] = useState<Set<string>>(
    () => new Set(),
  );
  const skillsByName = useMemo(
    () => new Map(skills.map((skill) => [skill.name, skill])),
    [skills],
  );
  const childrenByParent = useMemo(() => {
    const groups = new Map<string, SkillRecord[]>();
    for (const skill of skills) {
      if (!skill.parent || !skillsByName.has(skill.parent)) {
        continue;
      }
      groups.set(skill.parent, [...(groups.get(skill.parent) ?? []), skill]);
    }
    return groups;
  }, [skills, skillsByName]);
  const topLevelSkills = skills.filter(
    (skill) => !skill.parent || !skillsByName.has(skill.parent),
  );

  function toggleExpanded(parentName: string) {
    setExpandedParents((current) => {
      const next = new Set(current);
      if (next.has(parentName)) {
        next.delete(parentName);
      } else {
        next.add(parentName);
      }
      return next;
    });
  }

  function reorder(targetName: string) {
    if (!draggedName || draggedName === targetName) {
      return;
    }
    onReorderSkills?.(moveName(skills.map((skill) => skill.name), draggedName, targetName));
  }

  return (
    <div className="flex max-h-72 flex-col gap-1 overflow-y-auto">
      {topLevelSkills.map((skill) => {
        const children = childrenByParent.get(skill.name) ?? [];
        const isExpanded = expandedParents.has(skill.name);
        return (
          <div key={skill.name} className="flex flex-col gap-1">
            <div className="grid grid-cols-[minmax(0,1fr)_2rem] items-center gap-1">
              <ManagedContextRow
                title={skill.name}
                description={skill.description || skill.source}
                enabled={skill.enabled}
                pinned={skill.pinned}
                protectedItem={skill.protected}
                draggable
                isDragging={draggedName === skill.name}
                onDragEnd={() => setDraggedName(null)}
                onDragStart={() => setDraggedName(skill.name)}
                onDrop={() => reorder(skill.name)}
                onPin={() => onPinSkill?.(skill, !skill.pinned)}
                onSelect={children.length > 0 ? () => toggleExpanded(skill.name) : undefined}
                onToggle={() => onToggleSkill?.(skill, !skill.enabled)}
                onDelete={() => onDeleteSkill?.(skill)}
              />
              {children.length > 0 ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={isExpanded ? "收起依赖 Skills" : "展开依赖 Skills"}
                  onClick={() => toggleExpanded(skill.name)}
                >
                  {isExpanded ? (
                    <ChevronDown className="size-4" />
                  ) : (
                    <ChevronRight className="size-4" />
                  )}
                </Button>
              ) : (
                <span />
              )}
            </div>
            {isExpanded && children.length > 0 ? (
              <div className="ml-5 flex flex-col gap-1 border-l pl-2">
                {children.map((child) => (
                  <ManagedContextRow
                    key={child.name}
                    title={child.name}
                    description={child.description || child.source}
                    enabled={child.enabled}
                    pinned={child.pinned}
                    protectedItem={child.protected}
                    draggable
                    className="py-1.5"
                    isDragging={draggedName === child.name}
                    onDragEnd={() => setDraggedName(null)}
                    onDragStart={() => setDraggedName(child.name)}
                    onDrop={() => reorder(child.name)}
                    onPin={() => onPinSkill?.(child, !child.pinned)}
                    onToggle={() => onToggleSkill?.(child, !child.enabled)}
                    onDelete={() => onDeleteSkill?.(child)}
                  />
                ))}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function McpContextList({
  servers,
  onDeleteMcpServer,
  onPinMcpServer,
  onReorderMcpServers,
  onToggleMcpServer,
}: {
  servers: McpServerRecord[];
  onDeleteMcpServer?: (server: McpServerRecord) => void;
  onPinMcpServer?: (server: McpServerRecord, pinned: boolean) => void;
  onReorderMcpServers?: (names: string[]) => void;
  onToggleMcpServer?: (server: McpServerRecord, enabled: boolean) => void;
}) {
  const [draggedName, setDraggedName] = useState<string | null>(null);

  function reorder(targetName: string) {
    if (!draggedName || draggedName === targetName) {
      return;
    }
    onReorderMcpServers?.(moveName(servers.map((server) => server.name), draggedName, targetName));
  }

  return (
    <div className="flex max-h-72 flex-col gap-1 overflow-y-auto">
      {servers.map((server) => (
        <ManagedContextRow
          key={server.name}
          title={server.name}
          description={server.url || server.transport || server.source}
          enabled={server.enabled}
          pinned={server.pinned}
          protectedItem={server.protected}
          draggable
          isDragging={draggedName === server.name}
          onDragEnd={() => setDraggedName(null)}
          onDragStart={() => setDraggedName(server.name)}
          onDrop={() => reorder(server.name)}
          onPin={() => onPinMcpServer?.(server, !server.pinned)}
          onToggle={() => onToggleMcpServer?.(server, !server.enabled)}
          onDelete={() => onDeleteMcpServer?.(server)}
        />
      ))}
    </div>
  );
}

function moveName(names: string[], draggedName: string, targetName: string): string[] {
  const next = names.filter((name) => name !== draggedName);
  const targetIndex = next.indexOf(targetName);
  if (targetIndex === -1) {
    return names;
  }
  next.splice(targetIndex, 0, draggedName);
  return next;
}

function ArtifactList({
  artifacts,
  onDeleteArtifact,
  onPreviewArtifact,
}: {
  artifacts: WorkspaceEntryRecord[];
  onDeleteArtifact?: (artifact: WorkspaceEntryRecord) => void;
  onPreviewArtifact?: (artifact: WorkspaceEntryRecord) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex max-h-56 flex-col gap-1 overflow-y-auto">
        {artifacts.slice(0, 12).map((artifact) => {
          const title = artifact.path.replace(/^artifacts\//, "");
          const description = [
            artifact.kind,
            typeof artifact.size_bytes === "number" ? formatBytes(artifact.size_bytes) : null,
          ].filter(Boolean).join(" · ");
          return (
            <ContextRecordRow
              key={artifact.path}
              title={title}
              description={description}
              disabled={artifact.kind !== "file"}
              onSelect={() => onPreviewArtifact?.(artifact)}
              actions={[
                {
                  label: "打开",
                  onSelect: () => onPreviewArtifact?.(artifact),
                  disabled: artifact.kind !== "file",
                },
                {
                  label: "删除",
                  variant: "destructive",
                  onSelect: () => onDeleteArtifact?.(artifact),
                  disabled: artifact.kind !== "file" || !onDeleteArtifact,
                },
              ]}
            />
          );
        })}
      </div>
    </div>
  );
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

function ContextRecordRow({
  actions,
  className,
  description,
  disabled = false,
  draggable = false,
  enabled,
  extraMenuContent,
  isActive = false,
  isDragging = false,
  title,
  onDragEnd,
  onDragOver,
  onDragStart,
  onDrop,
  onSelect,
}: {
  actions: ContextRecordAction[];
  className?: string;
  description?: string;
  disabled?: boolean;
  draggable?: boolean;
  enabled?: boolean;
  extraMenuContent?: ReactNode;
  isActive?: boolean;
  isDragging?: boolean;
  title: string;
  onDragEnd?: () => void;
  onDragOver?: () => void;
  onDragStart?: () => void;
  onDrop?: () => void;
  onSelect?: () => void;
}) {
  return (
    <div
      draggable={draggable && !disabled}
      onDragEnd={onDragEnd}
      onDragOver={(event) => {
        if (!draggable || disabled) {
          return;
        }
        event.preventDefault();
        onDragOver?.();
      }}
      onDragStart={(event) => {
        if (!draggable || disabled) {
          return;
        }
        event.dataTransfer.effectAllowed = "move";
        onDragStart?.();
      }}
      onDrop={(event) => {
        if (!draggable || disabled) {
          return;
        }
        event.preventDefault();
        onDrop?.();
      }}
      className={cn(
        "group/record flex min-w-0 items-center gap-2 rounded-md px-2 py-2 text-[0.95rem]",
        enabled === false && "opacity-60",
        enabled === true && "bg-muted/70",
        isActive && "bg-accent text-accent-foreground",
        draggable && !disabled && "cursor-grab active:cursor-grabbing",
        isDragging && "ring-1 ring-ring",
        disabled ? "opacity-50" : "hover:bg-accent hover:text-accent-foreground",
        className,
      )}
    >
      {draggable ? (
        <GripVertical className="shrink-0 text-muted-foreground opacity-70 group-hover/record:text-accent-foreground/70" />
      ) : null}
      <button
        type="button"
        disabled={disabled}
        className="min-w-0 flex-1 text-left disabled:cursor-not-allowed"
        onClick={onSelect}
      >
        <span className="block truncate font-medium" title={title}>
          {title}
        </span>
        {description ? (
          <span
            className="block truncate text-xs text-muted-foreground group-hover/record:text-accent-foreground/70"
            title={description}
          >
            {description}
          </span>
        ) : null}
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="shrink-0 opacity-70 group-hover/record:opacity-100"
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
        <DropdownMenuContent align="end" side="right" sideOffset={6} className="w-40">
          {extraMenuContent}
          {extraMenuContent && actions.length > 0 ? <DropdownMenuSeparator /> : null}
          {actions.map((action) => {
            const ActionIcon = action.icon;
            return (
              <DropdownMenuItem
                key={action.label}
                disabled={action.disabled}
                variant={action.variant}
                className="gap-2"
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  if (!action.disabled) {
                    action.onSelect();
                  }
                }}
              >
                {ActionIcon ? <ActionIcon className="size-4" /> : null}
                {action.label}
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

function ManagedContextRow({
  className,
  description,
  draggable,
  enabled,
  isDragging,
  pinned,
  protectedItem,
  title,
  onDragEnd,
  onDragOver,
  onDragStart,
  onDelete,
  onDrop,
  onPin,
  onSelect,
  onToggle,
}: {
  className?: string;
  description: string;
  draggable?: boolean;
  enabled: boolean;
  isDragging?: boolean;
  pinned: boolean;
  protectedItem: boolean;
  title: string;
  onDragEnd?: () => void;
  onDragOver?: () => void;
  onDragStart?: () => void;
  onDelete: () => void;
  onDrop?: () => void;
  onPin: () => void;
  onSelect?: () => void;
  onToggle: () => void;
}) {
  return (
    <ContextRecordRow
      title={title}
      description={description}
      className={className}
      draggable={draggable}
      enabled={enabled}
      isDragging={isDragging}
      onDragEnd={onDragEnd}
      onDragOver={onDragOver}
      onDragStart={onDragStart}
      onDrop={onDrop}
      onSelect={onSelect}
      actions={[
        {
          label: pinned ? "取消置顶" : "置顶",
          onSelect: onPin,
          icon: pinned ? PinOff : Pin,
        },
        {
          label: enabled ? "关闭" : "启用",
          onSelect: onToggle,
          icon: enabled ? PowerOff : Power,
        },
        {
          label: "删除",
          variant: "destructive",
          onSelect: onDelete,
          icon: Trash2,
          disabled: protectedItem,
        },
      ]}
    />
  );
}

const memoryKinds: MemoryKind[] = ["manual", "preference", "profile", "topic", "fact"];

const memoryKindLabels: Record<MemoryKind, string> = {
  manual: "手动",
  preference: "偏好",
  profile: "资料",
  topic: "近期",
  fact: "事实",
};

const memoryKindSections: MemoryKind[] = ["profile", "preference", "topic", "fact", "manual"];

const memoryKindSectionTitles: Record<MemoryKind, string> = {
  manual: "手动记忆",
  preference: "偏好",
  profile: "用户资料",
  topic: "近期话题",
  fact: "事实",
};

function MemoryTable({
  memories,
  onAddMemory,
  onDeleteMemory,
  onEditMemory,
}: {
  memories: MemoryRecord[];
  onAddMemory?: (content: string, kind: MemoryKind) => void;
  onDeleteMemory?: (memory: MemoryRecord) => void;
  onEditMemory?: (memory: MemoryRecord, content: string, kind: MemoryKind) => void;
}) {
  const [draftContent, setDraftContent] = useState("");
  const [draftKind, setDraftKind] = useState<MemoryKind>("manual");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingContent, setEditingContent] = useState("");
  const [editingKind, setEditingKind] = useState<MemoryKind>("manual");
  const groupedMemories = useMemo(
    () =>
      Object.fromEntries(
        memoryKindSections.map((kind) => [
          kind,
          memories.filter((memory) => memory.kind === kind).slice(0, 8),
        ]),
      ) as Record<MemoryKind, MemoryRecord[]>,
    [memories],
  );

  function beginEdit(memory: MemoryRecord) {
    setEditingId(memory.id);
    setEditingContent(memory.content);
    setEditingKind(memory.kind);
  }

  function resetEdit() {
    setEditingId(null);
    setEditingContent("");
    setEditingKind("manual");
  }

  function submitDraft() {
    const content = draftContent.trim();
    if (!content) {
      return;
    }
    onAddMemory?.(content, draftKind);
    setDraftContent("");
    setDraftKind("manual");
  }

  function submitEdit(memory: MemoryRecord) {
    const content = editingContent.trim();
    if (!content) {
      return;
    }
    onEditMemory?.(memory, content, editingKind);
    resetEdit();
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="max-h-96 overflow-y-auto rounded-md border">
        {memoryKindSections.map((kind) => (
          <MemorySection
            key={kind}
            kind={kind}
            memories={groupedMemories[kind]}
            editingId={editingId}
            editingContent={editingContent}
            editingKind={editingKind}
            onBeginEdit={beginEdit}
            onCancelEdit={resetEdit}
            onDeleteMemory={onDeleteMemory}
            onEditingContentChange={setEditingContent}
            onEditingKindChange={setEditingKind}
            onSubmitEdit={submitEdit}
          />
        ))}
      </div>
      <div className="grid grid-cols-[4.5rem_minmax(0,1fr)_2.25rem] items-center gap-2">
        <MemoryKindSelect value={draftKind} onChange={setDraftKind} />
        <Input
          value={draftContent}
          placeholder="添加记忆"
          className="h-9"
          onChange={(event) => setDraftContent(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              submitDraft();
            }
          }}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="添加记忆"
          onClick={submitDraft}
        >
          <Plus className="size-4" />
        </Button>
      </div>
    </div>
  );
}

function MemorySection({
  kind,
  memories,
  editingId,
  editingContent,
  editingKind,
  onBeginEdit,
  onCancelEdit,
  onDeleteMemory,
  onEditingContentChange,
  onEditingKindChange,
  onSubmitEdit,
}: {
  kind: MemoryKind;
  memories: MemoryRecord[];
  editingId: string | null;
  editingContent: string;
  editingKind: MemoryKind;
  onBeginEdit: (memory: MemoryRecord) => void;
  onCancelEdit: () => void;
  onDeleteMemory?: (memory: MemoryRecord) => void;
  onEditingContentChange: (content: string) => void;
  onEditingKindChange: (kind: MemoryKind) => void;
  onSubmitEdit: (memory: MemoryRecord) => void;
}) {
  return (
    <section className="border-b last:border-b-0">
      <div className="flex items-center justify-between bg-muted/50 px-3 py-2">
        <span className="text-xs font-medium">{memoryKindSectionTitles[kind]}</span>
        <span className="text-xs text-muted-foreground">{memories.length}</span>
      </div>
      {memories.length === 0 ? (
        <div className="px-3 py-3 text-xs text-muted-foreground">暂无{memoryKindLabels[kind]}记忆</div>
      ) : (
        memories.map((memory) => {
          const isEditing = editingId === memory.id;
          return (
            <div key={memory.id} className="border-t px-2 py-1.5 first:border-t-0">
              {isEditing ? (
                <div className="grid grid-cols-[minmax(0,1fr)_5.25rem] items-center gap-2">
                  <div className="grid min-w-0 grid-cols-[4.5rem_minmax(0,1fr)] gap-2">
                    <MemoryKindSelect value={editingKind} onChange={onEditingKindChange} />
                    <Input
                      value={editingContent}
                      onChange={(event) => onEditingContentChange(event.target.value)}
                      className="h-8"
                    />
                  </div>
                  <div className="flex justify-end gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label="保存记忆"
                      onClick={() => onSubmitEdit(memory)}
                    >
                      <Check className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label="取消编辑"
                      onClick={onCancelEdit}
                    >
                      <X className="size-4" />
                    </Button>
                  </div>
                </div>
              ) : (
                <ContextRecordRow
                  title={memory.content}
                  description={new Date(memory.updated_at || memory.created_at).toLocaleString()}
                  actions={[
                    {
                      label: "编辑",
                      icon: Pencil,
                      onSelect: () => onBeginEdit(memory),
                    },
                    {
                      label: "删除",
                      icon: Trash2,
                      variant: "destructive",
                      onSelect: () => onDeleteMemory?.(memory),
                      disabled: !onDeleteMemory,
                    },
                  ]}
                />
              )}
            </div>
          );
        })
      )}
    </section>
  );
}

function MemoryKindSelect({
  value,
  onChange,
}: {
  value: MemoryKind;
  onChange: (value: MemoryKind) => void;
}) {
  return (
    <select
      value={value}
      aria-label="记忆类别"
      className="h-8 rounded-md border bg-background px-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onChange={(event) => onChange(event.target.value as MemoryKind)}
    >
      {memoryKinds.map((kind) => (
        <option key={kind} value={kind}>
          {memoryKindLabels[kind]}
        </option>
      ))}
    </select>
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
              filteredThreads.map((item) => {
                const threadArtifacts = getThreadArtifacts(
                  item,
                  artifactFiles,
                  threadArtifactPaths,
                );
                return (
                  <SidebarMenuItem key={item.id}>
                    <ContextRecordRow
                      title={item.title}
                      description={formatThreadTime(item.updated_at)}
                      isActive={item.id === activeThreadId}
                      disabled={disabled}
                      onSelect={() => onSelectThread(item)}
                      extraMenuContent={
                        <ThreadArtifactSubmenu
                          artifacts={threadArtifacts}
                          onPreviewArtifact={onPreviewArtifact}
                        />
                      }
                      actions={[
                        {
                          label: "删除",
                          variant: "destructive",
                          icon: Trash2,
                          disabled,
                          onSelect: () => onDeleteThread(item),
                        },
                      ]}
                    />
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

function getThreadArtifacts(
  thread: ThreadRecord,
  artifacts: WorkspaceEntryRecord[],
  threadArtifactPaths: Record<string, string[]>,
): WorkspaceEntryRecord[] {
  const explicitPaths = new Set(threadArtifactPaths[thread.id] ?? []);
  const threadPrefix = `artifacts/${thread.id}/`;
  return artifacts.filter(
    (artifact) => explicitPaths.has(artifact.path) || artifact.path.startsWith(threadPrefix),
  );
}

function formatThreadTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString();
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
