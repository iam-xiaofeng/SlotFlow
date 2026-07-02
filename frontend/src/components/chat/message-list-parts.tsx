import {
  type RefObject,
  memo,
  useEffect,
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

export type UserMessageNavItem = {
  id: string;
  index: number;
  content: string;
};

type AssistantContentParts = {
  thought: string;
  body: string;
};

function MessageBubbleImpl({
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
      : splitAssistantContent(
          content,
          message.reasoningContent,
        );
  const hasAssistantThought = Boolean(assistantContent.thought.trim());
  const hasAssistantBody = Boolean(assistantContent.body.trim());
  const isCompressingContext =
    !isUser &&
    !clarification &&
    message.status === "streaming" &&
    message.compressionStarted === true;
  const shouldShowThinkingCard =
    !isUser &&
    !clarification &&
    (hasAssistantThought ||
      (message.status === "streaming" &&
        message.thinkingStarted === true &&
        !hasAssistantBody &&
        !isCompressingContext));
  const isAssistantThinking =
    !isUser &&
    !clarification &&
    message.status === "streaming" &&
    !isCompressingContext &&
    !hasAssistantBody &&
    message.thinkingStarted === true &&
    !message.reasoningContent?.trim();
  const canShowAssistantActions =
    !clarification &&
    isLatestAssistant &&
    message.status === "done" &&
    hasAssistantBody;
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
            ) : isCompressingContext ? (
              <ContextCompressingIndicator />
            ) : isAssistantThinking ? (
              <AssistantThinkingSummary content="" isStreaming />
            ) : (
              <>
                {shouldShowThinkingCard ? (
                  <AssistantThinkingSummary
                    content={assistantContent.thought}
                    isStreaming={message.status === "streaming"}
                  />
                ) : null}
                {hasAssistantBody ? (
                  <SoftStreamingMarkdown
                    content={assistantContent.body}
                    isStreaming={message.status === "streaming"}
                  />
                ) : message.status === "streaming" && !shouldShowThinkingCard ? (
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
            content={assistantContent.body}
            onCopyMessage={onCopyMessage}
            onRetryLatestAssistantMessage={onRetryLatestAssistantMessage}
          />
        ) : null}
      </div>
    </article>
  );
}

// Memoize so a streaming delta on the latest message does NOT re-render every older bubble
// (each would otherwise re-run the heavy react-markdown pipeline). We compare only the
// props that affect output; callback identities are ignored since their behavior is stable.
export const MessageBubble = memo(MessageBubbleImpl, (prev, next) => {
  return (
    prev.message === next.message &&
    prev.isLatestUser === next.isLatestUser &&
    prev.isLatestAssistant === next.isLatestAssistant &&
    prev.isEditing === next.isEditing &&
    prev.isStreaming === next.isStreaming &&
    prev.userMessageRef === next.userMessageRef
  );
});

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

function ContextCompressingIndicator() {
  return (
    <div className="flex h-8 items-center gap-2 text-sm text-muted-foreground">
      <span>正在压缩上下文</span>
      <ThinkingDots />
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

export function MessageNavigator({
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
    return {
      thought: reasoningContent.trim(),
      body: content.trim(),
    };
  }

  return {
    thought: "",
    body: content.trim(),
  };
}

export function assistantMessageHasOutput(message: ChatUiMessage): boolean {
  return Boolean(message.content.trim() || message.reasoningContent?.trim());
}

function SoftStreamingMarkdown({
  className,
  compact = false,
  content,
  isStreaming,
}: {
  className?: string;
  compact?: boolean;
  content: string;
  isStreaming: boolean;
}) {
  const [isSoft, setIsSoft] = useState(false);
  const lastContentRef = useRef(content);

  useEffect(() => {
    if (!isStreaming) {
      lastContentRef.current = content;
      setIsSoft(false);
      return;
    }
    if (content === lastContentRef.current) {
      return;
    }

    lastContentRef.current = content;
    setIsSoft(true);
    const timer = window.setTimeout(() => setIsSoft(false), 220);
    return () => window.clearTimeout(timer);
  }, [content, isStreaming]);

  // While fresh tokens arrive, render slightly faded; settle to full opacity so new text
  // reads as fading in from light to dark instead of a hard pop. motion-reduce keeps it crisp.
  return (
    <MarkdownContent
      className={cn(
        "transition-opacity duration-300 ease-out motion-reduce:transition-none",
        isSoft ? "opacity-70" : "opacity-100",
        className,
      )}
      compact={compact}
      content={content}
    />
  );
}

function AssistantThinkingSummary({
  content,
  isStreaming = false,
}: {
  content: string;
  isStreaming?: boolean;
}) {
  const displayContent = content.trim();
  const hasContent = Boolean(displayContent.trim());
  const steps = splitThinkingSteps(displayContent);

  return (
    <details
      className="group/thinking mb-5 w-full overflow-hidden rounded-lg border border-border/70 bg-background text-sm text-muted-foreground"
      open
    >
      <summary className="flex h-9 cursor-pointer list-none items-center gap-2 px-3 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground [&::-webkit-details-marker]:hidden">
        <ChevronDown className="size-3.5 transition-transform group-open/thinking:rotate-180" />
        <span>{isStreaming && !hasContent ? "思考中" : "隐藏步骤"}</span>
      </summary>
      <div className="px-4 pb-4 pt-1">
        <div className="max-h-[30rem] overflow-y-auto overscroll-contain pr-3 [scrollbar-gutter:stable]">
          {hasContent ? (
            <ol className="space-y-3">
              {steps.map((step, index) => (
                <li key={`${index}:${step.slice(0, 24)}`} className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-3">
                  <span className="relative flex justify-center">
                    <span className="grid size-4 place-items-center rounded-sm border border-border bg-background text-muted-foreground">
                      <Lightbulb className="size-3" />
                    </span>
                    {index < steps.length - 1 ? (
                      <span className="absolute top-5 bottom-[-0.75rem] w-px bg-border" />
                    ) : null}
                  </span>
                  <SoftStreamingMarkdown
                    className="min-w-0 text-[0.82rem] leading-6 text-muted-foreground"
                    content={step}
                    compact
                    isStreaming={isStreaming && index === steps.length - 1}
                  />
                </li>
              ))}
            </ol>
          ) : (
            <div className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-3">
              <span className="grid size-4 place-items-center rounded-sm border border-border bg-background text-muted-foreground">
                <Lightbulb className="size-3" />
              </span>
              <div className="flex min-h-8 items-center gap-2 text-[0.82rem] leading-6 text-muted-foreground">
                <span>{isStreaming ? "思考中..." : "已完成思考，模型未返回可展示的思考内容。"}</span>
                {isStreaming ? <ThinkingDots /> : null}
              </div>
            </div>
          )}
        </div>
      </div>
    </details>
  );
}

function splitThinkingSteps(content: string): string[] {
  const normalized = content.trim();
  if (!normalized) {
    return [];
  }

  const paragraphSteps = normalized
    .split(/\n{2,}/)
    .map((step) => step.trim())
    .filter(Boolean);
  if (paragraphSteps.length > 1) {
    return paragraphSteps;
  }

  const lineSteps = normalized
    .split(/\n(?=(?:[-*]\s+|\d+[.)、]\s+|#{1,6}\s+))/u)
    .map((step) => step.trim())
    .filter(Boolean);
  return lineSteps.length > 1 ? lineSteps : [normalized];
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
