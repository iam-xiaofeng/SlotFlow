"use client";

import { startTransition, useCallback, useEffect, useRef, useState } from "react";

import {
  type ChatMode,
  type ClarificationRequestRecord,
  type ChatStreamEvent,
  type ChatStreamRequest,
  type MessageRecord,
  type ThreadRecord,
  type WorkspaceEntryRecord,
  createThread,
  listThreadMessages,
  streamThreadRun,
} from "@/lib/chat-stream";

export type ChatUiMessageRole = "user" | "assistant" | "system" | "tool";
export type ChatUiMessageStatus = "streaming" | "done" | "error" | "cancelled";

export type ChatUiMessage = {
  id: string;
  role: ChatUiMessageRole;
  content: string;
  reasoningContent?: string;
  thinkingStarted?: boolean;
  status: ChatUiMessageStatus;
  runId?: string;
  createdAt?: string;
  metadata?: Record<string, unknown>;
};

export type ChatTodoStatus = "pending" | "in_progress" | "completed";

export type ChatTodo = {
  content: string;
  status: ChatTodoStatus;
};

export type UseChatStreamOptions = {
  defaultThreadTitle?: string;
  defaultModelName?: string;
  defaultMode?: ChatMode;
  defaultAgentName?: string;
  defaultMetadata?: Record<string, unknown>;
  maxEventLogItems?: number;
};

export type SendChatMessageOptions = Omit<Partial<ChatStreamRequest>, "message"> & {
  threadTitle?: string;
};

export type SendChatMessageResult = {
  accepted: boolean;
  thread: ThreadRecord | null;
  artifacts: WorkspaceEntryRecord[];
};

const fallbackThreadTitle = "SlotFlow chat";
const fallbackModelName = "deepseek-v4-pro";
const fallbackMode: ChatMode = "pro";
const fallbackAgentName = "default";
const fallbackMaxEventLogItems = 12;

