import {
  type RefObject,
  useCallback,
  useEffect,
  useLayoutEffect,
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
import { SlotFlowLogo } from "./slotflow-logo";

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
  const [turnAnchorSpacerHeight, setTurnAnchorSpacerHeight] = useState(0);
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

      const frame = window.requestAnimationFrame(() => {
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
      return () => window.cancelAnimationFrame(frame);
    },
    [getViewport, messagesEndRef],
  );

  const measureTurnAnchorSpacerHeight = useCallback(
    (messageId: string | null) => {
      if (!messageId) {
        return 0;
      }
      const viewport = getViewport();
      if (!viewport) {
        return 0;
      }
      const element = userMessageRefs.current.get(messageId);
      const endElement = messagesEndRef.current;
      if (!element) {
        return 0;
      }
      // 只补足"把本轮用户消息锚到视口顶部"所缺的高度:随着回答长高,spacer 同步收缩,
      // 输出填满一屏后归零。否则流式期间往下滚会滚进一整屏空白。
      const elementTop = element.getBoundingClientRect().top;
      const contentBottom = endElement
        ? endElement.getBoundingClientRect().bottom
        : element.getBoundingClientRect().bottom;
      const turnContentHeight = Math.max(0, contentBottom - elementTop);
      return Math.ceil(
        Math.max(0, viewport.clientHeight - turnContentHeight - 16),
      );
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

      const frames: number[] = [];
      const run = (remainingFrames: number, scrollBehavior: ScrollBehavior) => {
        const frame = window.requestAnimationFrame(() => {
          const currentViewport = getViewport();
          const currentElement = userMessageRefs.current.get(messageId);
          if (!currentViewport || !currentElement) {
            return;
          }

          const viewportRect = currentViewport.getBoundingClientRect();
          const elementRect = currentElement.getBoundingClientRect();
          const elementTop =
            elementRect.top - viewportRect.top + currentViewport.scrollTop;
          const maxScrollTop = Math.max(
            0,
            currentViewport.scrollHeight - currentViewport.clientHeight,
          );
          const targetTop = Math.min(maxScrollTop, Math.max(0, elementTop - 16));
          currentViewport.scrollTo({ top: targetTop, behavior: scrollBehavior });

          if (remainingFrames > 1) {
            run(remainingFrames - 1, "auto");
          }
        });
        frames.push(frame);
      };

      run(2, behavior);
      return () => {
        for (const frame of frames) {
          window.cancelAnimationFrame(frame);
        }
      };
    },
    [getViewport],
  );

  const scrollTurnAnchorOrLatestOutput = useCallback(
    (messageId: string) => {
      const viewport = getViewport();
      const element = userMessageRefs.current.get(messageId);
      const endElement = messagesEndRef.current;
      if (!viewport || !element || !endElement) {
        return null;
      }

      const frame = window.requestAnimationFrame(() => {
        const currentViewport = getViewport();
        const currentElement = userMessageRefs.current.get(messageId);
        const currentEndElement = messagesEndRef.current;
        if (!currentViewport || !currentElement || !currentEndElement) {
          return;
        }

        const viewportRect = currentViewport.getBoundingClientRect();
        const endRect = currentEndElement.getBoundingClientRect();
        const shouldFollowLatestOutput =
          autoFollowLatestAssistantRef.current ||
          endRect.bottom > viewportRect.bottom - 24;

        if (shouldFollowLatestOutput) {
          autoFollowLatestAssistantRef.current = true;
          const endTop =
            endRect.top - viewportRect.top + currentViewport.scrollTop;
          const targetTop = Math.max(
            0,
            endTop - currentViewport.clientHeight + endRect.height,
          );
          currentViewport.scrollTo({ top: targetTop, behavior: "auto" });
          return;
        }

        autoFollowLatestAssistantRef.current = false;
        const elementRect = currentElement.getBoundingClientRect();
        const elementTop =
          elementRect.top - viewportRect.top + currentViewport.scrollTop;
        const maxScrollTop = Math.max(
          0,
          currentViewport.scrollHeight - currentViewport.clientHeight,
        );
        const targetTop = Math.min(maxScrollTop, Math.max(0, elementTop - 16));
        currentViewport.scrollTo({ top: targetTop, behavior: "auto" });
      });

      return () => window.cancelAnimationFrame(frame);
    },
    [getViewport, messagesEndRef],
  );

  useLayoutEffect(() => {
    const updateSpacerHeight = () => {
      const nextHeight = measureTurnAnchorSpacerHeight(latestUserTurnAnchorKey);
      setTurnAnchorSpacerHeight((current) =>
        current === nextHeight ? current : nextHeight,
      );
    };

    updateSpacerHeight();
    if (!latestUserTurnAnchorKey) {
      return;
    }

    window.addEventListener("resize", updateSpacerHeight);
    return () => window.removeEventListener("resize", updateSpacerHeight);
  }, [
    latestUserTurnAnchorKey,
    latestUserTurnAnchorRefreshKey,
    measureTurnAnchorSpacerHeight,
    userMessageSignature,
  ]);

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
      // ⚠️ 这里**不能**用"程序滚动时间窗"来提前 return。流式期间每来一批 token 就会调一次
      // scrollViewportToBottom，而它每次都把时间窗往后推 700ms —— 于是整个流式过程时间窗永远
      // 有效，用户往上滚的那次 scroll 事件被直接吞掉，autoFollow 永远保持 true，页面被死死钉在
      // 底部（用户报告的"流式时滑不上去"就是这个）。
      //
      // 正确的判据本来就不需要"谁触发的"：
      //   · 在底部  → 一定该跟随（无论这次滚动来自谁）；
      //   · 不在底部 + 有真实手势（wheel/touch/键盘/指针）→ 用户明确要看历史，立刻停住跟随。
      // 程序滚动不会产生手势，所以不会误伤。
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

    const cleanup = scrollUserMessageToTurnTop(messageId, "auto");
    if (cleanup === null) {
      return;
    }

    autoFollowLatestAssistantRef.current = false;
    userScrollIntentRef.current = false;
    newTurnAnchoredUserIdsRef.current.add(messageId);
    return cleanup;
  }, [latestUserTurnAnchorKey, scrollUserMessageToTurnTop]);

  useEffect(() => {
    if (!latestUserTurnAnchorRefreshKey || !latestUserMessageId || userScrollIntentRef.current) {
      return;
    }

    const cleanup = scrollTurnAnchorOrLatestOutput(latestUserMessageId);
    if (cleanup === null) {
      return;
    }

    return cleanup;
  }, [
    latestUserMessageId,
    latestUserTurnAnchorRefreshKey,
    scrollTurnAnchorOrLatestOutput,
  ]);

  useEffect(() => {
    const messageId = latestAssistantFirstTokenScrollKey;
    if (!messageId || firstTokenScrolledMessageIdsRef.current.has(messageId)) {
      return;
    }

    const cleanup = latestUserMessageId
      ? scrollTurnAnchorOrLatestOutput(latestUserMessageId)
      : scrollViewportToBottom("smooth");
    if (cleanup === null) {
      return;
    }

    if (!latestUserMessageId) {
      autoFollowLatestAssistantRef.current = true;
    }
    userScrollIntentRef.current = false;
    firstTokenScrolledMessageIdsRef.current.add(messageId);
    return cleanup;
  }, [
    latestAssistantFirstTokenScrollKey,
    latestUserMessageId,
    scrollTurnAnchorOrLatestOutput,
    scrollViewportToBottom,
  ]);

  useEffect(() => {
    if (!latestAssistantStreamingOutputKey || !autoFollowLatestAssistantRef.current) {
      return;
    }

    const cleanup = scrollViewportToBottom("auto");
    if (cleanup === null) {
      return;
    }

    return cleanup;
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
            className="shrink-0"
            style={{ height: latestUserTurnAnchorKey ? turnAnchorSpacerHeight : 0 }}
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

const emptyStateSuggestions = [
  {
    title: "整理一份资料",
    prompt: "帮我搜集并整理一个主题的网页资料，输出成结构化的摘要文档。",
  },
  {
    title: "写代码并验证",
    prompt: "帮我写一段代码解决一个问题，并在沙箱里运行验证结果。",
  },
  {
    title: "分析上传的文件",
    prompt: "我想上传一个文件，请帮我阅读并总结其中的要点。",
  },
  {
    title: "规划一件复杂的事",
    prompt: "帮我把一个复杂目标拆解成可执行的分步计划，并逐步推进。",
  },
];

export function EmptyState({
  onSuggestion,
}: {
  onSuggestion?: (prompt: string) => void;
}) {
  return (
    <div className="flex flex-col items-center px-4 pb-8 pt-10 text-center">
      <SlotFlowLogo className="mb-5 size-12 rounded-xl shadow-sm" />
      <h1 className="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
        有什么可以帮忙的？
      </h1>
      <p className="mt-3 max-w-md text-[0.95rem] leading-6 text-muted-foreground">
        SlotFlow 可以搜索资料、读写文件、在沙箱里运行代码，一步步帮你把事情做完。
      </p>
      {onSuggestion ? (
        <div className="mt-8 grid w-full max-w-2xl grid-cols-1 gap-2.5 sm:grid-cols-2">
          {emptyStateSuggestions.map((suggestion) => (
            <button
              key={suggestion.title}
              type="button"
              className="slotflow-hover-lift rounded-xl border border-border/80 bg-card/80 px-4 py-3 text-left backdrop-blur transition-colors hover:border-ring/40 hover:bg-card"
              onClick={() => onSuggestion(suggestion.prompt)}
            >
              <span className="block text-sm font-medium text-foreground">
                {suggestion.title}
              </span>
              <span className="mt-1 line-clamp-2 block text-xs leading-5 text-muted-foreground">
                {suggestion.prompt}
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
