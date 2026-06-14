import {
  type RefObject,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Copy, FileText, List, Pencil, RotateCcw } from "lucide-react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { type ChatUiMessage } from "@/hooks/use-chat-stream";
import { cn } from "@/lib/utils";

import {
  formatFileSize,
  getMessageFiles,
  displayFileName,
  type MessageFile,
  normalizeMathForMarkdown,
} from "./chat-format";

type MessageListProps = {
  messages: ChatUiMessage[];
  messagesEndRef: RefObject<HTMLDivElement | null>;
  isStreaming: boolean;
  onCopyMessage: (content: string) => void;
  onEditLatestUserMessage: (messageId: string, content: string) => Promise<boolean>;
  onRetryLatestAssistantMessage: () => void;
};

type UserMessageNavItem = {
  id: string;
  index: number;
  content: string;
};

export function MessageList({
  messages,
  messagesEndRef,
  isStreaming,
  onCopyMessage,
  onEditLatestUserMessage,
  onRetryLatestAssistantMessage,
}: MessageListProps) {
  const scrollAreaRef = useRef<HTMLDivElement | null>(null);
  const userMessageRefs = useRef(new Map<string, HTMLElement>());
  const navigatorCloseTimerRef = useRef<number | null>(null);
  const [activeUserIndex, setActiveUserIndex] = useState(0);
  const [isNavigatorOpen, setIsNavigatorOpen] = useState(false);
  const [editingUserMessageId, setEditingUserMessageId] = useState<string | null>(null);
  const userMessages = useMemo<UserMessageNavItem[]>(() => {
    let index = 0;
    return messages.flatMap((message) => {
      if (message.role !== "user") {
        return [];
      }
      index += 1;
      return [{ id: message.id, index, content: message.content }];
    });
  }, [messages]);
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
    if (!viewport || userMessages.length === 0) {
      setActiveUserIndex(0);
      return;
    }

    const maxScrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
    if (viewport.scrollTop <= 4) {
      setActiveUserIndex(userMessages[0].index);
      return;
    }
    if (maxScrollTop - viewport.scrollTop <= 4) {
      setActiveUserIndex(userMessages[userMessages.length - 1].index);
      return;
    }

    const viewportRect = viewport.getBoundingClientRect();
    const targetY = viewportRect.top + viewportRect.height * 0.38;
    let nextIndex = userMessages[0].index;

    for (const item of userMessages) {
      const element = userMessageRefs.current.get(item.id);
      if (!element) {
        continue;
      }
      if (element.getBoundingClientRect().top <= targetY) {
        nextIndex = item.index;
      }
    }
    setActiveUserIndex((current) => (current === nextIndex ? current : nextIndex));
  }, [getViewport, userMessages]);

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
  }, [messages, updateActiveUserMessage]);

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
  userMessageRef?: (element: HTMLElement | null) => void;
}) {
  const isUser = message.role === "user";
  const files = getMessageFiles(message);
  const content = message.content || (message.status === "streaming" ? "..." : "");

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
            ) : (
              <MarkdownContent content={content} />
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
        ) : isLatestAssistant ? (
          <AssistantMessageActions
            content={content}
            disabled={isStreaming}
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
  disabled,
  onCopyMessage,
  onRetryLatestAssistantMessage,
}: {
  content: string;
  disabled: boolean;
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
        disabled={disabled}
        className="h-8 gap-1.5 px-2 text-xs"
        onClick={onRetryLatestAssistantMessage}
      >
        <RotateCcw className="size-4" />
        重试
      </Button>
    </div>
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

function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="slotflow-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { strict: false, throwOnError: false }]]}
      >
        {normalizeMathForMarkdown(content)}
      </ReactMarkdown>
    </div>
  );
}

function MessageAttachments({ files }: { files: MessageFile[] }) {
  return (
    <div className="flex max-w-full flex-wrap justify-end gap-2">
      {files.map((file) => (
        <div
          key={file.id}
          className="flex max-w-72 items-center gap-3 rounded-2xl border border-border bg-card px-3 py-2 text-left shadow-sm"
        >
          <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-muted">
            <FileText className="size-5" />
          </div>
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
