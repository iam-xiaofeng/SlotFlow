import {
  type RefObject,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { type ChatUiMessage } from "@/hooks/use-chat-stream";
import {
  type ClarificationOptionRecord,
  type ClarificationRequestRecord,
} from "@/lib/chat-stream";

import {
  type UserMessageNavItem,
  MessageBubble,
  MessageNavigator,
  assistantMessageHasOutput,
} from "./message-list-parts";

type MessageListProps = {
  messages: ChatUiMessage[];
  messagesEndRef: RefObject<HTMLDivElement | null>;
  isStreaming: boolean;
  onCopyMessage: (content: string) => void;
  onEditLatestUserMessage: (messageId: string, content: string) => Promise<boolean>;
  onRetryLatestAssistantMessage: () => void;
  onSelectClarification: (
    messageId: string,
    clarification: ClarificationRequestRecord,
    option: ClarificationOptionRecord,
  ) => void;
};


export function MessageList({
  messages,
  messagesEndRef,
  isStreaming,
  onCopyMessage,
  onEditLatestUserMessage,
  onRetryLatestAssistantMessage,
  onSelectClarification,
}: MessageListProps) {
  const scrollAreaRef = useRef<HTMLDivElement | null>(null);
  const userMessageRefs = useRef(new Map<string, HTMLElement>());
  const userMessagesRef = useRef<UserMessageNavItem[]>([]);
  const userMessageSignatureRef = useRef("");
  const navigatorCloseTimerRef = useRef<number | null>(null);
  const firstTokenScrolledMessageIdsRef = useRef(new Set<string>());
  const newTurnAnchoredUserIdsRef = useRef(new Set<string>());
  const autoFollowLatestAssistantRef = useRef(true);
  const userScrollIntentRef = useRef(false);
  const [activeUserIndex, setActiveUserIndex] = useState(0);
  const [isNavigatorOpen, setIsNavigatorOpen] = useState(false);
  const [editingUserMessageId, setEditingUserMessageId] = useState<string | null>(null);
  const computedUserMessages = useMemo<UserMessageNavItem[]>(() => {
    let index = 0;
    return messages.flatMap((message) => {
      if (message.role !== "user") {
        return [];
      }
      index += 1;
      return [{ id: message.id, index, content: message.content }];
    });
  }, [messages]);
  const userMessageSignature = computedUserMessages
    .map((item) => `${item.id}:${item.content}`)
    .join("\u0001");
  if (userMessageSignatureRef.current !== userMessageSignature) {
    userMessageSignatureRef.current = userMessageSignature;
    userMessagesRef.current = computedUserMessages;
  }
  const userMessages = userMessagesRef.current;
  const latestUserMessageId = userMessages.at(-1)?.id ?? null;
  const latestAssistantMessage = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index].role === "assistant") {
        return messages[index];
      }
    }
    return null;
  }, [messages]);
  const latestAssistantMessageId = latestAssistantMessage?.id ?? null;
  const latestAssistantFirstTokenScrollKey =
    latestAssistantMessage?.status === "streaming" &&
    assistantMessageHasOutput(latestAssistantMessage)
      ? latestAssistantMessage.id
      : null;
  const latestAssistantStreamingOutputKey =
    latestAssistantMessage?.status === "streaming" &&
    assistantMessageHasOutput(latestAssistantMessage)
      ? [
          latestAssistantMessage.id,
          latestAssistantMessage.content.length,
          latestAssistantMessage.reasoningContent?.length ?? 0,
        ].join(":")
      : null;
  const latestUserTurnAnchorKey =
    isStreaming && latestUserMessageId ? latestUserMessageId : null;
  const latestUserTurnAnchorRefreshKey =
    isStreaming && latestUserMessageId
      ? [
          latestUserMessageId,
          latestAssistantMessage?.content.length ?? 0,
          latestAssistantMessage?.reasoningContent?.length ?? 0,
        ].join(":")
      : null;

  const getViewport = useCallback(
    () =>
      scrollAreaRef.current?.querySelector<HTMLElement>(
        '[data-slot="scroll-area-viewport"]',
      ) ?? null,
    [],
  );

  const scrollViewportToBottom = useCallback(
    (behavior: ScrollBehavior) => {
      const viewport = getViewport();
      if (!viewport) {
        return null;
      }

      return window.requestAnimationFrame(() => {
        const endElement = messagesEndRef.current;
        if (!endElement) {
          viewport.scrollTo({ top: viewport.scrollHeight, behavior });
          return;
        }
        const viewportRect = viewport.getBoundingClientRect();
        const endRect = endElement.getBoundingClientRect();
        const endTop = endRect.top - viewportRect.top + viewport.scrollTop;
        const targetTop = Math.max(0, endTop - viewport.clientHeight + endRect.height);
        viewport.scrollTo({ top: targetTop, behavior });
      });
    },
    [getViewport, messagesEndRef],
  );

  const scrollUserMessageToTurnTop = useCallback(
    (messageId: string, behavior: ScrollBehavior) => {
      const viewport = getViewport();
      const element = userMessageRefs.current.get(messageId);
      if (!viewport || !element) {
        return null;
      }

      return window.requestAnimationFrame(() => {
        const viewportRect = viewport.getBoundingClientRect();
        const elementRect = element.getBoundingClientRect();
        const elementTop = elementRect.top - viewportRect.top + viewport.scrollTop;
        const maxScrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
        const targetTop = Math.min(maxScrollTop, Math.max(0, elementTop - 16));
        viewport.scrollTo({ top: targetTop, behavior });
      });
    },
    [getViewport],
  );

  const isViewportNearBottom = useCallback((viewport: HTMLElement) => {
    const maxScrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
    return maxScrollTop - viewport.scrollTop <= 24;
  }, []);

  const markUserScrollIntent = useCallback(() => {
    userScrollIntentRef.current = true;
  }, []);

  const updateActiveUserMessage = useCallback(() => {
    const viewport = getViewport();
    const currentUserMessages = userMessagesRef.current;
    if (!viewport || currentUserMessages.length === 0) {
      setActiveUserIndex((current) => (current === 0 ? current : 0));
      return;
    }

    const maxScrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
    if (viewport.scrollTop <= 4) {
      const nextIndex = currentUserMessages[0].index;
      setActiveUserIndex((current) => (current === nextIndex ? current : nextIndex));
      return;
    }
    if (maxScrollTop - viewport.scrollTop <= 4) {
      const nextIndex = currentUserMessages[currentUserMessages.length - 1].index;
      setActiveUserIndex((current) => (current === nextIndex ? current : nextIndex));
      return;
    }

    const viewportRect = viewport.getBoundingClientRect();
    const targetY = viewportRect.top + viewportRect.height * 0.38;
    let nextIndex = currentUserMessages[0].index;

    for (const item of currentUserMessages) {
      const element = userMessageRefs.current.get(item.id);
      if (!element) {
        continue;
      }
      if (element.getBoundingClientRect().top <= targetY) {
        nextIndex = item.index;
      }
    }
    setActiveUserIndex((current) => (current === nextIndex ? current : nextIndex));
  }, [getViewport]);

  useEffect(() => {
    const viewport = getViewport();
    if (!viewport) {
      return;
    }

    const handleScroll = () => {
      updateActiveUserMessage();
      if (isViewportNearBottom(viewport)) {
        autoFollowLatestAssistantRef.current = true;
        userScrollIntentRef.current = false;
        return;
      }
      if (userScrollIntentRef.current) {
        autoFollowLatestAssistantRef.current = false;
      }
    };

    updateActiveUserMessage();
    viewport.addEventListener("scroll", handleScroll, { passive: true });
    viewport.addEventListener("wheel", markUserScrollIntent, { passive: true });
    viewport.addEventListener("touchmove", markUserScrollIntent, { passive: true });
    viewport.addEventListener("pointerdown", markUserScrollIntent, { passive: true });
    viewport.addEventListener("keydown", markUserScrollIntent);
    return () => {
      viewport.removeEventListener("scroll", handleScroll);
      viewport.removeEventListener("wheel", markUserScrollIntent);
      viewport.removeEventListener("touchmove", markUserScrollIntent);
      viewport.removeEventListener("pointerdown", markUserScrollIntent);
      viewport.removeEventListener("keydown", markUserScrollIntent);
    };
  }, [getViewport, isViewportNearBottom, markUserScrollIntent, updateActiveUserMessage]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(updateActiveUserMessage);
    return () => window.cancelAnimationFrame(frame);
  }, [userMessageSignature, updateActiveUserMessage]);

  useEffect(() => {
    const messageId = latestUserTurnAnchorKey;
    if (!messageId || newTurnAnchoredUserIdsRef.current.has(messageId)) {
      return;
    }

    const frame = scrollUserMessageToTurnTop(messageId, "smooth");
    if (frame === null) {
      return;
    }

    autoFollowLatestAssistantRef.current = false;
    userScrollIntentRef.current = false;
    newTurnAnchoredUserIdsRef.current.add(messageId);
    return () => window.cancelAnimationFrame(frame);
  }, [latestUserTurnAnchorKey, scrollUserMessageToTurnTop]);

  useEffect(() => {
    if (!latestUserTurnAnchorRefreshKey || !latestUserMessageId || userScrollIntentRef.current) {
      return;
    }

    const frame = scrollUserMessageToTurnTop(latestUserMessageId, "auto");
    if (frame === null) {
      return;
    }

    return () => window.cancelAnimationFrame(frame);
  }, [
    latestUserMessageId,
    latestUserTurnAnchorRefreshKey,
    scrollUserMessageToTurnTop,
  ]);

  useEffect(() => {
    const messageId = latestAssistantFirstTokenScrollKey;
    if (!messageId || firstTokenScrolledMessageIdsRef.current.has(messageId)) {
      return;
    }

    const frame = latestUserMessageId
      ? scrollUserMessageToTurnTop(latestUserMessageId, "smooth")
      : scrollViewportToBottom("smooth");
    if (frame === null) {
      return;
    }

    autoFollowLatestAssistantRef.current = !latestUserMessageId;
    userScrollIntentRef.current = false;
    firstTokenScrolledMessageIdsRef.current.add(messageId);
    return () => window.cancelAnimationFrame(frame);
  }, [
    latestAssistantFirstTokenScrollKey,
    latestUserMessageId,
    scrollUserMessageToTurnTop,
    scrollViewportToBottom,
  ]);

  useEffect(() => {
    if (!latestAssistantStreamingOutputKey || !autoFollowLatestAssistantRef.current) {
      return;
    }

    const frame = scrollViewportToBottom("auto");
    if (frame === null) {
      return;
    }

    return () => window.cancelAnimationFrame(frame);
  }, [latestAssistantStreamingOutputKey, scrollViewportToBottom]);

  useEffect(() => {
    return () => {
      if (navigatorCloseTimerRef.current !== null) {
        window.clearTimeout(navigatorCloseTimerRef.current);
      }
    };
  }, []);

  function registerUserMessage(messageId: string, element: HTMLElement | null) {
    if (element) {
      userMessageRefs.current.set(messageId, element);
    } else {
      userMessageRefs.current.delete(messageId);
    }
  }

  function jumpToUserMessage(messageId: string) {
    const viewport = getViewport();
    const element = userMessageRefs.current.get(messageId);
    if (!viewport || !element) {
      return;
    }

    const viewportRect = viewport.getBoundingClientRect();
    const elementRect = element.getBoundingClientRect();
    const elementTop = elementRect.top - viewportRect.top + viewport.scrollTop;
    const maxScrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
    const targetTop = Math.min(
      maxScrollTop,
      Math.max(0, elementTop - viewport.clientHeight * 0.32),
    );

    viewport.scrollTo({
      top: targetTop,
      behavior: "smooth",
    });
  }

  async function submitUserMessageEdit(messageId: string, content: string) {
    setEditingUserMessageId(null);
    const accepted = await onEditLatestUserMessage(messageId, content);
    if (!accepted) {
      setEditingUserMessageId(messageId);
    }
    return accepted;
  }

  return (
    <div ref={scrollAreaRef} className="relative min-h-0 flex-1 overflow-hidden">
      <ScrollArea className="size-full overflow-hidden">
        <div className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-4 py-6 sm:px-6 lg:px-8">
          <div className="flex flex-col gap-5">
            {messages.map((message) => (
              <MessageBubble
                key={message.id}
                message={message}
                isLatestUser={message.id === latestUserMessageId}
                isLatestAssistant={message.id === latestAssistantMessageId}
                isEditing={message.id === editingUserMessageId}
                isStreaming={isStreaming}
                onCopyMessage={onCopyMessage}
                onStartEdit={() => setEditingUserMessageId(message.id)}
                onCancelEdit={() => setEditingUserMessageId(null)}
                onSubmitEdit={(content) => submitUserMessageEdit(message.id, content)}
                onRetryLatestAssistantMessage={onRetryLatestAssistantMessage}
                onSelectClarification={onSelectClarification}
                userMessageRef={
                  message.role === "user"
                    ? (element) => registerUserMessage(message.id, element)
                    : undefined
                }
              />
            ))}
          </div>
          <div ref={messagesEndRef} />
          <div
            aria-hidden="true"
            className={
              latestUserTurnAnchorKey
                ? "h-[58vh] shrink-0 transition-[height] duration-300 ease-out"
                : "h-0 shrink-0 transition-[height] duration-300 ease-out"
            }
          />
        </div>
      </ScrollArea>
      <MessageNavigator
        activeIndex={activeUserIndex || userMessages[0]?.index || 0}
        isOpen={isNavigatorOpen}
        userMessages={userMessages}
        onOpenChange={setIsNavigatorOpen}
        closeTimerRef={navigatorCloseTimerRef}
        onJumpToMessage={jumpToUserMessage}
      />
    </div>
  );
}

export function EmptyState() {
  return (
    <div className="grid flex-1 place-items-center px-4 py-16 text-center">
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
        有什么可以帮忙的？
      </h1>
    </div>
  );
}
