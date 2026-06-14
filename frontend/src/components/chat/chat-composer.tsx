import {
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  type RefObject,
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
  canSend: boolean;
  error: string | null;
  fileInputRef: RefObject<HTMLInputElement | null>;
  input: string;
  isExpanded: boolean;
  isStreaming: boolean;
  isUploading: boolean;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  onAttachFiles: () => void;
  onCancel: () => void;
  onClearError: () => void;
  onFileChange: (event: ChangeEvent<HTMLInputElement>) => void | Promise<void>;
  onInputChange: (event: ChangeEvent<HTMLTextAreaElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onRemoveAttachment: (fileId: string) => void;
  onSend: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void | Promise<void>;
};

export function ChatComposer({
  attachments,
  canSend,
  error,
  fileInputRef,
  input,
  isExpanded,
  isStreaming,
  isUploading,
  textareaRef,
  onAttachFiles,
  onCancel,
  onClearError,
  onFileChange,
  onInputChange,
  onKeyDown,
  onRemoveAttachment,
  onSend,
  onSubmit,
}: ChatComposerProps) {
  return (
    <form onSubmit={(event) => void onSubmit(event)} className="w-full">
      <div className="mx-auto w-full max-w-3xl">
        {error ? (
          <ComposerError message={error} onDismiss={onClearError} />
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
                isStreaming={isStreaming}
                textareaRef={textareaRef}
                onInputChange={onInputChange}
                onKeyDown={onKeyDown}
              />
              <div className="mt-3 flex items-center justify-between gap-2">
                <ComposerTools
                  disabled={isStreaming || isUploading}
                  isUploading={isUploading}
                  onAttachFiles={onAttachFiles}
                />
                <ComposerActions
                  canSend={canSend}
                  isStreaming={isStreaming}
                  onCancel={onCancel}
                  onSend={onSend}
                />
              </div>
            </>
          ) : (
            <>
              <ComposerTools
                compact
                disabled={isStreaming || isUploading}
                isUploading={isUploading}
                onAttachFiles={onAttachFiles}
              />
              <ComposerTextarea
                compact
                input={input}
                isStreaming={isStreaming}
                textareaRef={textareaRef}
                onInputChange={onInputChange}
                onKeyDown={onKeyDown}
              />
              <ComposerActions
                canSend={canSend}
                isStreaming={isStreaming}
                onCancel={onCancel}
                onSend={onSend}
              />
            </>
          )}
        </div>
      </div>
    </form>
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
  isStreaming,
  textareaRef,
  onInputChange,
  onKeyDown,
}: {
  compact?: boolean;
  input: string;
  isStreaming: boolean;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  onInputChange: (event: ChangeEvent<HTMLTextAreaElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
}) {
  return (
    <Textarea
      ref={textareaRef}
      value={input}
      onChange={onInputChange}
      disabled={isStreaming}
      rows={1}
      placeholder="有问题，尽管问"
      onKeyDown={onKeyDown}
      className={cn(
        "max-h-44 min-h-8 resize-none overflow-y-auto border-0 bg-transparent px-0 py-0 text-lg leading-7 shadow-none focus-visible:ring-0",
        compact && "flex-1",
      )}
    />
  );
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
