"use client";

import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import {
  Blocks,
  Brain,
  Check,
  ChevronDown,
  Download,
  ExternalLink,
  LoaderCircle,
  Pencil,
  Pin,
  PinOff,
  Plug,
  Plus,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import type {
  McpServerRecord,
  MemoryKind,
  MemoryRecord,
  SkillInstallRequest,
  SkillRecord,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

export type DirectoryTab = "skills" | "mcp" | "memory";

const TABS: { id: DirectoryTab; label: string; icon: typeof Blocks }[] = [
  { id: "skills", label: "Skills", icon: Blocks },
  { id: "mcp", label: "MCP", icon: Plug },
  { id: "memory", label: "记忆", icon: Brain },
];

const MEMORY_KINDS: { value: MemoryKind; label: string }[] = [
  { value: "manual", label: "手动" },
  { value: "preference", label: "偏好" },
  { value: "profile", label: "档案" },
  { value: "topic", label: "话题" },
  { value: "fact", label: "事实" },
];

const SEARCH_PLACEHOLDER: Record<DirectoryTab, string> = {
  skills: "搜索 Skills…",
  mcp: "搜索 MCP 服务…",
  memory: "搜索记忆…",
};

type RecommendedSkill = SkillInstallRequest & {
  label: string;
  description: string;
};

const RECOMMENDED_SKILLS: RecommendedSkill[] = [
  {
    label: "agency-agents",
    description: "Agency-style agent workflows and helper skills.",
    package_url: "https://github.com/msitarzewski/agency-agents",
    skill_name: "agency-agents",
  },
  {
    label: "Agent-Reach",
    description: "Agent reach / outreach workflow skills.",
    package_url: "https://github.com/Panniantong/Agent-Reach",
    skill_name: "Agent-Reach",
  },
  {
    label: "nature-skills",
    description: "Nature and science-oriented skill collection.",
    package_url: "https://github.com/Yuan1z0825/nature-skills",
    skill_name: "nature-skills",
  },
];

type DirectoryModalProps = {
  open: boolean;
  tab: DirectoryTab;
  onOpenChange: (open: boolean) => void;
  onTabChange: (tab: DirectoryTab) => void;
  skills: SkillRecord[];
  mcpServers: McpServerRecord[];
  memories: MemoryRecord[];
  onInstallSkill: (request?: SkillInstallRequest) => Promise<void> | void;
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

/** Centered Directory modal for Skills / MCP / 记忆 management. */
export function DirectoryModal(props: DirectoryModalProps) {
  const { open, tab, onOpenChange, onTabChange } = props;
  const [query, setQuery] = useState("");
  const activeLabel = TABS.find((item) => item.id === tab)?.label ?? "目录";

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/45 transition-opacity duration-200 data-ending-style:opacity-0 data-starting-style:opacity-0 supports-backdrop-filter:backdrop-blur-sm" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-50 grid h-[min(86vh,52rem)] w-[min(92vw,64rem)] -translate-x-1/2 -translate-y-1/2 grid-cols-[14rem_minmax(0,1fr)] overflow-hidden rounded-xl border bg-background text-foreground shadow-2xl transition-all duration-200 data-ending-style:scale-[0.98] data-ending-style:opacity-0 data-starting-style:scale-[0.98] data-starting-style:opacity-0 max-sm:h-[min(90vh,42rem)] max-sm:w-[94vw] max-sm:grid-cols-1">
          <nav className="flex min-h-0 flex-col gap-1 border-r bg-background p-6 max-sm:hidden">
            <Dialog.Title className="mb-4 text-xl font-semibold tracking-normal">目录</Dialog.Title>
            {TABS.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    onTabChange(item.id);
                    setQuery("");
                  }}
                  className={cn(
                    "flex h-10 items-center gap-3 rounded-lg px-3 text-left text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                    tab === item.id && "bg-muted font-medium text-foreground shadow-sm",
                  )}
                >
                  <Icon className="size-4" />
                  {item.label}
                </button>
              );
            })}
          </nav>

          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <div className="flex h-16 shrink-0 items-center gap-3 border-b px-5 max-sm:h-auto max-sm:flex-wrap max-sm:py-4">
              <Dialog.Title className="hidden w-full text-lg font-semibold max-sm:block">
                目录
              </Dialog.Title>
              <div className="hidden w-full gap-1 max-sm:flex">
                {TABS.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => {
                      onTabChange(item.id);
                      setQuery("");
                    }}
                    className={cn(
                      "h-8 flex-1 rounded-lg px-2 text-sm text-muted-foreground",
                      tab === item.id && "bg-muted font-medium text-foreground",
                    )}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <div className="relative min-w-0 flex-1">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={SEARCH_PLACEHOLDER[tab]}
                  className="h-10 w-full rounded-lg border border-input bg-background pl-9 pr-3 text-sm outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
                />
              </div>
              {tab === "skills" ? (
                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => void props.onInstallSkill()}
                  >
                    <Plus className="size-4" />
                    安装
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={props.onUploadSkill}>
                    <Upload className="size-4" />
                    上传
                  </Button>
                </div>
              ) : tab === "mcp" ? (
                <Button type="button" size="sm" variant="outline" onClick={props.onAddHttpMcpServer}>
                  <Plus className="size-4" />
                  添加 MCP
                </Button>
              ) : null}
              <Dialog.Close
                render={<Button type="button" size="icon-sm" variant="ghost" className="ml-1" />}
              >
                <X className="size-4" />
                <span className="sr-only">关闭 {activeLabel}</span>
              </Dialog.Close>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]">
              <div className="p-5">
                {tab === "skills" ? (
                  <SkillsGrid query={query} {...props} />
                ) : tab === "mcp" ? (
                  <McpGrid query={query} {...props} />
                ) : (
                  <MemoryGrid query={query} {...props} />
                )}
              </div>
            </div>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ToggleSwitch({
  enabled,
  onChange,
  disabled,
}: {
  enabled: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      disabled={disabled}
      onClick={() => onChange(!enabled)}
      className={cn(
        "relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-50",
        enabled ? "bg-primary" : "bg-muted-foreground/30",
      )}
    >
      <span
        className={cn(
          "absolute top-0.5 size-4 rounded-full bg-white shadow-sm transition-all",
          enabled ? "left-[1.125rem]" : "left-0.5",
        )}
      />
    </button>
  );
}

function CardGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 gap-3 md:grid-cols-2">{children}</div>;
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="grid place-items-center py-16 text-center text-sm text-muted-foreground">
      {text}
    </div>
  );
}

function CardChip({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
      {children}
    </span>
  );
}

type SkillGroup = {
  root: SkillRecord;
  children: SkillRecord[];
};

function SkillsGrid({
  query,
  skills,
  onInstallSkill,
  onToggleSkill,
  onPinSkill,
  onDeleteSkill,
}: { query: string } & DirectoryModalProps) {
  const [installingSkillName, setInstallingSkillName] = useState<string | null>(null);
  const q = query.trim().toLowerCase();
  const matches = (skill: SkillRecord) =>
    !q ||
    skill.name.toLowerCase().includes(q) ||
    (skill.description ?? "").toLowerCase().includes(q);
  const installedNames = useMemo(
    () => new Set(skills.map((skill) => skill.name.toLowerCase())),
    [skills],
  );
  const recommendedSkills = useMemo(
    () =>
      RECOMMENDED_SKILLS.filter(
        (skill) =>
          !q ||
          skill.label.toLowerCase().includes(q) ||
          skill.description.toLowerCase().includes(q) ||
          skill.package_url.toLowerCase().includes(q),
      ),
    [q],
  );

  // Group by parent: skills with parent===null (or whose parent isn't in the list) are
  // roots; the rest nest under their parent. A multi-skill package like nature-skills
  // then shows as ONE root card with a collapsible list of its sub-skills instead of a
  // flat wall of cards.
  const groups = useMemo<SkillGroup[]>(() => {
    const byName = new Map(skills.map((skill) => [skill.name, skill]));
    const childrenByParent = new Map<string, SkillRecord[]>();
    const roots: SkillRecord[] = [];
    for (const skill of skills) {
      const parentName = skill.parent ?? null;
      if (parentName && byName.has(parentName)) {
        const list = childrenByParent.get(parentName) ?? [];
        list.push(skill);
        childrenByParent.set(parentName, list);
      } else {
        roots.push(skill);
      }
    }
    return roots
      .map((root) => ({
        root,
        children: (childrenByParent.get(root.name) ?? []).slice().sort(sortSkillsPinnedFirst),
      }))
      .sort((a, b) => sortSkillsPinnedFirst(a.root, b.root));
  }, [skills]);

  // When searching, keep a root if it or any of its children matches; auto-expand those.
  const visible = useMemo(() => {
    if (!q) {
      return groups;
    }
    return groups
      .map((group) => {
        const rootHit = matches(group.root);
        const childHits = group.children.filter(matches);
        if (!rootHit && childHits.length === 0) {
          return null;
        }
        return {
          root: group.root,
          children: rootHit ? group.children : childHits,
        };
      })
      .filter((group): group is SkillGroup => group !== null);
  }, [groups, q]);

  async function installRecommendedSkill(skill: RecommendedSkill) {
    setInstallingSkillName(skill.skill_name);
    try {
      await onInstallSkill({
        package_url: skill.package_url,
        skill_name: skill.skill_name,
      });
    } finally {
      setInstallingSkillName(null);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <RecommendedSkillsSection
        skills={recommendedSkills}
        installedNames={installedNames}
        installingSkillName={installingSkillName}
        onInstallSkill={(skill) => void installRecommendedSkill(skill)}
      />
      {visible.length > 0 ? (
        <section className="flex flex-col gap-3">
          <SectionHeading title="已安装 Skills" description="当前可用的本地 Skills。" />
          <CardGrid>
            {visible.map((group) => (
              <SkillGroupCard
                key={group.root.name}
                group={group}
                query={q}
                onToggleSkill={onToggleSkill}
                onPinSkill={onPinSkill}
                onDeleteSkill={onDeleteSkill}
              />
            ))}
          </CardGrid>
        </section>
      ) : (
        <EmptyState text={q ? "没有匹配的已安装 Skill" : "暂无已安装 Skill，点推荐卡片下载或右上角安装/上传"} />
      )}
    </div>
  );
}

function RecommendedSkillsSection({
  skills,
  installedNames,
  installingSkillName,
  onInstallSkill,
}: {
  skills: RecommendedSkill[];
  installedNames: Set<string>;
  installingSkillName: string | null;
  onInstallSkill: (skill: RecommendedSkill) => void;
}) {
  if (skills.length === 0) {
    return null;
  }

  return (
    <section className="flex flex-col gap-3">
      <SectionHeading
        title="推荐 Skills"
        description="精选仓库，点击下载后直接安装到本地 Skills。"
      />
      <CardGrid>
        {skills.map((skill) => {
          const installed = installedNames.has(skill.skill_name.toLowerCase());
          const installing = installingSkillName === skill.skill_name;
          return (
            <div
              key={skill.package_url}
              className="flex min-h-32 flex-col gap-3 rounded-lg border border-primary/20 bg-primary/5 p-4"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  <Download className="size-4 shrink-0 text-primary" />
                  <span className="truncate font-medium">{skill.label}</span>
                </div>
                <CardChip>{installed ? "已安装" : "推荐"}</CardChip>
              </div>
              <p className="line-clamp-2 min-h-[2.5rem] text-sm text-muted-foreground">
                {skill.description}
              </p>
              <div className="mt-auto flex items-center justify-between gap-2">
                <a
                  href={skill.package_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex min-w-0 items-center gap-1 truncate text-xs text-muted-foreground hover:text-foreground"
                >
                  <ExternalLink className="size-3.5 shrink-0" />
                  <span className="truncate">{skill.package_url.replace("https://github.com/", "")}</span>
                </a>
                <Button
                  type="button"
                  size="sm"
                  disabled={installed || installing}
                  onClick={() => onInstallSkill(skill)}
                  className="shrink-0"
                >
                  {installing ? (
                    <LoaderCircle className="mr-1.5 size-3.5 animate-spin" />
                  ) : null}
                  {installed ? "已安装" : installing ? "下载中" : "下载"}
                </Button>
              </div>
            </div>
          );
        })}
      </CardGrid>
    </section>
  );
}

function SectionHeading({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      <p className="text-xs text-muted-foreground">{description}</p>
    </div>
  );
}

function sortSkillsPinnedFirst(a: SkillRecord, b: SkillRecord) {
  return Number(b.pinned) - Number(a.pinned);
}

function SkillGroupCard({
  group,
  query,
  onToggleSkill,
  onPinSkill,
  onDeleteSkill,
}: {
  group: SkillGroup;
  query: string;
  onToggleSkill: DirectoryModalProps["onToggleSkill"];
  onPinSkill: DirectoryModalProps["onPinSkill"];
  onDeleteSkill: DirectoryModalProps["onDeleteSkill"];
}) {
  const hasChildren = group.children.length > 0;
  // Auto-expand while searching (so matched sub-skills are visible) or on user toggle.
  const [expanded, setExpanded] = useState(hasChildren && query.length > 0);
  useEffect(() => {
    if (query.length > 0 && hasChildren) {
      setExpanded(true);
    }
  }, [query, hasChildren]);

  return (
    <div className="group flex min-h-32 flex-col gap-2 rounded-lg border bg-card p-4 transition-colors hover:border-foreground/20 hover:shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Blocks className="size-4 shrink-0 text-primary" />
          <span className="truncate font-medium">{group.root.name}</span>
          {group.root.pinned ? <Pin className="size-3.5 shrink-0 text-amber-500" /> : null}
        </div>
        <ToggleSwitch
          enabled={group.root.enabled}
          onChange={(next) => onToggleSkill(group.root, next)}
        />
      </div>
      <p className="line-clamp-2 min-h-[2.5rem] text-sm text-muted-foreground">
        {group.root.description || "（无描述）"}
      </p>
      <div className="mt-auto flex items-center justify-between gap-2 pt-1">
        <div className="flex min-w-0 items-center gap-2">
          <CardChip>{group.root.source}</CardChip>
          {hasChildren ? (
            <button
              type="button"
              onClick={() => setExpanded((current) => !current)}
              className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
              aria-expanded={expanded}
            >
              <span>{group.children.length} 个子 Skill</span>
              <ChevronDown
                className={cn("size-3.5 transition-transform", expanded && "rotate-180")}
              />
            </button>
          ) : null}
        </div>
        <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
          <Button
            type="button"
            size="icon-xs"
            variant="ghost"
            title={group.root.pinned ? "取消置顶" : "置顶"}
            onClick={() => onPinSkill(group.root, !group.root.pinned)}
          >
            {group.root.pinned ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />}
          </Button>
          {!group.root.protected ? (
            <Button
              type="button"
              size="icon-xs"
              variant="ghost"
              title="删除"
              className="text-muted-foreground hover:text-destructive"
              onClick={() => onDeleteSkill(group.root)}
            >
              <Trash2 className="size-3.5" />
            </Button>
          ) : null}
        </div>
      </div>
      {hasChildren && expanded ? (
        <ul className="mt-1 flex max-h-60 flex-col gap-1 overflow-y-auto overscroll-contain border-t pt-2 [scrollbar-gutter:stable]">
          {group.children.map((child) => (
            <li
              key={child.name}
              className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-muted/50"
            >
              <div className="flex min-w-0 items-center gap-2">
                <Blocks className="size-3.5 shrink-0 text-muted-foreground" />
                <div className="flex min-w-0 flex-col">
                  <span className="truncate">{child.name}</span>
                  {child.description ? (
                    <span className="truncate text-xs text-muted-foreground">
                      {child.description}
                    </span>
                  ) : null}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <ToggleSwitch
                  enabled={child.enabled}
                  onChange={(next) => onToggleSkill(child, next)}
                />
                {!child.protected ? (
                  <Button
                    type="button"
                    size="icon-xs"
                    variant="ghost"
                    title="删除"
                    className="text-muted-foreground hover:text-destructive"
                    onClick={() => onDeleteSkill(child)}
                  >
                    <Trash2 className="size-3" />
                  </Button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function McpGrid({
  query,
  mcpServers,
  onToggleMcpServer,
  onPinMcpServer,
  onDeleteMcpServer,
}: { query: string } & DirectoryModalProps) {
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q
      ? mcpServers.filter(
          (server) =>
            server.name.toLowerCase().includes(q) ||
            (server.url ?? "").toLowerCase().includes(q),
        )
      : mcpServers;
    return [...list].sort((a, b) => Number(b.pinned) - Number(a.pinned));
  }, [mcpServers, query]);

  if (filtered.length === 0) {
    return <EmptyState text={query ? "没有匹配的 MCP 服务" : "暂无 MCP 服务，点右上角「添加 MCP」"} />;
  }

  return (
    <CardGrid>
      {filtered.map((server) => (
        <div
          key={server.name}
          className="group flex min-h-32 flex-col gap-2 rounded-lg border bg-card p-4 transition-colors hover:border-foreground/20 hover:shadow-sm"
        >
          <div className="flex items-start justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <Plug className="size-4 shrink-0 text-primary" />
              <span className="truncate font-medium">{server.name}</span>
            </div>
            <ToggleSwitch
              enabled={server.enabled}
              onChange={(next) => onToggleMcpServer(server, next)}
            />
          </div>
          <p className="line-clamp-2 min-h-[2.5rem] break-all text-sm text-muted-foreground">
            {server.url || server.transport || "—"}
          </p>
          <div className="mt-auto flex items-center justify-between gap-2 pt-1">
            <CardChip>{server.source === "environment" ? "环境配置" : "用户添加"}</CardChip>
            <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
              <Button
                type="button"
                size="icon-xs"
                variant="ghost"
                title={server.pinned ? "取消置顶" : "置顶"}
                onClick={() => onPinMcpServer(server, !server.pinned)}
              >
                {server.pinned ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />}
              </Button>
              {!server.protected ? (
                <Button
                  type="button"
                  size="icon-xs"
                  variant="ghost"
                  title="删除"
                  className="text-muted-foreground hover:text-destructive"
                  onClick={() => onDeleteMcpServer(server)}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      ))}
    </CardGrid>
  );
}

function displayMemoryContent(content: string): string {
  // The category is shown as a section header, so drop the redundant "用户的…是：" prefix
  // (and tidy multi-field profile sentences) for a concise, table-like reading.
  let text = content.trim();
  text = text.replace(/^(用户的偏好是|用户资料|用户近期关注|用户事实|用户记录|用户的)\s*[:：]?\s*/, "");
  text = text.replace(/。用户的/g, "。").replace(/用户的/g, "");
  return text.trim() || content.trim();
}

function MemoryGrid({
  query,
  memories,
  onAddMemory,
  onEditMemory,
  onDeleteMemory,
}: { query: string } & DirectoryModalProps) {
  const [draftContent, setDraftContent] = useState("");
  const [draftKind, setDraftKind] = useState<MemoryKind>("manual");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q
      ? memories.filter((memory) => memory.content.toLowerCase().includes(q))
      : memories;
  }, [memories, query]);

  // Group by category in a stable, reading-friendly order.
  const groups = useMemo(() => {
    return MEMORY_KINDS.map((kind) => ({
      kind: kind.value,
      label: kind.label,
      items: filtered.filter((memory) => memory.kind === kind.value),
    })).filter((group) => group.items.length > 0);
  }, [filtered]);

  function submitDraft() {
    const content = draftContent.trim();
    if (!content) {
      return;
    }
    onAddMemory(content, draftKind);
    setDraftContent("");
    setDraftKind("manual");
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2 rounded-lg border bg-muted/20 p-3">
        <textarea
          value={draftContent}
          onChange={(event) => setDraftContent(event.target.value)}
          placeholder="添加一条长期记忆…（模型会在后续对话中作为背景参考）"
          rows={2}
          className="w-full resize-none rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
        />
        <div className="flex items-center justify-between gap-2">
          <select
            value={draftKind}
            onChange={(event) => setDraftKind(event.target.value as MemoryKind)}
            className="h-8 rounded-lg border border-input bg-background px-2 text-xs outline-none"
          >
            {MEMORY_KINDS.map((kind) => (
              <option key={kind.value} value={kind.value}>
                {kind.label}
              </option>
            ))}
          </select>
          <Button type="button" size="sm" onClick={submitDraft} disabled={!draftContent.trim()}>
            <Plus className="size-4" />
            添加记忆
          </Button>
        </div>
      </div>

      {groups.length === 0 ? (
        <EmptyState text={query ? "没有匹配的记忆" : "暂无长期记忆"} />
      ) : (
        <div className="flex flex-col gap-5">
          {groups.map((group) => (
            <section key={group.kind} className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2 px-1">
                <span className="text-xs font-medium text-foreground">{group.label}</span>
                <span className="text-xs text-muted-foreground">{group.items.length}</span>
                <div className="h-px flex-1 bg-border" />
              </div>
              <div className="overflow-hidden rounded-lg border bg-card">
                {group.items.map((memory, index) => {
                  const isEditing = editingId === memory.id;
                  return (
                    <div
                      key={memory.id}
                      className={`group flex items-start gap-2 px-3 py-2 transition-colors hover:bg-muted/40 ${
                        index > 0 ? "border-t" : ""
                      }`}
                    >
                      {isEditing ? (
                        <div className="flex w-full flex-col gap-2">
                          <textarea
                            value={editContent}
                            onChange={(event) => setEditContent(event.target.value)}
                            rows={2}
                            className="w-full resize-none rounded-md border border-input bg-background px-2 py-1.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
                          />
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              type="button"
                              size="icon-xs"
                              variant="ghost"
                              title="取消"
                              onClick={() => setEditingId(null)}
                            >
                              <X className="size-3.5" />
                            </Button>
                            <Button
                              type="button"
                              size="icon-xs"
                              variant="ghost"
                              title="保存"
                              onClick={() => {
                                const content = editContent.trim();
                                if (content) {
                                  onEditMemory(memory, content, memory.kind);
                                }
                                setEditingId(null);
                              }}
                            >
                              <Check className="size-3.5" />
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <p
                            className="line-clamp-2 flex-1 whitespace-pre-wrap text-sm leading-relaxed text-foreground"
                            title={memory.content}
                          >
                            {displayMemoryContent(memory.content)}
                          </p>
                          <div className="flex items-center gap-0.5 pt-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                            <Button
                              type="button"
                              size="icon-xs"
                              variant="ghost"
                              title="编辑"
                              onClick={() => {
                                setEditingId(memory.id);
                                setEditContent(displayMemoryContent(memory.content));
                              }}
                            >
                              <Pencil className="size-3.5" />
                            </Button>
                            <Button
                              type="button"
                              size="icon-xs"
                              variant="ghost"
                              title="删除"
                              className="text-muted-foreground hover:text-destructive"
                              onClick={() => onDeleteMemory(memory)}
                            >
                              <Trash2 className="size-3.5" />
                            </Button>
                          </div>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
