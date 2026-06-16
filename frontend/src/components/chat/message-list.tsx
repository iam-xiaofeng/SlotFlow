import {
  type RefObject,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ChevronDown,
  Copy,
  FileText,
  Lightbulb,
  List,
  Pencil,
  RotateCcw,
  SendHorizontal,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { type ChatUiMessage } from "@/hooks/use-chat-stream";
import {
  type ClarificationOptionRecord,
  type ClarificationRequestRecord,
  resolveUploadRawUrl,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

import {
  formatFileSize,
  getMessageFiles,
  displayFileName,
  isImageFile,
  type MessageFile,
} from "./chat-format";
import { MarkdownContent } from "./markdown-content";

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

type UserMessageNavItem = {
  id: string;
  index: number;
  content: string;
};

type AssistantContentParts = {
  thought: string;
  body: string;
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
  const latestAssistantMessageId =
    [...messages].reverse().find((message) => message.role === "assistant")?.id ?? null;

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
    const viewport = getViewport();
    if (!viewport) {
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      viewport.scrollTo({ top: viewport.scrollHeight });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [getViewport, messages]);

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

function MessageBubble({
  message,
  isLatestUser,
  isLatestAssistant,
  isEditing,
  isStreaming,
  onCopyMessage,
  onStartEdit,
  onCancelEdit,
  onSubmitEdit,
  onRetryLatestAssistantMessage,
  onSelectClarification,
  userMessageRef,
}: {
  message: ChatUiMessage;
  isLatestUser: boolean;
  isLatestAssistant: boolean;
  isEditing: boolean;
  isStreaming: boolean;
  onCopyMessage: (content: string) => void;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onSubmitEdit: (content: string) => Promise<boolean>;
  onRetryLatestAssistantMessage: () => void;
  onSelectClarification: (
    messageId: string,
    clarification: ClarificationRequestRecord,
    option: ClarificationOptionRecord,
  ) => void;
  userMessageRef?: (element: HTMLElement | null) => void;
}) {
  const isUser = message.role === "user";
  const files = getMessageFiles(message);
  const content = message.content;
  const clarification = getClarificationRequest(message);
  const assistantContent =
    isUser || clarification
      ? { thought: "", body: content }
      : splitAssistantContent(content, message.reasoningContent);
  const isAssistantThinking =
    !isUser &&
    !clarification &&
    message.status === "streaming" &&
    !content.trim() &&
    !message.reasoningContent?.trim();
  const canShowAssistantActions =
    !clarification &&
    isLatestAssistant &&
    message.status === "done" &&
    Boolean(content.trim());
  const canAnswerClarification =
    Boolean(clarification) && isLatestAssistant && !isStreaming;

  return (
    <article
      ref={userMessageRef}
      className={cn("group/message flex", isUser ? "justify-end" : "justify-start")}
    >
      <div
        className={cn(
          "flex min-w-0 flex-col gap-2 text-base leading-7",
          isUser
            ? "max-w-[82%] items-end text-foreground"
            : "w-full max-w-3xl px-1 text-foreground",
        )}
      >
        {files.length > 0 ? <MessageAttachments files={files} /> : null}
        {isUser && isEditing ? (
          <InlineUserMessageEditor
            initialContent={content}
            isSubmitting={isStreaming}
            onCancel={onCancelEdit}
            onSubmit={onSubmitEdit}
          />
        ) : (
          <div
            className={cn(
              "min-w-0 break-words",
              isUser ? "rounded-2xl bg-muted px-4 py-2.5" : "w-full",
            )}
          >
            {isUser ? (
              <p className="whitespace-pre-wrap">{content}</p>
            ) : clarification ? (
              <ClarificationRequestPanel
                clarification={clarification}
                disabled={!canAnswerClarification}
                onSelect={(selectedClarification, option) =>
                  onSelectClarification(message.id, selectedClarification, option)
                }
              />
            ) : isAssistantThinking ? (
              <AssistantThinkingSummary content="" isStreaming />
            ) : (
              <>
                {assistantContent.thought ? (
                  <AssistantThinkingSummary
                    content={assistantContent.thought}
                    isStreaming={message.status === "streaming"}
                  />
                ) : null}
                {assistantContent.body.trim() ? (
                  <MarkdownContent content={assistantContent.body} />
                ) : message.status === "streaming" ? (
                  <ThinkingIndicator />
                ) : null}
              </>
            )}
          </div>
        )}
        {isUser && !isEditing ? (
          <UserMessageActions
            canEdit={isLatestUser && !isStreaming}
            content={content}
            messageId={message.id}
            onCopyMessage={onCopyMessage}
            onStartEdit={onStartEdit}
          />
        ) : canShowAssistantActions ? (
          <AssistantMessageActions
            content={content}
            onCopyMessage={onCopyMessage}
            onRetryLatestAssistantMessage={onRetryLatestAssistantMessage}
          />
        ) : null}
      </div>
    </article>
  );
}

function UserMessageActions({
  canEdit,
  content,
  messageId,
  onCopyMessage,
  onStartEdit,
}: {
  canEdit: boolean;
  content: string;
  messageId: string;
  onCopyMessage: (content: string) => void;
  onStartEdit: () => void;
}) {
  return (
    <div className="flex h-7 items-center justify-end gap-1 opacity-0 transition-opacity group-hover/message:opacity-100">
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        title="复制"
        onClick={() => onCopyMessage(content)}
      >
        <Copy className="size-4" />
        <span className="sr-only">复制</span>
      </Button>
      {canEdit ? (
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          title="编辑最新输入"
          onClick={onStartEdit}
        >
          <Pencil className="size-4" />
          <span className="sr-only">编辑最新输入 {messageId}</span>
        </Button>
      ) : null}
    </div>
  );
}

function InlineUserMessageEditor({
  initialContent,
  isSubmitting,
  onCancel,
  onSubmit,
}: {
  initialContent: string;
  isSubmitting: boolean;
  onCancel: () => void;
  onSubmit: (content: string) => Promise<boolean>;
}) {
  const [value, setValue] = useState(initialContent);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const canSubmit = Boolean(value.trim()) && !isSubmitting;

  useEffect(() => {
    setValue(initialContent);
    window.requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      textarea?.focus();
      textarea?.setSelectionRange(textarea.value.length, textarea.value.length);
    });
  }, [initialContent]);

  async function submit() {
    if (!canSubmit) {
      return;
    }
    await onSubmit(value);
  }

  return (
    <div className="w-full max-w-[48rem] rounded-3xl bg-muted px-4 py-3">
      <textarea
        ref={textareaRef}
        value={value}
        rows={3}
        disabled={isSubmitting}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) {
            return;
          }
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void submit();
          }
        }}
        className="max-h-[16rem] min-h-24 w-full resize-none overflow-y-auto bg-transparent text-base leading-7 outline-none [overflow-wrap:anywhere]"
      />
      <div className="mt-3 flex justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="rounded-full"
          disabled={isSubmitting}
          onClick={onCancel}
        >
          取消
        </Button>
        <Button
          type="button"
          size="sm"
          className="rounded-full"
          disabled={!canSubmit}
          onClick={() => void submit()}
        >
          发送
        </Button>
      </div>
    </div>
  );
}

