import {
  type ChangeEvent,
  type ClipboardEvent,
  type CompositionEvent,
  type KeyboardEvent,
  type RefObject,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  type LucideIcon,
  ArrowUp,
  Brain,
  Check,
  CheckCircle2,
  ChevronRight,
  ChevronDown,
  Circle,
  FileText,
  Folder,
  Globe2,
  ImageIcon,
  ListTodo,
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
import {
  type ChatMode,
  type ModelOptionRecord,
  type UploadedFileRecord,
  resolveUploadRawUrl,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

import { displayFileName, formatFileSize, isImageFile } from "./chat-format";


export type ComposerQueuedMessage = {
  id: string;
  text: string;
  attachmentCount: number;
  position: number;
};

export type ComposerTodo = {
  content: string;
  status: "pending" | "in_progress" | "completed";
};

export const MODE_OPTIONS: Record<ChatMode, { label: string; description: string }> = {
  flash: {
    label: "Flash",
    description: "快速响应，适合简单问答。",
  },
  pro: {
    label: "Pro",
    description: "默认模式，兼顾速度与质量。",
  },
  ultra: {
    label: "Ultra",
    description: "更强推理，适合复杂任务。",
  },
};


export function ComposerTodoPanel({
  todos,
  todoRevision,
  isStreaming,
}: {
  todos: ComposerTodo[];
  todoRevision: number;
  isStreaming: boolean;
}) {
  const [isCollapsed, setIsCollapsed] = useState(true);
  const previousTodoRevisionRef = useRef(todoRevision);
  const previousStreamingRef = useRef(isStreaming);

  useEffect(() => {
    if (previousTodoRevisionRef.current !== todoRevision && todos.length > 0) {
      setIsCollapsed(false);
    }
    previousTodoRevisionRef.current = todoRevision;
  }, [todoRevision, todos.length]);

  useEffect(() => {
    if (previousStreamingRef.current && !isStreaming && todos.length > 0) {
      setIsCollapsed(true);
    }
    previousStreamingRef.current = isStreaming;
  }, [isStreaming, todos.length]);

  if (todos.length === 0) {
    return null;
  }

  const completedCount = todos.filter((todo) => todo.status === "completed").length;

  return (
    <div className="relative z-0 mx-3 -mb-px overflow-hidden rounded-t-2xl border border-b-0 border-border/80 bg-muted/35 shadow-sm backdrop-blur-sm transition-all duration-200 ease-out">
      <button
        type="button"
        aria-expanded={!isCollapsed}
        className="flex min-h-9 w-full items-center justify-between gap-3 px-4 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-muted/50 sm:px-5"
        onClick={() => setIsCollapsed((current) => !current)}
      >
        <span className="flex min-w-0 items-center gap-2">
          <ListTodo className="size-4 shrink-0" />
          <span className="truncate">To-dos</span>
        </span>
        <span className="flex shrink-0 items-center gap-2 text-xs">
          <span>
            {completedCount}/{todos.length}
          </span>
          <ChevronDown
            className={cn(
              "size-4 transition-transform",
              !isCollapsed && "rotate-180",
            )}
          />
        </span>
      </button>
      <div
        className={cn(
          "overflow-hidden bg-background/90 transition-[max-height,padding] duration-200 ease-out",
          isCollapsed
            ? "max-h-0 px-4 py-0 sm:px-5"
            : "max-h-32 overflow-y-auto px-4 pb-3 pt-2 sm:px-5",
        )}
      >
        <ol className="flex flex-col gap-1.5">
          {todos.map((todo, index) => (
            <li
              key={`${index}:${todo.content}`}
              className="flex min-w-0 items-start gap-2 text-sm leading-5"
            >
              <TodoStatusIcon status={todo.status} />
              <span
                className={cn(
                  "min-w-0 flex-1",
                  todo.status === "completed"
                    ? "text-muted-foreground line-through"
                    : todo.status === "in_progress"
                      ? "text-foreground"
                      : "text-muted-foreground",
                )}
              >
                {todo.content}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function TodoStatusIcon({ status }: { status: ComposerTodo["status"] }) {
  if (status === "completed") {
    return <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" />;
  }
  if (status === "in_progress") {
    return <LoaderCircle className="mt-0.5 size-4 shrink-0 animate-spin text-primary" />;
  }
  return <Circle className="mt-0.5 size-4 shrink-0 text-muted-foreground/70" />;
}

export function ComposerQueue({
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

export function ComposerError({
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

export function ComposerAttachments({
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
          className="h-auto max-w-full gap-2 rounded-md py-1 pl-1 pr-1.5"
        >
          {isImageFile(file) ? (
            <img
              src={resolveUploadRawUrl(file.id)}
              alt={displayFileName(file)}
              className="size-9 rounded object-cover"
            />
          ) : (
            <span className="grid size-8 place-items-center rounded bg-muted">
              <FileText className="size-4 shrink-0" />
            </span>
          )}
          <span className="max-w-44 truncate">{displayFileName(file)}</span>
          <span className="shrink-0 text-muted-foreground">
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

export function ComposerTextarea({
  input,
  textareaRef,
  onCompositionEnd,
  onCompositionStart,
  onInputChange,
  onKeyDown,
  onPasteFiles,
}: {
  input: string;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  onCompositionEnd: (event: CompositionEvent<HTMLTextAreaElement>) => void;
  onCompositionStart: () => void;
  onInputChange: (event: ChangeEvent<HTMLTextAreaElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onPasteFiles: (files: File[]) => void | Promise<void>;
}) {
  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const imageFiles = extractClipboardImageFiles(event);
    if (imageFiles.length === 0) {
      return;
    }
    event.preventDefault();
    void onPasteFiles(imageFiles);
  }

  return (
    <Textarea
      ref={textareaRef}
      value={input}
      onChange={onInputChange}
      onCompositionEnd={onCompositionEnd}
      onCompositionStart={onCompositionStart}
      rows={1}
      placeholder="有问题，尽管问"
      onKeyDown={onKeyDown}
      onPaste={handlePaste}
      wrap="soft"
      className="block max-h-[min(40dvh,12rem)] min-h-8 w-full min-w-0 resize-none overflow-hidden border-0 bg-transparent px-0 py-0 !text-lg !leading-7 shadow-none placeholder:text-muted-foreground/75 [field-sizing:fixed] [overflow-wrap:anywhere] focus-visible:ring-0"
    />
  );
}

export function resizeComposerTextarea(element: HTMLTextAreaElement | null) {
  if (!element) {
    return;
  }

  const maxHeight = Math.min(window.innerHeight * 0.4, 192);
  element.style.height = "auto";
  const nextHeight = Math.min(element.scrollHeight, maxHeight);
  element.style.height = `${Math.max(nextHeight, 32)}px`;
  element.style.overflowY = element.scrollHeight > maxHeight ? "auto" : "hidden";
}

function extractClipboardImageFiles(event: ClipboardEvent<HTMLTextAreaElement>): File[] {
  const items = Array.from(event.clipboardData.items ?? []);
  return items.flatMap((item, index) => {
    if (item.kind !== "file" || !item.type.startsWith("image/")) {
      return [];
    }
    const file = item.getAsFile();
    if (!file) {
      return [];
    }
    if (file.name) {
      return [file];
    }
    const suffix = item.type.split("/")[1] || "png";
    return [new File([file], `pasted-image-${index + 1}.${suffix}`, { type: item.type })];
  });
}

export function defaultAttachmentMessage(files: UploadedFileRecord[]) {
  if (files.length === 0) {
    return "";
  }
  if (files.every(isImageFile)) {
    return files.length === 1 ? "请查看这张图片。" : "请查看这些图片。";
  }
  return "请查看这些附件。";
}

export function ComposerPromptChip({
  icon: Icon,
  label,
}: {
  icon: LucideIcon;
  label: string;
}) {
  return (
    <button
      type="button"
      className="inline-flex h-10 items-center gap-2 rounded-xl border border-border/80 bg-background px-3 text-sm font-medium text-foreground shadow-sm transition-colors hover:bg-muted/60"
    >
      <Icon className="size-4 text-muted-foreground" />
      {label}
    </button>
  );
}

type ComposerToolsProps = {
  disabled: boolean;
  isUploading: boolean;
  onAttachFiles: () => void;
};

export function ComposerTools({
  disabled,
  isUploading,
  onAttachFiles,
}: ComposerToolsProps) {
  return (
    <div className="flex items-center gap-1">
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              type="button"
              variant="ghost"
              size="icon"
              disabled={disabled}
              className="size-10 rounded-full text-foreground hover:bg-muted"
            />
          }
        >
          {isUploading ? (
            <LoaderCircle className="size-5 animate-spin" />
          ) : (
            <Plus className="size-5" />
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
  isLoadingModels: boolean;
  isRunSettingsLocked: boolean;
  modelOptions: ModelOptionRecord[];
  selectedMode: ChatMode;
  selectedModelName: string;
  selectedThinkingEnabled: boolean;
  onCancel: () => void;
  onModeChange: (mode: ChatMode) => void;
  onModelChange: (modelName: string) => void;
  onSend: () => void;
  onThinkingEnabledChange: (enabled: boolean) => void;
};

export function ComposerActions({
  canSend,
  isStreaming,
  isLoadingModels,
  isRunSettingsLocked,
  modelOptions,
  selectedMode,
  selectedModelName,
  selectedThinkingEnabled,
  onCancel,
  onModeChange,
  onModelChange,
  onSend,
  onThinkingEnabledChange,
}: ComposerActionsProps) {
  return (
    <div className="flex min-w-0 shrink-0 items-center gap-1.5">
      <ComposerModelSelect
        value={selectedModelName}
        options={modelOptions}
        disabled={isStreaming || isRunSettingsLocked}
        isLoading={isLoadingModels}
        mode={selectedMode}
        thinkingEnabled={selectedThinkingEnabled}
        onModeChange={onModeChange}
        onThinkingEnabledChange={onThinkingEnabledChange}
        onChange={onModelChange}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon"
        title="语音输入"
        className="size-9 rounded-full text-foreground hover:bg-muted"
      >
        <Mic className="size-5" />
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
              className="size-10 rounded-full bg-foreground text-background hover:bg-foreground/90 disabled:bg-muted disabled:text-muted-foreground"
            >
              <ArrowUp className="size-5" />
              <span className="sr-only">加入队列</span>
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            title="停止"
            onClick={onCancel}
            className="size-10 rounded-full bg-muted text-foreground hover:bg-muted/80"
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
          className="size-10 rounded-full bg-foreground text-background hover:bg-foreground/90 disabled:bg-muted disabled:text-muted-foreground"
        >
          <ArrowUp className="size-5" />
          <span className="sr-only">发送</span>
        </Button>
      )}
    </div>
  );
}

function ComposerModelSelect({
  value,
  options,
  disabled,
  isLoading,
  mode,
  thinkingEnabled,
  onChange,
  onModeChange,
  onThinkingEnabledChange,
}: {
  value: string;
  options: ModelOptionRecord[];
  disabled: boolean;
  isLoading: boolean;
  mode: ChatMode;
  thinkingEnabled: boolean;
  onChange: (modelName: string) => void;
  onModeChange: (mode: ChatMode) => void;
  onThinkingEnabledChange: (enabled: boolean) => void;
}) {
  const hasOptions = options.length > 0;
  const activeModel = options.find((model) => model.id === value);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        type="button"
        disabled={disabled || !hasOptions}
        title={disabled ? "当前对话已开始，模型和模式已锁定" : "选择模型、模式和思考开关"}
        className="inline-flex h-10 min-w-0 max-w-[17rem] items-center justify-center gap-1.5 rounded-xl bg-muted px-3 text-sm font-medium outline-none transition-colors hover:bg-muted/80 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-60 aria-expanded:bg-muted/80 sm:max-w-[19rem]"
      >
        <span className="min-w-0 truncate">
          {hasOptions
            ? shortModelName(activeModel?.id ?? value)
            : isLoading
              ? "加载中…"
              : "无可用模型"}
        </span>
        <span className="shrink-0 text-muted-foreground">{MODE_OPTIONS[mode].label}</span>
        <span className="shrink-0 text-muted-foreground">
          {thinkingEnabled ? "Thinking" : "No thinking"}
        </span>
        <ChevronDownIcon />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        side="top"
        align="end"
        sideOffset={8}
        className="w-[25rem] rounded-2xl p-2 shadow-xl"
      >
        <div className="flex max-h-72 flex-col gap-1 overflow-y-auto">
          {options.map((model) => (
            <DropdownMenuItem
              key={`${model.provider}:${model.id}`}
              className="grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-xl px-3 py-2"
              onClick={() => onChange(model.id)}
            >
              <span className="min-w-0">
                <span className="block truncate font-medium">{shortModelName(model.id)}</span>
                <span className="block truncate text-sm text-muted-foreground">
                  {modelDescription(model)}
                </span>
              </span>
              {model.id === value ? <Check className="size-4 text-blue-600" /> : null}
            </DropdownMenuItem>
          ))}
        </div>
        {isLoading ? (
          <p className="px-3 pb-1 pt-2 text-xs text-muted-foreground">正在刷新模型列表</p>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuSub>
          <DropdownMenuSubTrigger className="min-h-11 rounded-xl px-3">
            <span className="flex-1">Effort</span>
            <span className="text-muted-foreground">{MODE_OPTIONS[mode].label}</span>
            <ChevronRight className="size-4 text-muted-foreground" />
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent
            sideOffset={10}
            className="w-[25rem] rounded-2xl p-3 shadow-xl"
          >
            <p className="mb-3 px-1 text-sm leading-5 text-muted-foreground">
              选择本轮任务的执行模式，控制规划和子 agent 能力。
            </p>
            {(["flash", "pro", "ultra"] as ChatMode[]).map((nextMode) => (
              <DropdownMenuItem
                key={nextMode}
                title={MODE_OPTIONS[nextMode].description}
                className="grid min-h-[4.25rem] grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-xl px-3 py-2"
                onClick={() => onModeChange(nextMode)}
              >
                <span className="min-w-0">
                  <span className="flex items-center gap-2 font-medium">
                    {MODE_OPTIONS[nextMode].label}
                    {nextMode === "pro" ? (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                        Default
                      </span>
                    ) : null}
                  </span>
                  <span className="mt-0.5 block truncate whitespace-nowrap text-sm leading-5 text-muted-foreground">
                    {MODE_OPTIONS[nextMode].description}
                  </span>
                </span>
                {nextMode === mode ? <Check className="size-4 text-blue-600" /> : null}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <button
              type="button"
              className="flex w-full items-center justify-between gap-4 rounded-xl px-3 py-2 text-left transition-colors hover:bg-muted"
              onClick={(event) => {
                event.preventDefault();
                onThinkingEnabledChange(!thinkingEnabled);
              }}
            >
              <span>
                <span className="block text-sm font-medium">Thinking</span>
                <span className="block text-sm text-muted-foreground">
                  开启后请求模型原生思考；关闭后不传 thinking 参数。
                </span>
              </span>
              <span
                className={
                  thinkingEnabled
                    ? "relative h-6 w-11 shrink-0 rounded-full bg-blue-600"
                    : "relative h-6 w-11 shrink-0 rounded-full bg-muted"
                }
              >
                <span
                  className={
                    thinkingEnabled
                      ? "absolute right-1 top-1 size-4 rounded-full bg-white shadow-sm"
                      : "absolute left-1 top-1 size-4 rounded-full bg-background shadow-sm"
                  }
                />
              </span>
            </button>
          </DropdownMenuSubContent>
        </DropdownMenuSub>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ChevronDownIcon() {
  return <ChevronRight className="size-4 rotate-90 text-muted-foreground" />;
}

function shortModelName(modelName: string) {
  return modelName
    .replace(/^deepseek[-_]/i, "")
    .replace(/^claude[-_]/i, "")
    .replace(/^gpt[-_]/i, "GPT ")
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function modelDescription(model: ModelOptionRecord) {
  if (model.provider === "deepseek") {
    return model.id.includes("flash") ? "Fastest for quick answers" : "Efficient for everyday tasks";
  }
  if (model.provider === "anthropic") {
    return "For careful writing and reasoning";
  }
  if (model.provider === "custom") {
    return "Served via your custom endpoint";
  }
  return "General-purpose reasoning model";
}