export function useChatStream(options: UseChatStreamOptions = {}) {
  const {
    defaultThreadTitle = fallbackThreadTitle,
    defaultModelName = fallbackModelName,
    defaultMode = fallbackMode,
    defaultAgentName = fallbackAgentName,
    defaultMetadata = {},
    maxEventLogItems = fallbackMaxEventLogItems,
  } = options;

  const [thread, setThread] = useState<ThreadRecord | null>(null);
  const [messages, setMessages] = useState<ChatUiMessage[]>([]);
  const [todos, setTodos] = useState<ChatTodo[]>([]);
  const [todoRevision, setTodoRevision] = useState(0);
  const [events, setEvents] = useState<ChatStreamEvent[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const isStreamingRef = useRef(false);
  const hasTodoListForCurrentRunRef = useRef(false);

  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  const appendEvent = useCallback(
    (event: ChatStreamEvent) => {
      startTransition(() => {
        setEvents((current) => [
          ...current.slice(Math.max(0, current.length - maxEventLogItems + 1)),
          event,
        ]);
      });
    },
    [maxEventLogItems],
  );

  const replaceTodos = useCallback((nextTodos: ChatTodo[]) => {
    if (nextTodos.length === 0 && !hasTodoListForCurrentRunRef.current) {
      return;
    }
    setTodos(nextTodos);
    if (nextTodos.length > 0 && !hasTodoListForCurrentRunRef.current) {
      hasTodoListForCurrentRunRef.current = true;
      setTodoRevision((current) => current + 1);
    }
  }, []);

  const updateAssistantMessage = useCallback((
    messageId: string,
    update: (message: ChatUiMessage) => ChatUiMessage,
  ) => {
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId ? update(message) : message,
      ),
    );
  }, []);

  const appendAssistantDelta = useCallback((
    messageId: string,
    channel: "content" | "reasoning",
    delta: string,
  ) => {
    updateAssistantMessage(messageId, (message) =>
      channel === "reasoning"
        ? { ...message, reasoningContent: `${message.reasoningContent ?? ""}${delta}` }
        : { ...message, content: message.content + delta },
    );
  }, [updateAssistantMessage]);

  const replaceAssistantContent = useCallback((
    messageId: string,
    channel: "content" | "reasoning",
    content: string,
  ) => {
    updateAssistantMessage(messageId, (message) =>
      channel === "reasoning"
        ? { ...message, reasoningContent: content }
        : { ...message, content },
    );
  }, [updateAssistantMessage]);

  const patchAssistant = useCallback(
    (messageId: string, patch: Partial<ChatUiMessage>) => {
      updateAssistantMessage(messageId, (message) => ({ ...message, ...patch }));
    },
    [updateAssistantMessage],
  );

  const startNewThread = useCallback(
    async (title = defaultThreadTitle): Promise<ThreadRecord | null> => {
      if (isStreamingRef.current) {
        return thread;
      }

      setError(null);
      try {
        const nextThread = await createThread(title);
        setThread(nextThread);
        setMessages([]);
        setTodos([]);
        setTodoRevision(0);
        hasTodoListForCurrentRunRef.current = false;
        setEvents([]);
        return nextThread;
      } catch (caught) {
        const message = caught instanceof Error ? caught.message : "create thread failed";
        setError(message);
        return null;
      }
    },
    [defaultThreadTitle, thread],
  );

  const resetThread = useCallback((): boolean => {
    if (isStreamingRef.current) {
      return false;
    }

    setThread(null);
    setMessages([]);
    setTodos([]);
    setTodoRevision(0);
    hasTodoListForCurrentRunRef.current = false;
    setEvents([]);
    setError(null);
    return true;
  }, []);

  const removeMessage = useCallback((messageId: string) => {
    setMessages((current) => current.filter((message) => message.id !== messageId));
  }, []);

  const loadThread = useCallback(async (targetThread: ThreadRecord): Promise<boolean> => {
    if (isStreamingRef.current) {
      return false;
    }

    setError(null);
    try {
      const storedMessages = await listThreadMessages(targetThread.id);
      setThread(targetThread);
      setMessages(storedMessages.map(messageRecordToUiMessage));
      setTodos([]);
      setTodoRevision(0);
      hasTodoListForCurrentRunRef.current = false;
      setEvents([]);
      return true;
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "load thread failed";
      setError(message);
      return false;
    }
  }, []);

  const sendMessage = useCallback(
    async (
      rawMessage: string,
      overrides: SendChatMessageOptions = {},
    ): Promise<SendChatMessageResult> => {
      const text = rawMessage.trim();
      if (!text || isStreamingRef.current) {
        return { accepted: false, thread, artifacts: [] };
      }

      const controller = new AbortController();
      const messageMetadata = {
        ...defaultMetadata,
        ...(overrides.metadata ?? {}),
      };
      const effectiveMode = overrides.mode ?? defaultMode;
      const reusedUserMessageId = overrides.reuse_user_message_id ?? null;
      const userMessage: ChatUiMessage = {
        id: reusedUserMessageId ?? makeId("user"),
        role: "user",
        content: text,
        status: "done",
        metadata: messageMetadata,
      };
      const assistantMessageId = makeId("assistant");
      const assistantMessage: ChatUiMessage = {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        thinkingStarted: effectiveMode !== "flash",
        status: "streaming",
      };

      abortControllerRef.current = controller;
      isStreamingRef.current = true;
      hasTodoListForCurrentRunRef.current = false;
      setIsStreaming(true);
      setError(null);

      let activeThread: ThreadRecord | null = thread;
      let accepted = false;
      try {
        if (!activeThread) {
          activeThread = await createThread(overrides.threadTitle ?? defaultThreadTitle, {
            signal: controller.signal,
          });
          setThread(activeThread);
        }

        setMessages((current) => {
          if (!reusedUserMessageId) {
            return [...current, userMessage, assistantMessage];
          }

          const targetIndex = current.findIndex(
            (message) => message.id === reusedUserMessageId && message.role === "user",
          );
          if (targetIndex < 0) {
            return [...current, userMessage, assistantMessage];
          }

          const targetMessage = current[targetIndex];
          return [
            ...current.slice(0, targetIndex),
            {
              ...targetMessage,
              content: text,
              metadata: {
                ...targetMessage.metadata,
                request_metadata: messageMetadata,
              },
            },
            assistantMessage,
          ];
        });
        accepted = true;

        let failed = false;
        const body: ChatStreamRequest = {
          message: text,
          model_name: overrides.model_name ?? defaultModelName,
          mode: effectiveMode,
          agent_name: overrides.agent_name ?? defaultAgentName,
          files: overrides.files ?? [],
          metadata: messageMetadata,
          reuse_user_message_id: reusedUserMessageId,
        };

        let discoveredArtifacts: WorkspaceEntryRecord[] = [];
        for await (const streamEvent of streamThreadRun(activeThread.id, body, {
          signal: controller.signal,
        })) {
          appendEvent(streamEvent);

          if (streamEvent.event === "run.prepared") {
            const runId = streamEvent.data.run_id;
            if (typeof runId === "string") {
              patchAssistant(assistantMessageId, { runId });
            }
          }

          if (streamEvent.event === "message.delta") {
            const delta = streamEvent.data.delta;
            if (typeof delta === "string") {
              const channel = streamEvent.data.channel;
              if (channel === "reasoning") {
                patchAssistant(assistantMessageId, { thinkingStarted: true });
              }
              appendAssistantDelta(
                assistantMessageId,
                channel === "reasoning" ? "reasoning" : "content",
                delta,
              );
            }
          }

          if (streamEvent.event === "clarification.requested") {
            const clarification = parseClarificationRequest(streamEvent.data);
            if (clarification) {
              patchAssistant(assistantMessageId, {
                content: formatClarificationContent(clarification),
                metadata: { clarification },
              });
            }
          }

          if (streamEvent.event === "todo.updated") {
            replaceTodos(parseTodos(streamEvent.data.todos));
          }

          if (streamEvent.event === "state.snapshot") {
            const content = latestAssistantContent(streamEvent);
            if (content) {
              replaceAssistantContent(assistantMessageId, "content", content);
            }
            const reasoningContent = latestAssistantReasoningContent(streamEvent);
            if (reasoningContent) {
              replaceAssistantContent(assistantMessageId, "reasoning", reasoningContent);
            }
            const nextTodos = latestTodos(streamEvent);
            if (nextTodos) {
              replaceTodos(nextTodos);
            }
            const nextArtifacts = latestDiscoveredArtifacts(streamEvent);
            if (nextArtifacts.length > 0) {
              discoveredArtifacts = mergeWorkspaceEntries(
                discoveredArtifacts,
                nextArtifacts,
              );
            }
          }

          if (streamEvent.event === "run.error") {
            failed = true;
            const message = String(streamEvent.data.message ?? "agent stream failed");
            setError(message);
            patchAssistant(assistantMessageId, { status: "error" });
          }
        }

        if (controller.signal.aborted) {
          patchAssistant(assistantMessageId, { status: "cancelled" });
        } else if (!failed) {
          patchAssistant(assistantMessageId, { status: "done" });
        }

        return { accepted: true, thread: activeThread, artifacts: discoveredArtifacts };
      } catch (caught) {
        if (controller.signal.aborted) {
          patchAssistant(assistantMessageId, { status: "cancelled" });
          return { accepted, thread: activeThread, artifacts: [] };
        }

        const message = caught instanceof Error ? caught.message : "stream failed";
        setError(message);
        patchAssistant(assistantMessageId, { status: "error" });
        return { accepted, thread: activeThread, artifacts: [] };
      } finally {
        if (abortControllerRef.current === controller) {
          abortControllerRef.current = null;
        }
        isStreamingRef.current = false;
        setIsStreaming(false);
      }
    },
    [
      appendAssistantDelta,
      appendEvent,
      defaultAgentName,
      defaultMetadata,
      defaultMode,
      defaultModelName,
      defaultThreadTitle,
      patchAssistant,
      replaceAssistantContent,
      replaceTodos,
      thread,
    ],
  );

  const cancelStream = useCallback(() => {
    abortControllerRef.current?.abort();
  }, []);

  return {
    thread,
    messages,
    todos,
    todoRevision,
    events,
    isStreaming,
    error,
    sendMessage,
    startNewThread,
    cancelStream,
    resetThread,
    removeMessage,
    loadThread,
    clearError: () => setError(null),
  };
}

