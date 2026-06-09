"use client";

import { startTransition, useCallback, useEffect, useRef, useState } from "react";

import {
  type ChatMode,
  type ChatStreamEvent,
  type ChatStreamRequest,
  type ThreadRecord,
  createThread,
  streamThreadRun,
} from "@/lib/chat-stream";

export type ChatUiMessageRole = "user" | "assistant";
export type ChatUiMessageStatus = "streaming" | "done" | "error" | "cancelled";

export type ChatUiMessage = {
  id: string;
  role: ChatUiMessageRole;
  content: string;
  status: ChatUiMessageStatus;
  runId?: string;
};

export type UseChatStreamOptions = {
  defaultThreadTitle?: string;
  defaultModelName?: string;
  defaultMode?: ChatMode;
  defaultAgentName?: string;
  defaultMetadata?: Record<string, unknown>;
  maxEventLogItems?: number;
};

export type SendChatMessageOptions = Omit<Partial<ChatStreamRequest>, "message">;

export type SendChatMessageResult = {
  accepted: boolean;
  thread: ThreadRecord | null;
};

const fallbackThreadTitle = "SlotFlow chat";
const fallbackModelName = "deepseek-v4-flash";
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
  const [events, setEvents] = useState<ChatStreamEvent[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const isStreamingRef = useRef(false);

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

  const appendAssistantText = useCallback((messageId: string, delta: string) => {
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId
          ? { ...message, content: message.content + delta }
          : message,
      ),
    );
  }, []);

  const replaceAssistantText = useCallback((messageId: string, content: string) => {
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId ? { ...message, content } : message,
      ),
    );
  }, []);

  const patchAssistant = useCallback(
    (messageId: string, patch: Partial<ChatUiMessage>) => {
      setMessages((current) =>
        current.map((message) =>
          message.id === messageId ? { ...message, ...patch } : message,
        ),
      );
    },
    [],
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

  const sendMessage = useCallback(
    async (
      rawMessage: string,
      overrides: SendChatMessageOptions = {},
    ): Promise<SendChatMessageResult> => {
      const text = rawMessage.trim();
      if (!text || isStreamingRef.current) {
        return { accepted: false, thread };
      }

      const controller = new AbortController();
      const userMessage: ChatUiMessage = {
        id: makeId("user"),
        role: "user",
        content: text,
        status: "done",
      };
      const assistantMessageId = makeId("assistant");
      const assistantMessage: ChatUiMessage = {
        id: assistantMessageId,
        role: "assistant",
        content: "",
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
          activeThread = await createThread(defaultThreadTitle, {
            signal: controller.signal,
          });
          setThread(activeThread);
        }

        setMessages((current) => [...current, userMessage, assistantMessage]);
        accepted = true;

        let failed = false;
        const body: ChatStreamRequest = {
          message: text,
          model_name: overrides.model_name ?? defaultModelName,
          mode: overrides.mode ?? defaultMode,
          agent_name: overrides.agent_name ?? defaultAgentName,
          files: overrides.files ?? [],
          metadata: {
            ...defaultMetadata,
            ...(overrides.metadata ?? {}),
          },
        };

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
              appendAssistantText(assistantMessageId, delta);
            }
          }

          if (streamEvent.event === "state.snapshot") {
            const content = latestAssistantContent(streamEvent);
            if (content) {
              replaceAssistantText(assistantMessageId, content);
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

        return { accepted: true, thread: activeThread };
      } catch (caught) {
        if (controller.signal.aborted) {
          patchAssistant(assistantMessageId, { status: "cancelled" });
          return { accepted, thread: activeThread };
        }

        const message = caught instanceof Error ? caught.message : "stream failed";
        setError(message);
        patchAssistant(assistantMessageId, { status: "error" });
        return { accepted, thread: activeThread };
      } finally {
        if (abortControllerRef.current === controller) {
          abortControllerRef.current = null;
        }
        isStreamingRef.current = false;
        setIsStreaming(false);
      }
    },
    [
      appendAssistantText,
      appendEvent,
      defaultAgentName,
      defaultMetadata,
      defaultMode,
      defaultModelName,
      defaultThreadTitle,
      patchAssistant,
      replaceAssistantText,
      thread,
    ],
  );

  const cancelStream = useCallback(() => {
    abortControllerRef.current?.abort();
  }, []);

  return {
    thread,
    messages,
    events,
    isStreaming,
    error,
    sendMessage,
    startNewThread,
    cancelStream,
    clearError: () => setError(null),
  };
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

function makeId(prefix: string) {
  return `${prefix}_${crypto.randomUUID()}`;
}
