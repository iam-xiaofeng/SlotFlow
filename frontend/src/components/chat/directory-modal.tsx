"use client";

import { Blocks, Brain, Plug, Plus, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import type {
  McpServerRecord,
  MemoryKind,
  MemoryRecord,
  SkillRecord,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

import {
  McpContextList,
  MemoryTable,
  SkillContextList,
} from "./chat-sidebar-context";

export type DirectoryTab = "skills" | "mcp" | "memory";

const TABS: { id: DirectoryTab; label: string; icon: typeof Blocks }[] = [
  { id: "skills", label: "Skills", icon: Blocks },
  { id: "mcp", label: "MCP", icon: Plug },
  { id: "memory", label: "记忆", icon: Brain },
];

type DirectoryModalProps = {
  open: boolean;
  tab: DirectoryTab;
  onOpenChange: (open: boolean) => void;
  onTabChange: (tab: DirectoryTab) => void;
  skills: SkillRecord[];
  mcpServers: McpServerRecord[];
  memories: MemoryRecord[];
  onInstallSkill: () => void;
  onUploadSkill: () => void;
  onToggleSkill: (skill: SkillRecord, enabled: boolean) => void;
  onPinSkill: (skill: SkillRecord, pinned: boolean) => void;
  onReorderSkills: (names: string[]) => void;
  onDeleteSkill: (skill: SkillRecord) => void;
  onAddHttpMcpServer: () => void;
  onToggleMcpServer: (server: McpServerRecord, enabled: boolean) => void;
  onPinMcpServer: (server: McpServerRecord, pinned: boolean) => void;
  onReorderMcpServers: (names: string[]) => void;
  onDeleteMcpServer: (server: McpServerRecord) => void;
  onAddMemory: (content: string, kind: MemoryKind) => void;
  onEditMemory: (memory: MemoryRecord, content: string, kind: MemoryKind) => void;
  onDeleteMemory: (memory: MemoryRecord) => void;
};

/** A single Directory modal with three tabs (Skills / MCP / 记忆), each managing its
 *  content. Opened from the sidebar; replaces the three separate context dropdowns. */
export function DirectoryModal({
  open,
  tab,
  onOpenChange,
  onTabChange,
  skills,
  mcpServers,
  memories,
  onInstallSkill,
  onUploadSkill,
  onToggleSkill,
  onPinSkill,
  onReorderSkills,
  onDeleteSkill,
  onAddHttpMcpServer,
  onToggleMcpServer,
  onPinMcpServer,
  onReorderMcpServers,
  onDeleteMcpServer,
  onAddMemory,
  onEditMemory,
  onDeleteMemory,
}: DirectoryModalProps) {
  const activeLabel = TABS.find((item) => item.id === tab)?.label ?? "目录";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="left"
        className="flex w-full flex-row gap-0 p-0 sm:max-w-3xl"
      >
        <nav className="flex w-44 shrink-0 flex-col gap-1 border-r bg-muted/20 p-3">
          <SheetTitle className="px-2 pb-2 text-base">目录</SheetTitle>
          {TABS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onTabChange(item.id)}
                className={cn(
                  "flex items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                  tab === item.id && "bg-muted font-medium text-foreground",
                )}
              >
                <Icon className="size-4" />
                {item.label}
              </button>
            );
          })}
        </nav>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b px-4 pr-12">
            <span className="text-base font-medium">{activeLabel}</span>
            {tab === "skills" ? (
              <div className="flex shrink-0 items-center gap-1">
                <Button type="button" size="sm" variant="outline" onClick={onInstallSkill}>
                  <Plus className="size-4" />
                  安装
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={onUploadSkill}>
                  <Upload className="size-4" />
                  上传
                </Button>
              </div>
            ) : tab === "mcp" ? (
              <Button type="button" size="sm" variant="outline" onClick={onAddHttpMcpServer}>
                <Plus className="size-4" />
                添加 MCP
              </Button>
            ) : null}
          </div>

          <ScrollArea className="min-h-0 flex-1">
            <div className="p-4">
              {tab === "skills" ? (
                <SkillContextList
                  skills={skills}
                  onDeleteSkill={onDeleteSkill}
                  onPinSkill={onPinSkill}
                  onReorderSkills={onReorderSkills}
                  onToggleSkill={onToggleSkill}
                />
              ) : tab === "mcp" ? (
                <McpContextList
                  servers={mcpServers}
                  onDeleteMcpServer={onDeleteMcpServer}
                  onPinMcpServer={onPinMcpServer}
                  onReorderMcpServers={onReorderMcpServers}
                  onToggleMcpServer={onToggleMcpServer}
                />
              ) : (
                <MemoryTable
                  memories={memories}
                  onAddMemory={onAddMemory}
                  onDeleteMemory={onDeleteMemory}
                  onEditMemory={onEditMemory}
                />
              )}
            </div>
          </ScrollArea>
        </div>
      </SheetContent>
    </Sheet>
  );
}