function messageRecordToUiMessage(record: MessageRecord): ChatUiMessage {
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

function latestAssistantContent(event: ChatStreamEvent): string | null {
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
      if ((role === "assistant" || role === "ai") && typeof content === "string") {
        return content;
      }
    }
  }

  return null;
}

function latestAssistantReasoningContent(event: ChatStreamEvent): string | null {
  const messages = event.data.messages;
  if (!Array.isArray(messages)) {
    return null;
  }

  for (const message of [...messages].reverse()) {
    if (
      typeof message === "object" &&
      message !== null &&
      "role" in message &&
      "reasoning_content" in message
    ) {
      const role = message.role;
      const content = message.reasoning_content;
      if ((role === "assistant" || role === "ai") && typeof content === "string") {
        return content;
      }
    }
  }

  return null;
}

function latestTodos(event: ChatStreamEvent): ChatTodo[] | null {
  const state = event.data.state;
  if (typeof state !== "object" || state === null || !("todos" in state)) {
    return null;
  }
  return parseTodos(state.todos);
}

function parseTodos(value: unknown): ChatTodo[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.flatMap((item) => {
    if (
      typeof item === "object" &&
      item !== null &&
      "content" in item &&
      "status" in item &&
      typeof item.content === "string"
    ) {
      const status = parseTodoStatus(item.status);
      return [{ content: item.content, status }];
    }
    return [];
  });
}

function parseTodoStatus(value: unknown): ChatTodoStatus {
  if (value === "completed" || value === "in_progress" || value === "pending") {
    return value;
  }
  return "pending";
}

function latestDiscoveredArtifacts(event: ChatStreamEvent): WorkspaceEntryRecord[] {
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

function mergeWorkspaceEntries(
  left: WorkspaceEntryRecord[],
  right: WorkspaceEntryRecord[],
): WorkspaceEntryRecord[] {
  const merged = new Map(left.map((entry) => [entry.path, entry]));
  for (const entry of right) {
    merged.set(entry.path, entry);
  }
  return [...merged.values()];
}

function parseClarificationRequest(
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

function formatClarificationContent(clarification: ClarificationRequestRecord): string {
  return [
    clarification.context,
    clarification.question,
    ...clarification.options.map((option) => `${option.id}. ${option.label}`),
  ]
    .filter((line): line is string => Boolean(line?.trim()))
    .join("\n");
}

function makeId(prefix: string) {
  return `${prefix}_${crypto.randomUUID()}`;
}
