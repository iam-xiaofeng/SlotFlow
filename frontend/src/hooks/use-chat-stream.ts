"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

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
  settleRunningToolActivities,
  upsertToolActivity,
} from "./use-chat-stream-helpers";

export type {
  ChatTodo,
  ChatTodoStatus,
  ChatUiMessage,
  ChatToolActivity,
  ChatToolStatus,
  ChatUiMessageRole,
  ChatUiMessageStatus,
} from "./use-chat-stream-helpers";

type AssistantDeltaChannel = "content" | "reasoning";
type PendingAssistantDeltas = Record<AssistantDeltaChannel, string>;

/**
 * 线程级运行状态,驱动侧边栏徽标:
 * - streaming: 正在生成(转圈)
 * - attention: 在后台生成完毕、用户还没回来看(蓝点)
 * - needs_input: 等用户澄清/决策(闪烁蓝点)
 * - error: 后台运行报错(红点)
 */
export type ThreadRunStatus = "streaming" | "attention" | "needs_input" | "error";

type ThreadTodoState = { todos: ChatTodo[]; listKey: string | null; signature: string };

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
const fallbackModelName = "deepseek/deepseek-v4-pro";
const fallbackMode: ChatMode = "pro";
const fallbackAgentName = "default";
const streamingDeltaFlushMs = 80;

function todoContentKey(todos: ChatTodo[]): string {
  return JSON.stringify(todos.map((todo) => todo.content.trim()));
}

const emptyMessages: ChatUiMessage[] = [];
const emptyTodos: ChatTodo[] = [];

