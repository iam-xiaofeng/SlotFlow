import {
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  type RefObject,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ArrowUp,
  Brain,
  FileText,
  Folder,
  Globe2,
  ImageIcon,
  LoaderCircle,
  Mic,
  MoreHorizontal,
  Paperclip,
  Plus,
  Square,
  Telescope,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Textarea } from "@/components/ui/textarea";
import { type UploadedFileRecord } from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

import { displayFileName, formatFileSize } from "./chat-format";

type ChatComposerProps = {
  attachments: UploadedFileRecord[];
  error: string | null;
  fileInputRef: RefObject<HTMLInputElement | null>;
  isStreaming: boolean;
  isUploading: boolean;
  queuedMessages: ComposerQueuedMessage[];
  onAttachFiles: () => void;
  onCancel: () => void;
  onClearError: () => void;
  onFileChange: (event: ChangeEvent<HTMLInputElement>) => void | Promise<void>;
  onRemoveAttachment: (fileId: string) => void;
  onRemoveQueuedMessage: (messageId: string) => void;
  onSendMessage: (message: string) => Promise<boolean>;
};

export type ComposerQueuedMessage = {
  id: string;
  text: string;
  attachmentCount: number;
  position: number;
};

export function ChatComposer({
  attachments,
  error,
  fileInputRef,
  isStreaming,
  isUploading,
  queuedMessages,
  onAttachFiles,
  onCancel,
  onClearError,
  onFileChange,
  onRemoveAttachment,
  onRemoveQueuedMessage,
  onSendMessage,
}: ChatComposerProps) {
  const [input, setInput] = useState("");
  const [isExpanded, setIsExpanded] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const canSend = Boolean(input.trim()) && !isUploading;

  function handleInputChange(event: ChangeEvent<HTMLTextAreaElement>) {
    const nextValue = event.target.value;
    setInput(nextValue);
    const nextExpanded = shouldUseExpandedComposer(nextValue);
    setIsExpanded((current) => (current === nextExpanded ? current : nextExpanded));
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing) {
      return;
    }

    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitCurrentInput();
    }
  }

  async function submitCurrentInput() {
    const text = input.trim();
    if (!text || isUploading) {
      return;
    }

    setInput("");
    setIsExpanded(false);
    const accepted = await onSendMessage(text);
    if (!accepted) {
      setInput(text);
      setIsExpanded(shouldUseExpandedComposer(text));
    }
  }

  return (
    <form
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        void submitCurrentInput();
      }}
      className="w-full"
    >
      <div className="mx-auto w-full max-w-3xl">
        {error ? (
          <ComposerError message={error} onDismiss={onClearError} />
        ) : null}

        {queuedMessages.length > 0 ? (
          <ComposerQueue
            messages={queuedMessages}
            onRemoveQueuedMessage={onRemoveQueuedMessage}
          />
        ) : null}

        {attachments.length > 0 ? (
          <ComposerAttachments
            files={attachments}
            onRemoveAttachment={onRemoveAttachment}
          />
        ) : null}

        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(event) => void onFileChange(event)}
        />

        <div
          className={cn(
            "rounded-3xl border border-input bg-background shadow-sm",
            isExpanded ? "px-4 py-3" : "flex min-h-14 items-center gap-2 px-3 py-2",
          )}
        >
          {isExpanded ? (
            <>
              <ComposerTextarea
                input={input}
                textareaRef={textareaRef}
                onInputChange={handleInputChange}
                onKeyDown={handleKeyDown}
              />
              <div className="mt-3 flex items-center justify-between gap-2">
                <ComposerTools
                  disabled={isUploading}
                  isUploading={isUploading}
                  onAttachFiles={onAttachFiles}
                />
                <ComposerActions
                  canSend={canSend}
                  isStreaming={isStreaming}
                  onCancel={onCancel}
                  onSend={() => void submitCurrentInput()}
                />
              </div>
            </>
          ) : (
            <>
              <ComposerTools
                compact
                disabled={isUploading}
                isUploading={isUploading}
                onAttachFiles={onAttachFiles}
              />
              <ComposerTextarea
                compact
                input={input}
                textareaRef={textareaRef}
                onInputChange={handleInputChange}
                onKeyDown={handleKeyDown}
              />
              <ComposerActions
                canSend={canSend}
                isStreaming={isStreaming}
                onCancel={onCancel}
                onSend={() => void submitCurrentInput()}
              />
            </>
          )}
        </div>
      </div>
    </form>
  );
}

