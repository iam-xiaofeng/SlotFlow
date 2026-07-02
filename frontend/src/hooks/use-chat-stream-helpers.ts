import {
  type ChatStreamEvent,
  type ClarificationRequestRecord,
  type MessageRecord,
  type WorkspaceEntryRecord,
} from "@/lib/chat-stream";

export type ChatUiMessageRole = "user" | "assistant" | "system" | "tool";
export type ChatUiMessageStatus = "streaming" | "done" | "error" | "cancelled";

export type ChatUiMessage = {
  id: string;
  role: ChatUiMessageRole;
  content: string;
  reasoningContent?: string;
  thinkingStarted?: boolean;
  compressionStarted?: boolean;
  toolStatus?: ChatToolStatus;
  status: ChatUiMessageStatus;
  runId?: string;
  createdAt?: string;
  metadata?: Record<string, unknown>;
};

export type ChatToolStatusPhase = "starting" | "running" | "completed" | "error";

export type ChatToolStatus = {
  toolName: string;
  phase: ChatToolStatusPhase;
  message: string;
  command?: string;
};

export type ChatTodoStatus = "pending" | "in_progress" | "completed";

export type ChatTodo = {
  content: string;
  status: ChatTodoStatus;
};

export function messageRecordToUiMessage(record: MessageRecord): ChatUiMessage {
  const reasoningContent = parseReasoningContent(record.metadata);
  const thinkingStarted = parseThinkingStarted(record.metadata, reasoningContent);
  return {
    id: record.id,
    role: record.role,
    content: record.content,
    reasoningContent,
    thinkingStarted,
    status: "done",
    runId: record.run_id ?? undefined,
    createdAt: record.created_at,
    metadata: record.metadata,
  };
}

function parseReasoningContent(metadata: Record<string, unknown> | undefined): string | undefined {
  const value = metadata?.reasoning_content;
  return typeof value === "string" && value.trim() ? value : undefined;
}

function parseThinkingStarted(
  metadata: Record<string, unknown> | undefined,
  reasoningContent: string | undefined,
): boolean {
  return Boolean(reasoningContent) || metadata?.thinking_enabled === true;
}

export function mergeReasoningContent(
  current: string | undefined,
  incoming: string,
): string {
  // LangGraph values snapshot may keep only the latest reasoning chunk; preserve
  // the longer live stream accumulated from message.delta.
  const currentText = current ?? "";
  if (!currentText.trim()) {
    return incoming;
  }
  if (!incoming.trim()) {
    return currentText;
  }

  return incoming.length > currentText.length ? incoming : currentText;
}

export function mergeAssistantContent(current: string, incoming: string): string {
  const currentText = current ?? "";
  if (!currentText.trim()) {
    return incoming;
  }
  if (!incoming.trim()) {
    return currentText;
  }
  if (incoming.startsWith(currentText)) {
    return incoming;
  }
  if (currentText.startsWith(incoming)) {
    return currentText;
  }

  // Values snapshots can lag behind the live message.delta stream or retain only the
  // final compact assistant message. Never let a shorter unrelated snapshot erase text
  // the user already watched stream in.
  return incoming.length > currentText.length ? incoming : currentText;
}

export function latestAssistantContent(event: ChatStreamEvent): string | null {
  const messages = event.data.messages;
  if (!Array.isArray(messages)) {
    return null;
  }

  for (const message of [...messages].reverse()) {
    if (
      typeof message === "object" &&
      message !== null &&
      "role" in message &&
      "content" in message
    ) {
      const role = message.role;
      const content = message.content;
      if (role === "assistant" || role === "ai") {
        if ((message as Record<string, unknown>).has_tool_calls === true) {
          continue;
        }
        if (typeof content === "string") {
          return content;
        }
      }
    }
  }

  return null;
}

export function latestAssistantReasoningContent(event: ChatStreamEvent): string | null {
  const messages = event.data.messages;
  if (!Array.isArray(messages)) {
    return null;
  }

  for (const message of [...messages].reverse()) {
    if (typeof message === "object" && message !== null && "role" in message) {
      const record = message as Record<string, unknown>;
      const role = record.role;
      if (role === "assistant" || role === "ai") {
        const reasoningContent = record.reasoning_content;
        if (typeof reasoningContent === "string" && reasoningContent.trim()) {
          return reasoningContent;
        }
      }
    }
  }

  return null;
}