function AssistantMessageActions({
  content,
  onCopyMessage,
  onRetryLatestAssistantMessage,
}: {
  content: string;
  onCopyMessage: (content: string) => void;
  onRetryLatestAssistantMessage: () => void;
}) {
  return (
    <div className="flex h-8 items-center gap-1 text-muted-foreground">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        title="复制"
        className="h-8 gap-1.5 px-2 text-xs"
        onClick={() => onCopyMessage(content)}
      >
        <Copy className="size-4" />
        复制
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        title="重试"
        className="h-8 gap-1.5 px-2 text-xs"
        onClick={onRetryLatestAssistantMessage}
      >
        <RotateCcw className="size-4" />
        重试
      </Button>
    </div>
  );
}

function ThinkingIndicator() {
  return (
    <div className="flex h-8 items-center gap-1.5 text-muted-foreground">
      <span className="sr-only">思考中</span>
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          className="size-2 rounded-full bg-current opacity-60 animate-bounce"
          style={{ animationDelay: `${index * 120}ms` }}
        />
      ))}
    </div>
  );
}

function ClarificationRequestPanel({
  clarification,
  disabled,
  onSelect,
}: {
  clarification: ClarificationRequestRecord;
  disabled: boolean;
  onSelect: (
    clarification: ClarificationRequestRecord,
    option: ClarificationOptionRecord,
  ) => void;
}) {
  useEffect(() => {
    if (disabled || clarification.options.length === 0) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      const target = event.target;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        (target instanceof HTMLElement && target.isContentEditable)
      ) {
        return;
      }
      const option = clarification.options.find(
        (item) =>
          item.id.toLowerCase() === event.key.toLowerCase() &&
          !isClarificationFreeformOption(item),
      );
      if (!option) {
        return;
      }
      event.preventDefault();
      onSelect(clarification, option);
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [clarification, disabled, onSelect]);

  return (
    <div className="max-w-3xl rounded-lg border bg-background p-4 shadow-sm">
      {clarification.context ? (
        <p className="mb-2 text-sm leading-6 text-muted-foreground">
          {clarification.context}
        </p>
      ) : null}
      <p className="whitespace-pre-wrap text-base font-medium leading-7">
        {clarification.question}
      </p>
      {clarification.options.length > 0 ? (
        <div className="mt-3 grid gap-2">
          {clarification.options.map((option) =>
            isClarificationFreeformOption(option) ? (
              <ClarificationFreeformOption
                key={option.id}
                clarification={clarification}
                disabled={disabled}
                option={option}
                onSelect={onSelect}
              />
            ) : (
              <Button
                key={option.id}
                type="button"
                variant="outline"
                className="h-auto justify-start gap-3 rounded-lg px-3 py-2.5 text-left"
                disabled={disabled}
                onClick={() => onSelect(clarification, option)}
              >
                <span className="grid size-6 shrink-0 place-items-center rounded-md bg-muted text-xs font-semibold">
                  {option.id}
                </span>
                <span className="min-w-0 whitespace-normal break-words leading-6">
                  {option.label}
                </span>
              </Button>
            ),
          )}
        </div>
      ) : null}
    </div>
  );
}