function ComposerQueue({
  messages,
  onRemoveQueuedMessage,
}: {
  messages: ComposerQueuedMessage[];
  onRemoveQueuedMessage: (messageId: string) => void;
}) {
  return (
    <div className="mb-3 flex max-h-36 flex-col gap-1 overflow-y-auto rounded-2xl border bg-muted/30 p-2">
      {messages.map((message) => (
        <div
          key={message.id}
          className="flex min-w-0 items-center gap-2 rounded-xl px-2 py-1.5 text-sm"
        >
          <span className="shrink-0 text-xs text-muted-foreground">
            {message.position}
          </span>
          <span className="min-w-0 flex-1 truncate" title={message.text}>
            {message.text}
          </span>
          {message.attachmentCount > 0 ? (
            <span className="shrink-0 text-xs text-muted-foreground">
              {message.attachmentCount} 个附件
            </span>
          ) : null}
          <span className="shrink-0 text-xs text-muted-foreground">排队中</span>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            title="移出队列"
            onClick={() => onRemoveQueuedMessage(message.id)}
          >
            <X className="size-4" />
            <span className="sr-only">移出队列</span>
          </Button>
        </div>
      ))}
    </div>
  );
}

function ComposerError({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss: () => void;
}) {
  return (
    <div className="mb-3 flex items-start justify-between gap-3 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-[0.95rem] text-destructive">
      <span className="min-w-0 break-words">{message}</span>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        title="Dismiss error"
        onClick={onDismiss}
      >
        <X className="size-4" />
        <span className="sr-only">Dismiss error</span>
      </Button>
    </div>
  );
}

function ComposerAttachments({
  files,
  onRemoveAttachment,
}: {
  files: UploadedFileRecord[];
  onRemoveAttachment: (fileId: string) => void;
}) {
  return (
    <div className="mb-3 flex flex-wrap gap-2">
      {files.map((file) => (
        <Badge
          key={file.id}
          variant="outline"
          className="h-8 max-w-full gap-1.5 rounded-md pr-1"
        >
          <FileText className="size-4 shrink-0" />
          <span className="max-w-52 truncate">{displayFileName(file)}</span>
          <span className="text-muted-foreground">
            {formatFileSize(file.size_bytes)}
          </span>
          <button
            type="button"
            className="grid size-5 place-items-center rounded-sm hover:bg-muted"
            title="Remove file"
            onClick={() => onRemoveAttachment(file.id)}
          >
            <X className="size-4" />
            <span className="sr-only">Remove file</span>
          </button>
        </Badge>
      ))}
    </div>
  );
}

