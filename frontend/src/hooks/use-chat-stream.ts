"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  type ChatMode,
  type ChatStreamRequest,
  type ThreadRecord,
  type WorkspaceEntryRecord,
  createThread,
  listThreadMessages,
  streamThreadRun,
} from "@/lib/chat-stream";
import {
  type ChatTodo,
  type ChatUiMessage,
  formatClarificationContent,
  latestAssistantContent,
  latestAssistantReasoningContent,
  latestDiscoveredArtifacts,
  latestTodos,
  makeId,
  mergeAssistantContent,
  mergeReasoningContent,
  mergeWorkspaceEntries,
  messageRecordToUiMessage,
  parseClarificationRequest,
  parseToolStatus,
  parseTodos,
} from "./use-chat-stream-helpers";

export type {
  ChatTodo,
  ChatTodoStatus,
  ChatUiMessage,
  ChatToolStatus,
  ChatUiMessageRole,
  ChatUiMessageStatus,
} from "./use-chat-stream-helpers";

type AssistantDeltaChannel = "content" | "reasoning";
type PendingAssistantDeltas = Record<AssistantDeltaChannel, string>;

export type UseChatStreamOptions = {
  defaultThreadTitle?: string;
  defaultModelName?: string;
  defaultMode?: ChatMode;
  defaultAgentName?: string;
  defaultMetadata?: Record<string, unknown>;
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
const streamingDeltaFlushMs = 80;

function todoContentKey(todos: ChatTodo[]): string {
  return JSON.stringify(todos.map((todo) => todo.content.trim()));
}

export function useChatStream(options: UseChatStreamOptions = {}) {
  const {
    defaultThreadTitle = fallbackThreadTitle,
    defaultModelName = fallbackModelName,
    defaultMode = fallbackMode,
    defaultAgentName = fallbackAgentName,
    defaultMetadata = {},
  } = options;

  const [thread, setThread] = useState<ThreadRecord | null>(null);
  const [messages, setMessages] = useState<ChatUiMessage[]>([]);
  const [todos, setTodos] = useState<ChatTodo[]>([]);
  const [todoListKey, setTodoListKey] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const isStreamingRef = useRef(false);
  const todoSignatureRef = useRef("[]");
  const todoListKeyRef = useRef<string | null>(null);
  const pendingAssistantDeltasRef = useRef(new Map<string, PendingAssistantDeltas>());
  const pendingFlushTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
      if (pendingFlushTimerRef.current !== null) {
        window.clearTimeout(pendingFlushTimerRef.current);
      }
      pendingAssistantDeltasRef.current.clear();
    };
  }, []);

  const replaceTodos = useCallback((nextTodos: ChatTodo[]) => {
    if (nextTodos.length === 0 && todoSignatureRef.current === "[]") {
      return;
    }
    const nextSignature = JSON.stringify(nextTodos);
    if (nextSignature === todoSignatureRef.current) {
      return;
    }
    const nextListKey = nextTodos.length > 0 ? todoContentKey(nextTodos) : null;
    todoSignatureRef.current = nextSignature;
    if (nextListKey !== todoListKeyRef.current) {
      todoListKeyRef.current = nextListKey;
      setTodoListKey(nextListKey);
    }
    setTodos(nextTodos);
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

  const flushPendingAssistantDeltas = useCallback(() => {
    if (pendingFlushTimerRef.current !== null) {
      window.clearTimeout(pendingFlushTimerRef.current);
      pendingFlushTimerRef.current = null;
    }

    const pendingDeltas = pendingAssistantDeltasRef.current;
    if (pendingDeltas.size === 0) {
      return;
    }

    const deltasByMessage = new Map(pendingDeltas);
    pendingDeltas.clear();
    setMessages((current) =>
      current.map((message) => {
        const deltas = deltasByMessage.get(message.id);
        if (!deltas) {
          return message;
        }

        let nextMessage = message;
        if (deltas.reasoning) {
          nextMessage = {
            ...nextMessage,
            compressionStarted: false,
            thinkingStarted: true,
            reasoningContent: `${nextMessage.reasoningContent ?? ""}${deltas.reasoning}`,
          };
        }
        if (deltas.content) {
          nextMessage = {
            ...nextMessage,
            compressionStarted: false,
            content: nextMessage.content + deltas.content,
          };
        }
        return nextMessage;
      }),
    );
  }, []);

  const scheduleAssistantDeltaFlush = useCallback(() => {
    if (pendingFlushTimerRef.current !== null) {
      return;
    }
    pendingFlushTimerRef.current = window.setTimeout(() => {
      pendingFlushTimerRef.current = null;
      flushPendingAssistantDeltas();
    }, streamingDeltaFlushMs);
  }, [flushPendingAssistantDeltas]);

  const appendAssistantDelta = useCallback((
    messageId: string,
    channel: AssistantDeltaChannel,
    delta: string,
  ) => {
    if (!delta) {
      return;
    }
    const pending = pendingAssistantDeltasRef.current.get(messageId) ?? {
      content: "",
      reasoning: "",
    };
    pending[channel] += delta;
    pendingAssistantDeltasRef.current.set(messageId, pending);
    scheduleAssistantDeltaFlush();
  }, [scheduleAssistantDeltaFlush]);

  const replaceAssistantContent = useCallback((
    messageId: string,
    channel: "content" | "reasoning",
    content: string,
  ) => {
    updateAssistantMessage(messageId, (message) => {
      if (channel === "reasoning") {
        return {
          ...message,
          compressionStarted: false,
          thinkingStarted: true,
          reasoningContent: mergeReasoningContent(message.reasoningContent, content),
        };
      }
      return {
        ...message,
        compressionStarted: false,
        content: mergeAssistantContent(message.content, content),
      };
    });
  }, [updateAssistantMessage]);

  const patchAssistant = useCallback(
    (messageId: string, patch: Partial<ChatUiMessage>) => {
      updateAssistantMessage(messageId, (message) => ({ ...message, ...patch }));
    },
    [updateAssistantMessage],
  );

  const resetThread = useCallback((): boolean => {
    if (isStreamingRef.current) {
      return false;
    }

    setThread(null);
    setMessages([]);
    setTodos([]);
    setTodoListKey(null);
    todoSignatureRef.current = "[]";
    todoListKeyRef.current = null;
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
      setTodoListKey(null);
      todoSignatureRef.current = "[]";
      todoListKeyRef.current = null;
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
      const effectiveThinkingEnabled = overrides.thinking_enabled ?? effectiveMode !== "flash";
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
        thinkingStarted: effectiveThinkingEnabled,
        status: "streaming",
      };

      abortControllerRef.current = controller;
      isStreamingRef.current = true;
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
          provider: overrides.provider,
          mode: effectiveMode,
          thinking_enabled: effectiveThinkingEnabled,
          agent_name: overrides.agent_name ?? defaultAgentName,
          files: overrides.files ?? [],
          metadata: messageMetadata,
          reuse_user_message_id: reusedUserMessageId,
        };

        let discoveredArtifacts: WorkspaceEntryRecord[] = [];
        let toolStatusVisible = false;
        for await (const streamEvent of streamThreadRun(activeThread.id, body, {
          signal: controller.signal,
        })) {
          if (streamEvent.event === "run.prepared") {
            const runId = streamEvent.data.run_id;
            if (typeof runId === "string") {
              patchAssistant(assistantMessageId, { runId });
            }
          }

          if (streamEvent.event === "context.compressing") {
            patchAssistant(assistantMessageId, { compressionStarted: true });
          }

          if (streamEvent.event === "message.delta") {
            const delta = streamEvent.data.delta;
            if (typeof delta === "string") {
              const channel = streamEvent.data.channel;
              if (channel === "reasoning") {
                patchAssistant(assistantMessageId, { thinkingStarted: true });
              } else if (toolStatusVisible) {
                // 工具已执行完、模型恢复输出正文:清掉状态芯片,别让"running"一直挂着骗人。
                toolStatusVisible = false;
                patchAssistant(assistantMessageId, { toolStatus: undefined });
              }
              appendAssistantDelta(
                assistantMessageId,
                channel === "reasoning" ? "reasoning" : "content",
                delta,
              );
            }
          }

          if (streamEvent.event === "tool.status") {
            const toolStatus = parseToolStatus(streamEvent.data);
            if (toolStatus) {
              toolStatusVisible = true;
              patchAssistant(assistantMessageId, {
                compressionStarted: false,
                toolStatus,
              });
            }
          }

          if (streamEvent.event === "clarification.requested") {
            flushPendingAssistantDeltas();
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
            flushPendingAssistantDeltas();
            const snapshotTodos = latestTodos(streamEvent);
            if (snapshotTodos !== null) {
              replaceTodos(snapshotTodos);
            }
            const content = latestAssistantContent(streamEvent);
            if (content) {
              replaceAssistantContent(assistantMessageId, "content", content);
            }
            const reasoningContent = latestAssistantReasoningContent(streamEvent);
            if (reasoningContent) {
              replaceAssistantContent(assistantMessageId, "reasoning", reasoningContent);
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
            flushPendingAssistantDeltas();
            failed = true;
            const message = String(streamEvent.data.message ?? "agent stream failed");
            setError(message);
            patchAssistant(assistantMessageId, {
              compressionStarted: false,
              toolStatus: undefined,
              status: "error",
            });
          }
        }

        flushPendingAssistantDeltas();
        if (controller.signal.aborted) {
          patchAssistant(assistantMessageId, {
            compressionStarted: false,
            toolStatus: undefined,
            status: "cancelled",
          });
        } else if (!failed) {
          patchAssistant(assistantMessageId, {
            compressionStarted: false,
            toolStatus: undefined,
            status: "done",
          });
        }

        return { accepted: true, thread: activeThread, artifacts: discoveredArtifacts };
      } catch (caught) {
        flushPendingAssistantDeltas();
        if (controller.signal.aborted) {
          patchAssistant(assistantMessageId, {
            compressionStarted: false,
            toolStatus: undefined,
            status: "cancelled",
          });
          return { accepted, thread: activeThread, artifacts: [] };
        }

        const message = caught instanceof Error ? caught.message : "stream failed";
        setError(message);
        patchAssistant(assistantMessageId, {
          compressionStarted: false,
          toolStatus: undefined,
          status: "error",
        });
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
      defaultAgentName,
      defaultMetadata,
      defaultMode,
      defaultModelName,
      defaultThreadTitle,
      flushPendingAssistantDeltas,
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
    todoListKey,
    isStreaming,
    error,
    sendMessage,
    cancelStream,
    resetThread,
    removeMessage,
    loadThread,
    clearError: () => setError(null),
  };
}