function ClarificationFreeformOption({
  clarification,
  disabled,
  option,
  onSelect,
}: {
  clarification: ClarificationRequestRecord;
  disabled: boolean;
  option: ClarificationOptionRecord;
  onSelect: (
    clarification: ClarificationRequestRecord,
    option: ClarificationOptionRecord,
  ) => void;
}) {
  const [value, setValue] = useState("");
  const canSend = Boolean(value.trim()) && !disabled;
  const placeholder = option.label.replace(/^其他(?:[:：\s-]*|$)/u, "").trim() || "请补充说明";

  function submit() {
    if (!canSend) {
      return;
    }
    onSelect(clarification, {
      ...option,
      label: value.trim(),
    });
    setValue("");
  }

  return (
    <div className="flex min-h-14 items-center gap-2 rounded-lg border bg-background px-3 py-2">
      <span className="grid size-6 shrink-0 place-items-center rounded-md bg-muted text-xs font-semibold">
        {option.id}
      </span>
      <input
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) {
            return;
          }
          if (event.key === "Enter") {
            event.preventDefault();
            submit();
          }
        }}
        className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
      />
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="size-9 shrink-0"
        disabled={!canSend}
        title="发送说明"
        onClick={submit}
      >
        <SendHorizontal className="size-4" />
        <span className="sr-only">发送说明</span>
      </Button>
    </div>
  );
}

function isClarificationFreeformOption(option: ClarificationOptionRecord): boolean {
  const text = `${option.id} ${option.label}`.toLowerCase();
  return (
    text.includes("其他") ||
    text.includes("请说明") ||
    text.includes("other") ||
    text.includes("specify")
  );
}

