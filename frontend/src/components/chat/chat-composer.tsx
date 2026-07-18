import {
  type ChangeEvent,
  type CompositionEvent,
  type FormEvent,
  type KeyboardEvent,
  type RefObject,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import {
  type ChatMode,
  type ModelOptionRecord,
  type UploadedFileRecord,
} from "@/lib/chat-stream";

import {
  type ComposerContextUsage,
  type ComposerQueuedMessage,
  type ComposerTodo,
  ComposerActions,
  ComposerAttachments,
  ComposerContextMeter,
  ComposerError,
  ComposerQueue,
  ComposerTextarea,
  ComposerTodoPanel,
  ComposerTools,
  defaultAttachmentMessage,
  resizeComposerTextarea,
} from "./composer-parts";

type ChatComposerProps = {
  attachments: UploadedFileRecord[];
  todos: ComposerTodo[];
  todoListKey: string | null;
  contextUsage: ComposerContextUsage | null;
  error: string | null;
  fileInputRef: RefObject<HTMLInputElement | null>;
  isStreaming: boolean;
  isUploading: boolean;
  isLoadingModels: boolean;
  isRunSettingsLocked: boolean;
  modelOptions: ModelOptionRecord[];
  queuedMessages: ComposerQueuedMessage[];
  selectedMode: ChatMode;
  selectedModelName: string;
  selectedThinkingEnabled: boolean;
  onAttachFiles: () => void;
  onCancel: () => void;
  onClearError: () => void;
  onFileChange: (event: ChangeEvent<HTMLInputElement>) => void | Promise<void>;
  onPasteFiles: (files: File[]) => void | Promise<void>;
  onRemoveAttachment: (fileId: string) => void;
  onRemoveQueuedMessage: (messageId: string) => void;
  onModeChange: (mode: ChatMode) => void;
  onModelChange: (modelName: string) => void;
  onSendMessage: (message: string) => Promise<boolean>;
  onThinkingEnabledChange: (enabled: boolean) => void;
};


export function ChatComposer({
  attachments,
  todos,
  todoListKey,
  contextUsage,
  error,
  fileInputRef,
  isStreaming,
  isUploading,
  isLoadingModels,
  isRunSettingsLocked,
  modelOptions,
  queuedMessages,
  selectedMode,
  selectedModelName,
  selectedThinkingEnabled,
  onAttachFiles,
  onCancel,
  onClearError,
  onFileChange,
  onPasteFiles,
  onRemoveAttachment,
  onRemoveQueuedMessage,
  onModeChange,
  onModelChange,
  onSendMessage,
  onThinkingEnabledChange,
}: ChatComposerProps) {
  const [input, setInput] = useState("");
  const isComposingRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const canSend = (Boolean(input.trim()) || attachments.length > 0) && !isUploading;

  useLayoutEffect(() => {
    resizeComposerTextarea(textareaRef.current);
  }, [input]);

  function handleInputChange(event: ChangeEvent<HTMLTextAreaElement>) {
    const nextValue = event.target.value;
    setInput(nextValue);
    resizeComposerTextarea(event.currentTarget);
  }

  function handleCompositionStart() {
    isComposingRef.current = true;
  }

  function handleCompositionEnd(event: CompositionEvent<HTMLTextAreaElement>) {
    isComposingRef.current = false;
    const nextValue = event.currentTarget.value;
    setInput(nextValue);
    resizeComposerTextarea(event.currentTarget);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing || isComposingRef.current) {
      return;
    }

    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitCurrentInput();
    }
  }

  async function submitCurrentInput() {
    const text = input.trim() || defaultAttachmentMessage(attachments);
    if (!text || isUploading) {
      return;
    }

    setInput("");
    const accepted = await onSendMessage(text);
    if (!accepted) {
      setInput(text);
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
      <div className="mx-auto w-full max-w-[52rem]">
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

        <ComposerTodoPanel
          todos={todos}
          todoListKey={todoListKey}
        />

        <div className="relative z-10 overflow-hidden rounded-2xl border border-border bg-card shadow-[0_2px_12px_-6px_rgba(15,23,42,0.12)] transition-[border-color,box-shadow] duration-150 focus-within:border-ring/50 focus-within:shadow-[0_8px_30px_-14px_rgba(37,99,235,0.28)]">
          <div className="px-5 pb-2 pt-4 sm:px-6">
            <ComposerTextarea
              input={input}
              textareaRef={textareaRef}
              onCompositionEnd={handleCompositionEnd}
              onCompositionStart={handleCompositionStart}
              onInputChange={handleInputChange}
              onKeyDown={handleKeyDown}
              onPasteFiles={onPasteFiles}
            />
          </div>
          <div className="flex min-h-12 items-center justify-between gap-3 px-4 pb-3 sm:px-5">
            <div className="flex items-center gap-2">
              <ComposerTools
                disabled={isUploading}
                isUploading={isUploading}
                onAttachFiles={onAttachFiles}
              />
              <ComposerContextMeter usage={contextUsage} />
            </div>
            <ComposerActions
              canSend={canSend}
              isStreaming={isStreaming}
              isLoadingModels={isLoadingModels}
              isRunSettingsLocked={isRunSettingsLocked}
              modelOptions={modelOptions}
              selectedMode={selectedMode}
              selectedModelName={selectedModelName}
              selectedThinkingEnabled={selectedThinkingEnabled}
              onCancel={onCancel}
              onModeChange={onModeChange}
              onModelChange={onModelChange}
              onSend={() => void submitCurrentInput()}
              onThinkingEnabledChange={onThinkingEnabledChange}
            />
          </div>
        </div>
      </div>
    </form>
  );
}
