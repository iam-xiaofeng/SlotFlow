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

  const getViewport = useCallback(
    () =>
      scrollAreaRef.current?.querySelector<HTMLElement>(
        '[data-slot="scroll-area-viewport"]',
      ) ?? null,
    [],
  );

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

    updateActiveUserMessage();
    viewport.addEventListener("scroll", updateActiveUserMessage, { passive: true });
    return () => viewport.removeEventListener("scroll", updateActiveUserMessage);
  }, [getViewport, updateActiveUserMessage]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(updateActiveUserMessage);
    return () => window.cancelAnimationFrame(frame);
  }, [userMessageSignature, updateActiveUserMessage]);

  useEffect(() => {
    const messageId = latestAssistantFirstTokenScrollKey;
    if (!messageId || firstTokenScrolledMessageIdsRef.current.has(messageId)) {
      return;
    }

    const viewport = getViewport();
    if (!viewport) {
      return;
    }

    firstTokenScrolledMessageIdsRef.current.add(messageId);
    const frame = window.requestAnimationFrame(() => {
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [getViewport, latestAssistantFirstTokenScrollKey]);

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