function MessageNavigator({
  activeIndex,
  isOpen,
  userMessages,
  onOpenChange,
  closeTimerRef,
  onJumpToMessage,
}: {
  activeIndex: number;
  isOpen: boolean;
  userMessages: UserMessageNavItem[];
  onOpenChange: (open: boolean) => void;
  closeTimerRef: RefObject<number | null>;
  onJumpToMessage: (messageId: string) => void;
}) {
  if (userMessages.length <= 1) {
    return null;
  }

  function openNavigator() {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    onOpenChange(true);
  }

  function scheduleCloseNavigator() {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
    }
    closeTimerRef.current = window.setTimeout(() => {
      onOpenChange(false);
      closeTimerRef.current = null;
    }, 260);
  }

  return (
    <div
      className="absolute right-3 top-1/2 z-10 -translate-y-1/2"
      onMouseEnter={openNavigator}
      onMouseLeave={scheduleCloseNavigator}
      onFocus={openNavigator}
      onBlur={scheduleCloseNavigator}
    >
      <button
        type="button"
        className="flex h-14 w-10 flex-col items-center justify-center gap-0.5 rounded-full border bg-background/90 text-xs font-medium text-muted-foreground shadow-sm backdrop-blur hover:text-foreground"
        title="消息定位"
        onClick={() => onOpenChange(!isOpen)}
      >
        <List className="size-4" />
        <span>{activeIndex}/{userMessages.length}</span>
      </button>
      {isOpen ? (
        <div
          className="absolute right-12 top-1/2 w-72 -translate-y-1/2 rounded-xl border bg-popover p-2 text-popover-foreground shadow-lg"
          onMouseEnter={openNavigator}
          onWheel={openNavigator}
        >
          <div className="absolute -right-4 top-0 h-full w-4" />
          <div className="mb-1 px-2 text-xs text-muted-foreground">用户输入</div>
          <div className="flex max-h-80 flex-col gap-1 overflow-y-auto">
            {userMessages.map((item) => (
              <button
                key={item.id}
                type="button"
                className={cn(
                  "rounded-lg px-2 py-2 text-left text-sm hover:bg-muted",
                  item.index === activeIndex && "bg-muted",
                )}
                onClick={() => onJumpToMessage(item.id)}
              >
                <span className="mb-0.5 block text-xs text-muted-foreground">
                  第 {item.index} 条
                </span>
                <span className="line-clamp-2 break-words">
                  {item.content || "空消息"}
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function getClarificationRequest(
  message: ChatUiMessage,
): ClarificationRequestRecord | null {
  const raw = message.metadata?.clarification;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }

  const record = raw as Record<string, unknown>;
  if (
    record.type !== "clarification" ||
    typeof record.id !== "string" ||
    typeof record.question !== "string"
  ) {
    return null;
  }

  const options = Array.isArray(record.options)
    ? record.options.flatMap((item) => {
        if (
          item &&
          typeof item === "object" &&
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
    id: record.id,
    question: record.question,
    clarification_type:
      typeof record.clarification_type === "string"
        ? record.clarification_type
        : "missing_info",
    context: typeof record.context === "string" ? record.context : null,
    options,
    source:
      typeof record.source === "string" ? record.source : "slotflow_clarification",
    thread_id: typeof record.thread_id === "string" ? record.thread_id : null,
    run_id: typeof record.run_id === "string" ? record.run_id : null,
  };
}

function splitAssistantContent(
  content: string,
  reasoningContent?: string,
): AssistantContentParts {
  if (reasoningContent?.trim()) {
    return { thought: reasoningContent.trim(), body: content };
  }

  const normalized = content.trim();
  if (!normalized) {
    return { thought: "", body: "" };
  }

  const lines = normalized.split(/\r?\n/);
  const thoughtStart = lines.findIndex((line) => isThoughtHeading(line));
  if (thoughtStart < 0) {
    return { thought: "", body: content };
  }

  const bodyBeforeThought = lines.slice(0, thoughtStart).join("\n").trim();
  const bodyStart = lines.findIndex((line, index) => {
    if (index <= thoughtStart + 2) {
      return false;
    }
    const trimmed = line.trim();
    return (
      /^#{1,3}\s*(?:20\d{2}|[一二三四五六七八九十]+、|报告|.*报告|结果|结论|总结|最终答复|最终回答|产物)/u.test(
        trimmed,
      ) || /^(?:最终答复|最终回答|结果|结论|总结|产物)[:：]/u.test(trimmed)
    );
  });

  if (bodyStart > thoughtStart) {
    const bodyAfterThought = lines.slice(bodyStart).join("\n").trim();
    return {
      thought: lines.slice(thoughtStart, bodyStart).join("\n").trim(),
      body: [bodyBeforeThought, bodyAfterThought].filter(Boolean).join("\n\n"),
    };
  }

  return {
    thought: lines.slice(thoughtStart).join("\n").trim(),
    body: bodyBeforeThought,
  };
}

function isThoughtHeading(line: string) {
  const normalized = line
    .trim()
    .replace(/^#{1,6}\s*/, "")
    .replace(/^[^\w\u4e00-\u9fff]{0,8}\s*/, "")
    .trim();
  return /^(?:Durable Context Summary|思考过程|(?:我的)?(?:实际)?推理过程(?:（[^）]+）)?|流程回放|过程回放|隐藏步骤|Thinking|Reasoning)\s*[:：]?\s*$/iu.test(
    normalized,
  );
}

function AssistantThinkingSummary({
  content,
  isStreaming = false,
}: {
  content: string;
  isStreaming?: boolean;
}) {
  const displayContent = stripThoughtHeading(content);
  const hasContent = Boolean(displayContent.trim());

  return (
    <details
      className="group/thinking mb-5 w-full overflow-hidden rounded-lg border border-border/70 bg-background text-sm text-muted-foreground shadow-sm"
      open
    >
      <summary className="flex h-9 cursor-pointer list-none items-center gap-2 bg-muted/30 px-3 font-medium text-muted-foreground transition-colors hover:text-foreground [&::-webkit-details-marker]:hidden">
        <span className="grid size-5 place-items-center text-muted-foreground">
          <Lightbulb className="size-4" />
        </span>
        <span>{isStreaming && !hasContent ? "思考中" : "思考过程"}</span>
        <ChevronDown className="ml-auto size-4 transition-transform group-open/thinking:rotate-180" />
      </summary>
      <div className="border-t border-border/60 px-4 pb-4 pt-3">
        <div className="max-h-[26rem] overflow-y-auto overscroll-contain pr-3 [scrollbar-gutter:stable]">
          <div className="border-l border-border/70 pl-4 leading-6">
            {hasContent ? (
              <MarkdownContent
                className="text-muted-foreground"
                content={displayContent}
                compact
              />
            ) : (
              <div className="flex h-8 items-center gap-2 text-muted-foreground">
                <span>正在分析请求并组织步骤</span>
                <ThinkingDots />
              </div>
            )}
          </div>
        </div>
      </div>
    </details>
  );
}

function stripThoughtHeading(content: string): string {
  return content
    .replace(
      /^(?:#{1,6}\s*)?(?:[^\w\u4e00-\u9fff]{0,8}\s*)?(?:Durable Context Summary|思考过程|(?:我的)?(?:实际)?推理过程(?:（[^）]+）)?|流程回放|过程回放|隐藏步骤|Thinking|Reasoning)\s*[:：]?\s*\n+/iu,
      "",
    )
    .trim();
}

function ThinkingDots() {
  return (
    <span className="inline-flex items-center gap-1">
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          className="size-1.5 rounded-full bg-current opacity-60 animate-bounce"
          style={{ animationDelay: `${index * 120}ms` }}
        />
      ))}
    </span>
  );
}

function MessageAttachments({ files }: { files: MessageFile[] }) {
  return (
    <div className="flex max-w-full flex-wrap justify-end gap-2">
      {files.map((file) => (
        <div
          key={file.id}
          className="flex max-w-72 items-center gap-3 rounded-lg border border-border bg-card px-2 py-2 text-left shadow-sm"
        >
          {isImageFile(file) ? (
            <img
              src={resolveUploadRawUrl(file.id)}
              alt={displayFileName(file)}
              className="size-16 shrink-0 rounded-md object-cover"
            />
          ) : (
            <div className="grid size-10 shrink-0 place-items-center rounded-md bg-muted">
              <FileText className="size-5" />
            </div>
          )}
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">{displayFileName(file)}</div>
            {typeof file.size_bytes === "number" ? (
              <div className="text-xs text-muted-foreground">
                {formatFileSize(file.size_bytes)}
              </div>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}
