"use client";

import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import {
  Blocks,
  Brain,
  Check,
  ChevronDown,
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
  onGroupSkills: (input: {
    name: string;
    description: string;
    content: string;
    members: string[];
  }) => Promise<void> | void;
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
  onToggleSkill,
  onPinSkill,
  onDeleteSkill,
  onGroupSkills,
}: { query: string } & DirectoryModalProps) {
  const q = query.trim().toLowerCase();
  const matches = (skill: SkillRecord) =>
    !q ||
    skill.name.toLowerCase().includes(q) ||
    (skill.description ?? "").toLowerCase().includes(q);

  // 组合模式:勾选若干顶层 skill,合成一个索引 skill,避免一堆平行 skill 占满模型注意力。
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [groupDialogOpen, setGroupDialogOpen] = useState(false);

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

  // 只有"顶层、非受保护"的 skill 能被选进新组合。
  const selectableRoots = useMemo(
    () => visible.map((group) => group.root).filter((root) => !root.protected),
    [visible],
  );

  function toggleSelected(name: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  }

  function exitSelecting() {
    setSelecting(false);
    setSelected(new Set());
  }

  return (
    <div className="flex flex-col gap-5">
      {selectableRoots.length >= 2 ? (
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">
            {selecting
              ? `已选 ${selected.size} 个 · 合成一个索引 Skill 收拢它们`
              : "多个相关 Skill 可合成一个索引 Skill，减少对模型的干扰"}
          </p>
          <div className="flex items-center gap-2">
            {selecting ? (
              <>
                <Button type="button" size="sm" variant="ghost" onClick={exitSelecting}>
                  取消
                </Button>
                <Button
                  type="button"
                  size="sm"
                  disabled={selected.size < 2}
                  onClick={() => setGroupDialogOpen(true)}
                >
                  组合（{selected.size}）
                </Button>
              </>
            ) : (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setSelecting(true)}
              >
                <Blocks className="size-4" />
                组合 Skills
              </Button>
            )}
          </div>
        </div>
      ) : null}

      {visible.length > 0 ? (
        <section className="flex flex-col gap-3">
          <SectionHeading title="已安装 Skills" description="当前可用的本地 Skills。" />
          <CardGrid>
            {visible.map((group) => (
              <SkillGroupCard
                key={group.root.name}
                group={group}
                query={q}
                selecting={selecting}
                selected={selected.has(group.root.name)}
                selectable={!group.root.protected}
                onToggleSelected={() => toggleSelected(group.root.name)}
                onToggleSkill={onToggleSkill}
                onPinSkill={onPinSkill}
                onDeleteSkill={onDeleteSkill}
              />
            ))}
          </CardGrid>
        </section>
      ) : (
        <EmptyState text={q ? "没有匹配的已安装 Skill" : "暂无已安装 Skill，可用右上角安装或上传"} />
      )}

      {groupDialogOpen ? (
        <GroupSkillsDialog
          memberNames={[...selected]}
          onClose={() => setGroupDialogOpen(false)}
          onSubmit={async (input) => {
            await onGroupSkills({ ...input, members: [...selected] });
            setGroupDialogOpen(false);
            exitSelecting();
          }}
        />
      ) : null}
    </div>
  );
}

function GroupSkillsDialog({
  memberNames,
  onClose,
  onSubmit,
}: {
  memberNames: string[];
  onClose: () => void;
  onSubmit: (input: {
    name: string;
    description: string;
    content: string;
  }) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const nameValid = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(name);
  const canSubmit = nameValid && description.trim().length > 0 && !submitting;

  async function submit() {
    if (!canSubmit) {
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit({ name: name.trim(), description: description.trim(), content });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog.Root open onOpenChange={(next) => (!next ? onClose() : undefined)}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-[60] bg-black/45" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-[60] flex w-[min(92vw,34rem)] -translate-x-1/2 -translate-y-1/2 flex-col gap-4 rounded-xl border bg-background p-6 text-foreground shadow-2xl">
          <Dialog.Title className="text-lg font-semibold">合成索引 Skill</Dialog.Title>
          <p className="text-sm text-muted-foreground">
            把选中的 {memberNames.length} 个 Skill 收进一个索引 Skill；系统提示词只会展示这一个，
            模型按需读取成员内容。
          </p>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium">名称</label>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如 nature-suite（字母数字/._-）"
              className="h-10 rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
            />
            {name && !nameValid ? (
              <span className="text-xs text-destructive">
                名称只能包含字母、数字、点、下划线或连字符
              </span>
            ) : null}
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium">描述（模型据此判断何时打开）</label>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={2}
              placeholder="例如 Nature 论文写作全流程套件：检索、写作、引用、润色、图表"
              className="resize-none rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium">正文（可选，索引 Skill 的指引内容）</label>
            <textarea
              value={content}
              onChange={(event) => setContent(event.target.value)}
              rows={3}
              placeholder="留空则自动生成一个最简索引。成员清单会自动附在末尾。"
              className="resize-none rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClose} disabled={submitting}>
              取消
            </Button>
            <Button type="button" onClick={() => void submit()} disabled={!canSubmit}>
              {submitting ? "合成中…" : "合成"}
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
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
  selecting,
  selected,
  selectable,
  onToggleSelected,
  onToggleSkill,
  onPinSkill,
  onDeleteSkill,
}: {
  group: SkillGroup;
  query: string;
  selecting: boolean;
  selected: boolean;
  selectable: boolean;
  onToggleSelected: () => void;
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

  const selectMode = selecting && selectable;

  return (
    <div
      className={cn(
        "slotflow-hover-lift group flex min-h-32 flex-col gap-2 rounded-lg border bg-card/95 p-4 transition-colors hover:border-foreground/20",
        selectMode && "cursor-pointer",
        selected && "border-primary ring-1 ring-primary",
      )}
      onClick={selectMode ? onToggleSelected : undefined}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {selectMode ? (
            <span
              className={cn(
                "grid size-4 shrink-0 place-items-center rounded border",
                selected ? "border-primary bg-primary text-primary-foreground" : "border-input",
              )}
            >
              {selected ? <Check className="size-3" /> : null}
            </span>
          ) : (
            <Blocks className="size-4 shrink-0 text-primary" />
          )}
          <span className="truncate font-medium">{group.root.name}</span>
          {group.root.pinned ? <Pin className="size-3.5 shrink-0 text-amber-500" /> : null}
        </div>
        {selecting ? null : (
          <ToggleSwitch
            enabled={group.root.enabled}
            onChange={(next) => onToggleSkill(group.root, next)}
          />
        )}
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
          className="slotflow-hover-lift group flex min-h-32 flex-col gap-2 rounded-lg border bg-card/95 p-4 transition-colors hover:border-foreground/20"
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
            <CardChip>
              {server.stateful
                ? "内置有状态"
                : server.source === "environment"
                  ? "环境配置"
                  : "用户添加"}
            </CardChip>
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
      <div className="slotflow-rise-in flex flex-col gap-2 rounded-lg border bg-muted/20 p-3">
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
              <div className="slotflow-rise-in overflow-hidden rounded-lg border bg-card/95">
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