function ComposerTextarea({
  compact = false,
  input,
  textareaRef,
  onInputChange,
  onKeyDown,
}: {
  compact?: boolean;
  input: string;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  onInputChange: (event: ChangeEvent<HTMLTextAreaElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
}) {
  return (
    <Textarea
      ref={textareaRef}
      value={input}
      onChange={onInputChange}
      rows={1}
      placeholder="有问题，尽管问"
      onKeyDown={onKeyDown}
      wrap="soft"
      className={cn(
        "max-h-[min(40dvh,12rem)] min-h-8 min-w-0 resize-none overflow-y-auto border-0 bg-transparent px-0 py-0 text-lg leading-7 shadow-none [overflow-wrap:anywhere] focus-visible:ring-0",
        compact && "flex-1 break-words",
      )}
    />
  );
}

function shouldUseExpandedComposer(value: string) {
  return value.includes("\n") || value.length > 48;
}

type ComposerToolsProps = {
  compact?: boolean;
  disabled: boolean;
  isUploading: boolean;
  onAttachFiles: () => void;
};

function ComposerTools({
  compact = false,
  disabled,
  isUploading,
  onAttachFiles,
}: ComposerToolsProps) {
  return (
    <div className={cn("flex items-center gap-1", compact && "shrink-0")}>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              type="button"
              variant="ghost"
              size="icon"
              disabled={disabled}
              className="size-10 rounded-full"
            />
          }
        >
          {isUploading ? (
            <LoaderCircle className="size-6 animate-spin" />
          ) : (
            <Plus className="size-6" />
          )}
          <span className="sr-only">打开添加菜单</span>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          side="top"
          align="start"
          sideOffset={10}
          className="w-72 rounded-2xl p-2"
        >
          <DropdownMenuItem onClick={onAttachFiles} className="gap-3 px-3 py-2.5">
            <Paperclip className="size-5" />
            添加照片和文件
          </DropdownMenuItem>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger className="gap-3 px-3 py-2.5">
              <FileText className="size-5" />
              近期文件
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="w-48">
              <DropdownMenuItem disabled>暂无近期文件</DropdownMenuItem>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled className="gap-3 px-3 py-2.5">
            <ImageIcon className="size-5" />
            创建图片
          </DropdownMenuItem>
          <DropdownMenuItem disabled className="gap-3 px-3 py-2.5">
            <Brain className="size-5" />
            思考一下
          </DropdownMenuItem>
          <DropdownMenuItem disabled className="gap-3 px-3 py-2.5">
            <Telescope className="size-5" />
            深度研究
          </DropdownMenuItem>
          <DropdownMenuItem disabled className="gap-3 px-3 py-2.5">
            <Globe2 className="size-5" />
            网页搜索
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled className="gap-3 px-3 py-2.5">
            <MoreHorizontal className="size-5" />
            更多
          </DropdownMenuItem>
          <DropdownMenuItem disabled className="gap-3 px-3 py-2.5">
            <Folder className="size-5" />
            项目
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

type ComposerActionsProps = {
  canSend: boolean;
  isStreaming: boolean;
  onCancel: () => void;
  onSend: () => void;
};

function ComposerActions({
  canSend,
  isStreaming,
  onCancel,
  onSend,
}: ComposerActionsProps) {
  return (
    <div className="flex shrink-0 items-center gap-1">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        title="语音输入"
        className="size-10 rounded-full"
      >
        <Mic className="size-6" />
        <span className="sr-only">语音输入</span>
      </Button>

      {isStreaming ? (
        <>
          {canSend ? (
            <Button
              type="button"
              size="icon"
              title="加入队列"
              disabled={!canSend}
              onClick={onSend}
              className="size-11 rounded-full bg-foreground text-background hover:bg-foreground/90 disabled:bg-muted disabled:text-muted-foreground"
            >
              <ArrowUp className="size-6" />
              <span className="sr-only">加入队列</span>
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            title="停止"
            onClick={onCancel}
            className="size-11 rounded-full bg-muted text-foreground hover:bg-muted/80"
          >
            <Square className="size-4 fill-current" />
            <span className="sr-only">停止</span>
          </Button>
        </>
      ) : (
        <Button
          type="button"
          size="icon"
          title="发送"
          disabled={!canSend}
          onClick={onSend}
          className="size-11 rounded-full bg-foreground text-background hover:bg-foreground/90 disabled:bg-muted disabled:text-muted-foreground"
        >
          <ArrowUp className="size-6" />
          <span className="sr-only">发送</span>
        </Button>
      )}
    </div>
  );
}