export function latestTodos(event: ChatStreamEvent): ChatTodo[] | null {
  const state = event.data.state;
  if (typeof state !== "object" || state === null || !("todos" in state)) {
    return null;
  }
  return parseTodos(state.todos);
}

export function parseTodos(value: unknown): ChatTodo[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const todos: ChatTodo[] = [];
  for (const item of value) {
    if (typeof item !== "object" || item === null) {
      continue;
    }
    const record = item as Record<string, unknown>;
    const content = typeof record.content === "string" ? record.content : record.text;
    if (typeof content !== "string" || !content.trim()) {
      continue;
    }
    const status =
      record.status === "in_progress" ||
      record.status === "completed" ||
      record.status === "pending"
        ? record.status
        : "pending";
    todos.push({ content, status });
  }
  return todos;
}

export function parseToolStatus(
  data: Record<string, unknown>,
): ChatToolStatus | null {
  const toolName = data.tool_name;
  const phase = data.phase;
  const message = data.message;
  if (
    typeof toolName !== "string" ||
    typeof message !== "string" ||
    (phase !== "starting" &&
      phase !== "running" &&
      phase !== "completed" &&
      phase !== "error")
  ) {
    return null;
  }
  return {
    toolName,
    phase,
    message,
    command: typeof data.command === "string" ? data.command : undefined,
  };
}

export function latestDiscoveredArtifacts(event: ChatStreamEvent): WorkspaceEntryRecord[] {
  const state = event.data.state;
  if (typeof state !== "object" || state === null || !("slotflow" in state)) {
    return [];
  }

  const slotflow = state.slotflow;
  if (
    typeof slotflow !== "object" ||
    slotflow === null ||
    !("artifacts" in slotflow)
  ) {
    return [];
  }

  const artifacts = slotflow.artifacts;
  if (
    typeof artifacts !== "object" ||
    artifacts === null ||
    !("new_entries" in artifacts) ||
    !Array.isArray(artifacts.new_entries)
  ) {
    return [];
  }

  return artifacts.new_entries.flatMap((entry) => {
    if (
      typeof entry === "object" &&
      entry !== null &&
      "path" in entry &&
      "kind" in entry &&
      typeof entry.path === "string" &&
      (entry.kind === "file" || entry.kind === "directory")
    ) {
      return [
        {
          path: entry.path,
          kind: entry.kind,
          size_bytes:
            "size_bytes" in entry && typeof entry.size_bytes === "number"
              ? entry.size_bytes
              : null,
        },
      ];
    }
    return [];
  });
}

export function mergeWorkspaceEntries(
  left: WorkspaceEntryRecord[],
  right: WorkspaceEntryRecord[],
): WorkspaceEntryRecord[] {
  const merged = new Map(left.map((entry) => [entry.path, entry]));
  for (const entry of right) {
    merged.set(entry.path, entry);
  }
  return [...merged.values()];
}

export function parseClarificationRequest(
  data: Record<string, unknown>,
): ClarificationRequestRecord | null {
  if (
    data.type !== "clarification" ||
    typeof data.id !== "string" ||
    typeof data.question !== "string"
  ) {
    return null;
  }

  const options = Array.isArray(data.options)
    ? data.options.flatMap((item) => {
        if (
          typeof item === "object" &&
          item !== null &&
          "id" in item &&
          "label" in item &&
          typeof item.id === "string" &&
          typeof item.label === "string"
        ) {
          return [{ id: item.id, label: item.label }];
        }
        return [];
      })
    : [];

  return {
    type: "clarification",
    id: data.id,
    question: data.question,
    clarification_type:
      typeof data.clarification_type === "string"
        ? data.clarification_type
        : "missing_info",
    context: typeof data.context === "string" ? data.context : null,
    options,
    source: typeof data.source === "string" ? data.source : "slotflow_clarification",
    thread_id: typeof data.thread_id === "string" ? data.thread_id : null,
    run_id: typeof data.run_id === "string" ? data.run_id : null,
  };
}

export function formatClarificationContent(clarification: ClarificationRequestRecord): string {
  return [
    clarification.context,
    clarification.question,
    ...clarification.options.map((option) => `${option.id}. ${option.label}`),
  ]
    .filter((line): line is string => Boolean(line?.trim()))
    .join("\n");
}

export function makeId(prefix: string) {
  return `${prefix}_${crypto.randomUUID()}`;
}