export function useChatStream(options: UseChatStreamOptions = {}) {
  const {
    defaultThreadTitle = fallbackThreadTitle,
    defaultModelName = fallbackModelName,
    defaultMode = fallbackMode,
    defaultAgentName = fallbackAgentName,
    defaultMetadata = {},
  } = options;

  const [thread, setThread] = useState<ThreadRecord | null>(null);
  const [messagesByThread, setMessagesByThread] = useState<
    Record<string, ChatUiMessage[]>
  >({});
  const [todosByThread, setTodosByThread] = useState<Record<string, ThreadTodoState>>(
    {},
  );
  const [errorsByThread, setErrorsByThread] = useState<Record<string, string | null>>(
    {},
  );
  const [runStates, setRunStates] = useState<Record<string, ThreadRunStatus>>({});

  const activeThreadIdRef = useRef<string | null>(null);
  activeThreadIdRef.current = thread?.id ?? null;
  const abortControllersRef = useRef(new Map<string, AbortController>());
  const streamingThreadsRef = useRef(new Set<string>());
  // 流式增量按 messageId 批量落地;messageId→threadId 索引让 flush 精确写回对应线程。
  const pendingAssistantDeltasRef = useRef(new Map<string, PendingAssistantDeltas>());
  const messageThreadIndexRef = useRef(new Map<string, string>());
  const pendingFlushTimerRef = useRef<number | null>(null);

  useEffect(() => {
    const controllers = abortControllersRef.current;
    const pendingDeltas = pendingAssistantDeltasRef.current;
    return () => {
      for (const controller of controllers.values()) {
        controller.abort();
      }
      if (pendingFlushTimerRef.current !== null) {
        window.clearTimeout(pendingFlushTimerRef.current);
      }
      pendingDeltas.clear();
    };
  }, []);

  const activeThreadId = thread?.id ?? null;
  const messages = activeThreadId
    ? messagesByThread[activeThreadId] ?? emptyMessages
    : emptyMessages;
  const activeTodoState = activeThreadId ? todosByThread[activeThreadId] : undefined;
  const todos = activeTodoState?.todos ?? emptyTodos;
  const todoListKey = activeTodoState?.listKey ?? null;
  const error = activeThreadId ? errorsByThread[activeThreadId] ?? null : null;
  const isStreaming = activeThreadId
    ? runStates[activeThreadId] === "streaming"
    : false;

  const setThreadError = useCallback((threadId: string | null, message: string | null) => {
    if (!threadId) {
      return;
    }
    setErrorsByThread((current) => ({ ...current, [threadId]: message }));
  }, []);

  const setRunState = useCallback(
    (threadId: string, status: ThreadRunStatus | null) => {
      setRunStates((current) => {
        if (status === null) {
          if (!(threadId in current)) {
            return current;
          }
          const next = { ...current };
          delete next[threadId];
          return next;
        }
        if (current[threadId] === status) {
          return current;
        }
        return { ...current, [threadId]: status };
      });
    },
    [],
  );

  const replaceTodos = useCallback((threadId: string, nextTodos: ChatTodo[]) => {
    setTodosByThread((current) => {
      const existing = current[threadId];
      if (nextTodos.length === 0 && (!existing || existing.signature === "[]")) {
        return current;
      }
      const nextSignature = JSON.stringify(nextTodos);
      if (existing && existing.signature === nextSignature) {
        return current;
      }
      const nextListKey = nextTodos.length > 0 ? todoContentKey(nextTodos) : null;
      return {
        ...current,
        [threadId]: {
          todos: nextTodos,
          listKey: nextListKey,
          signature: nextSignature,
        },
      };
    });
  }, []);

  const updateThreadMessages = useCallback(
    (threadId: string, update: (messages: ChatUiMessage[]) => ChatUiMessage[]) => {
      setMessagesByThread((current) => ({
        ...current,
        [threadId]: update(current[threadId] ?? []),
      }));
    },
    [],
  );

  const updateAssistantMessage = useCallback(
    (
      threadId: string,
      messageId: string,
      update: (message: ChatUiMessage) => ChatUiMessage,
    ) => {
      updateThreadMessages(threadId, (current) =>
        current.map((message) =>
          message.id === messageId ? update(message) : message,
        ),
      );
    },
    [updateThreadMessages],
  );

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
    const threadIds = new Set(
      [...deltasByMessage.keys()].flatMap((messageId) => {
        const threadId = messageThreadIndexRef.current.get(messageId);
        return threadId ? [threadId] : [];
      }),
    );
    if (threadIds.size === 0) {
      return;
    }

    setMessagesByThread((current) => {
      const next = { ...current };
      for (const threadId of threadIds) {
        next[threadId] = (next[threadId] ?? []).map((message) => {
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
        });
      }
      return next;
    });
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

  const appendAssistantDelta = useCallback(
    (messageId: string, channel: AssistantDeltaChannel, delta: string) => {
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
    },
    [scheduleAssistantDeltaFlush],
  );

  const replaceAssistantContent = useCallback(
    (
      threadId: string,
      messageId: string,
      channel: AssistantDeltaChannel,
      content: string,
    ) => {
      updateAssistantMessage(threadId, messageId, (message) => {
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
    },
    [updateAssistantMessage],
  );

  const resetThread = useCallback((): boolean => {
    // 只是切换视图到"新聊天";后台线程继续生成,状态徽标留在侧边栏。
    setThread(null);
    return true;
  }, []);

  const removeMessage = useCallback((messageId: string) => {
    setMessagesByThread((current) => {
      const next: Record<string, ChatUiMessage[]> = {};
      for (const [threadId, threadMessages] of Object.entries(current)) {
        next[threadId] = threadMessages.filter((message) => message.id !== messageId);
      }
      return next;
    });
  }, []);

  const loadThread = useCallback(
    async (targetThread: ThreadRecord): Promise<boolean> => {
      // 查看即消费提醒徽标;生成中的线程保留转圈。
      setRunStates((current) => {
        if (!(targetThread.id in current) || current[targetThread.id] === "streaming") {
          return current;
        }
        const next = { ...current };
        delete next[targetThread.id];
        return next;
      });

      // 正在流式的线程以内存态为准,拉服务器会丢掉尚未持久化的实时输出。
      if (streamingThreadsRef.current.has(targetThread.id)) {
        setThread(targetThread);
        return true;
      }

      try {
        const storedMessages = await listThreadMessages(targetThread.id);
        setThread(targetThread);
        setMessagesByThread((current) => ({
          ...current,
          [targetThread.id]: storedMessages.map(messageRecordToUiMessage),
        }));
        return true;
      } catch (caught) {
        const message = caught instanceof Error ? caught.message : "load thread failed";
        setThreadError(activeThreadIdRef.current ?? targetThread.id, message);
        return false;
      }
    },
    [setThreadError],
  );

  const sendMessage = useCallback(
    async (
      rawMessage: string,
      overrides: SendChatMessageOptions = {},
    ): Promise<SendChatMessageResult> => {
      const text = rawMessage.trim();
      const originThreadId = activeThreadIdRef.current;
      if (!text || (originThreadId && streamingThreadsRef.current.has(originThreadId))) {
        return { accepted: false, thread, artifacts: [] };
      }

      const controller = new AbortController();
      const messageMetadata = {
        ...defaultMetadata,
        ...(overrides.metadata ?? {}),
      };
      const effectiveMode = overrides.mode ?? defaultMode;
      const effectiveThinkingEnabled =
        overrides.thinking_enabled ?? effectiveMode !== "flash";
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

      let activeThread: ThreadRecord | null = thread;
      let accepted = false;
      let runThreadId: string | null = originThreadId;
      try {
        if (!activeThread) {
          activeThread = await createThread(overrides.threadTitle ?? defaultThreadTitle, {
            signal: controller.signal,
          });
          // 用户可能已切走;只有还停在"新聊天"视图时才跟进到新线程。
          if (activeThreadIdRef.current === null) {
            setThread(activeThread);
          }
        }
        runThreadId = activeThread.id;
        const threadId = activeThread.id;

        abortControllersRef.current.set(threadId, controller);
        streamingThreadsRef.current.add(threadId);
        setRunState(threadId, "streaming");
        setThreadError(threadId, null);
        messageThreadIndexRef.current.set(assistantMessageId, threadId);

        updateThreadMessages(threadId, (current) => {
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
        let sawClarification = false;
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
        // ReAct 一次 run 里有多段正文(正文→工具→正文…)。模型各段独立成文,直接拼接会把
        // "## 标题"黏进上一段行中,markdown 便不再当它是标题。以工具调用为段界,重启时补空行。
        let contentStarted = false;
        let reasoningStarted = false;
        let contentBreakPending = false;
        let reasoningBreakPending = false;
        for await (const streamEvent of streamThreadRun(threadId, body, {
          signal: controller.signal,
        })) {
          if (streamEvent.event === "run.prepared") {
            const runId = streamEvent.data.run_id;
            if (typeof runId === "string") {
              updateAssistantMessage(threadId, assistantMessageId, (message) => ({
                ...message,
                runId,
              }));
            }
          }

          if (streamEvent.event === "context.compressing") {
            updateAssistantMessage(threadId, assistantMessageId, (message) => ({
              ...message,
              compressionStarted: true,
            }));
          }

          if (streamEvent.event === "message.delta") {
            const delta = streamEvent.data.delta;
            if (typeof delta === "string") {
              const channel = streamEvent.data.channel;
              if (channel === "reasoning") {
                updateAssistantMessage(threadId, assistantMessageId, (message) => ({
                  ...message,
                  thinkingStarted: true,
                }));
                if (reasoningBreakPending && reasoningStarted) {
                  appendAssistantDelta(assistantMessageId, "reasoning", "\n\n");
                }
                reasoningBreakPending = false;
                reasoningStarted = true;
              } else {
                if (toolStatusVisible) {
                  // 工具已执行完、模型恢复输出正文:清掉状态芯片,时间线里在转的行收敛为完成。
                  toolStatusVisible = false;
                  updateAssistantMessage(threadId, assistantMessageId, (message) => ({
                    ...message,
                    toolStatus: undefined,
                    toolActivities: settleRunningToolActivities(
                      message.toolActivities ?? [],
                    ),
                  }));
                }
                if (contentBreakPending && contentStarted) {
                  appendAssistantDelta(assistantMessageId, "content", "\n\n");
                }
                contentBreakPending = false;
                contentStarted = true;
              }
              appendAssistantDelta(
                assistantMessageId,
                channel === "reasoning" ? "reasoning" : "content",
                delta,
              );
            }
          }

          if (streamEvent.event === "tool.delta") {
            contentBreakPending = true;
            reasoningBreakPending = true;
          }

          if (streamEvent.event === "tool.status") {
            contentBreakPending = true;
            reasoningBreakPending = true;
            const toolStatus = parseToolStatus(streamEvent.data);
            if (toolStatus) {
              toolStatusVisible = true;
              updateAssistantMessage(threadId, assistantMessageId, (message) => ({
                ...message,
                compressionStarted: false,
                toolStatus,
                toolActivities: upsertToolActivity(
                  message.toolActivities ?? [],
                  toolStatus,
                ),
              }));
            }
          }

          if (streamEvent.event === "clarification.requested") {
            flushPendingAssistantDeltas();
            const clarification = parseClarificationRequest(streamEvent.data);
            if (clarification) {
              sawClarification = true;
              updateAssistantMessage(threadId, assistantMessageId, (message) => ({
                ...message,
                content: formatClarificationContent(clarification),
                metadata: { clarification },
              }));
            }
          }

          if (streamEvent.event === "todo.updated") {
            replaceTodos(threadId, parseTodos(streamEvent.data.todos));
          }

          if (streamEvent.event === "state.snapshot") {
            flushPendingAssistantDeltas();
            const snapshotTodos = latestTodos(streamEvent);
            if (snapshotTodos !== null) {
              replaceTodos(threadId, snapshotTodos);
            }
            const content = latestAssistantContent(streamEvent);
            if (content) {
              replaceAssistantContent(threadId, assistantMessageId, "content", content);
            }
            const reasoningContent = latestAssistantReasoningContent(streamEvent);
            if (reasoningContent) {
              replaceAssistantContent(
                threadId,
                assistantMessageId,
                "reasoning",
                reasoningContent,
              );
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
            setThreadError(threadId, message);
            updateAssistantMessage(threadId, assistantMessageId, (message) => ({
              ...message,
              compressionStarted: false,
              toolStatus: undefined,
              status: "error",
            }));
          }
        }

        flushPendingAssistantDeltas();
        if (controller.signal.aborted) {
          updateAssistantMessage(threadId, assistantMessageId, (message) => ({
            ...message,
            compressionStarted: false,
            toolStatus: undefined,
            status: "cancelled",
          }));
        } else if (!failed) {
          updateAssistantMessage(threadId, assistantMessageId, (message) => ({
            ...message,
            compressionStarted: false,
            toolStatus: undefined,
            toolActivities: settleRunningToolActivities(message.toolActivities ?? []),
            status: "done",
          }));
        }

        finishRun(threadId, {
          failed,
          aborted: controller.signal.aborted,
          sawClarification,
        });
        return { accepted: true, thread: activeThread, artifacts: discoveredArtifacts };
      } catch (caught) {
        flushPendingAssistantDeltas();
        const threadId = runThreadId;
        if (threadId) {
          if (controller.signal.aborted) {
            updateAssistantMessage(threadId, assistantMessageId, (message) => ({
              ...message,
              compressionStarted: false,
              toolStatus: undefined,
              status: "cancelled",
            }));
            finishRun(threadId, {
              failed: false,
              aborted: true,
              sawClarification: false,
            });
            return { accepted, thread: activeThread, artifacts: [] };
          }

          const message = caught instanceof Error ? caught.message : "stream failed";
          setThreadError(threadId, message);
          updateAssistantMessage(threadId, assistantMessageId, (message) => ({
            ...message,
            compressionStarted: false,
            toolStatus: undefined,
            status: "error",
          }));
          finishRun(threadId, {
            failed: true,
            aborted: false,
            sawClarification: false,
          });
        }
        return { accepted, thread: activeThread, artifacts: [] };
      }

      function finishRun(
        threadId: string,
        outcome: { failed: boolean; aborted: boolean; sawClarification: boolean },
      ) {
        streamingThreadsRef.current.delete(threadId);
        if (abortControllersRef.current.get(threadId) === controller) {
          abortControllersRef.current.delete(threadId);
        }
        messageThreadIndexRef.current.delete(assistantMessageId);

        const isViewing = activeThreadIdRef.current === threadId;
        if (outcome.failed) {
          setRunState(threadId, isViewing ? null : "error");
        } else if (outcome.sawClarification) {
          setRunState(threadId, isViewing ? null : "needs_input");
        } else if (outcome.aborted || isViewing) {
          setRunState(threadId, null);
        } else {
          setRunState(threadId, "attention");
        }
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
      replaceAssistantContent,
      replaceTodos,
      setRunState,
      setThreadError,
      thread,
      updateAssistantMessage,
      updateThreadMessages,
    ],
  );

  const cancelStream = useCallback(() => {
    const threadId = activeThreadIdRef.current;
    if (!threadId) {
      return;
    }
    abortControllersRef.current.get(threadId)?.abort();
  }, []);

  const clearError = useCallback(() => {
    setThreadError(activeThreadIdRef.current, null);
  }, [setThreadError]);

  const isAnyStreaming = useMemo(
    () => Object.values(runStates).some((status) => status === "streaming"),
    [runStates],
  );

  return {
    thread,
    messages,
    todos,
    todoListKey,
    isStreaming,
    isAnyStreaming,
    runStates,
    error,
    sendMessage,
    cancelStream,
    resetThread,
    removeMessage,
    loadThread,
    clearError,
  };
}
