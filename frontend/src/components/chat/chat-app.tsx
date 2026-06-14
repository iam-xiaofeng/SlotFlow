"use client";

import {
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

import {
  Sidebar,
  SidebarInset,
  SidebarProvider,
} from "@/components/ui/sidebar";
import { useChatStream } from "@/hooks/use-chat-stream";
import {
  type ChatMode,
  type ThreadRecord,
  type UploadedFileRecord,
  type WorkspaceEntryRecord,
  listArtifacts,
  listThreads,
  uploadFile,
} from "@/lib/chat-stream";

import { ChatComposer } from "./chat-composer";
import { makeThreadTitle } from "./chat-format";
import { ThreadSidebar, UserMenu } from "./chat-sidebar";
import { EmptyState, MessageList } from "./message-list";

const defaultModelName = "deepseek-v4-flash";
const defaultMode: ChatMode = "pro";

export function ChatApp() {
  const [input, setInput] = useState("");
  const [threads, setThreads] = useState<ThreadRecord[]>([]);
  const [threadQuery, setThreadQuery] = useState("");
  const [threadListError, setThreadListError] = useState<string | null>(null);
  const [isLoadingThreads, setIsLoadingThreads] = useState(true);
  const [attachments, setAttachments] = useState<UploadedFileRecord[]>([]);
  const [artifacts, setArtifacts] = useState<WorkspaceEntryRecord[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [isComposerExpanded, setIsComposerExpanded] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const {
    thread,
    messages,
    isStreaming,
    error,
    sendMessage,
    cancelStream,
    loadThread,
    resetThread,
    clearError,
  } = useChatStream({
    defaultThreadTitle: "New chat",
    defaultModelName,
    defaultMode,
    defaultAgentName: "default",
    defaultMetadata: {
      source: "chat-ui",
    },
    maxEventLogItems: 10,
  });

  const refreshThreads = useCallback(async () => {
    setThreadListError(null);
    try {
      const nextThreads = await listThreads();
      setThreads(nextThreads);
      return nextThreads;
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "load threads failed";
      setThreadListError(message);
      return [];
    }
  }, []);

  const refreshArtifacts = useCallback(async () => {
    try {
      setArtifacts(await listArtifacts());
    } catch {
      setArtifacts([]);
    }
  }, []);

  useEffect(() => {
    let active = true;

    async function bootstrap() {
      setIsLoadingThreads(true);
      const nextThreads = await refreshThreads();
      if (!active) {
        return;
      }
      if (nextThreads[0]) {
        await loadThread(nextThreads[0]);
      }
      await refreshArtifacts();
      if (active) {
        setIsLoadingThreads(false);
      }
    }

    void bootstrap();

    return () => {
      active = false;
    };
  }, [loadThread, refreshArtifacts, refreshThreads]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const syncComposerSize = useCallback((nextValue: string) => {
    const textarea = textareaRef.current;
    if (!textarea) {
      setIsComposerExpanded(nextValue.includes("\n") || nextValue.length > 72);
      return;
    }

    textarea.style.height = "auto";
    const nextHeight = Math.min(textarea.scrollHeight, 176);
    textarea.style.height = `${Math.max(28, nextHeight)}px`;
    setIsComposerExpanded(textarea.scrollHeight > 38 || nextValue.includes("\n"));
  }, []);

  useEffect(() => {
    syncComposerSize(input);
  }, [input, syncComposerSize]);

  const filteredThreads = useMemo(() => {
    const query = threadQuery.trim().toLowerCase();
    if (!query) {
      return threads;
    }
    return threads.filter((item) => item.title.toLowerCase().includes(query));
  }, [threadQuery, threads]);

  function handleInputChange(event: ChangeEvent<HTMLTextAreaElement>) {
    const nextValue = event.target.value;
    setInput(nextValue);
    syncComposerSize(nextValue);
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing) {
      return;
    }

    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitMessage();
    }
  }

  async function handleSelectThread(nextThread: ThreadRecord) {
    if (isStreaming || nextThread.id === thread?.id) {
      return;
    }

    const loaded = await loadThread(nextThread);
    if (loaded) {
      setAttachments([]);
      clearError();
    }
  }

  function handleNewThread() {
    if (resetThread()) {
      setInput("");
      setAttachments([]);
    }
  }

  async function submitMessage() {
    const text = input.trim();
    if (!text || isStreaming || isUploading) {
      return;
    }

    const currentAttachments = attachments;
    setInput("");
    setAttachments([]);

    const result = await sendMessage(text, {
      mode: defaultMode,
      model_name: defaultModelName,
      agent_name: "default",
      files: currentAttachments.map((file) => file.id),
      metadata: {
        source: "chat-ui",
        uploaded_file_count: currentAttachments.length,
        uploaded_files: currentAttachments.map((file) => ({
          id: file.id,
          filename: file.filename,
          original_filename: file.original_filename,
          content_type: file.content_type,
          size_bytes: file.size_bytes,
        })),
      },
      threadTitle: makeThreadTitle(text),
    });

    if (result.accepted) {
      await refreshThreads();
      await refreshArtifacts();
    } else {
      setInput(text);
      setAttachments(currentAttachments);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await submitMessage();
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFiles = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (selectedFiles.length === 0) {
      return;
    }

    setIsUploading(true);
    try {
      const uploadedFiles: UploadedFileRecord[] = [];

      for (const file of selectedFiles) {
        try {
          uploadedFiles.push(await uploadFile(file));
        } catch (caught) {
          const message = caught instanceof Error ? caught.message : "upload failed";
          toast.error(message);
        }
      }

      if (uploadedFiles.length > 0) {
        setAttachments((current) => [...current, ...uploadedFiles]);
        toast.success(
          uploadedFiles.length === 1
            ? `${uploadedFiles[0].original_filename ?? uploadedFiles[0].filename} uploaded`
            : `${uploadedFiles.length} files uploaded`,
        );
      }
    } finally {
      setIsUploading(false);
    }
  }

  function handleRemoveAttachment(fileId: string) {
    setAttachments((current) => current.filter((item) => item.id !== fileId));
  }

  const composer = (
    <ChatComposer
      attachments={attachments}
      canSend={Boolean(input.trim()) && !isUploading}
      error={error}
      fileInputRef={fileInputRef}
      input={input}
      isExpanded={isComposerExpanded}
      isStreaming={isStreaming}
      isUploading={isUploading}
      textareaRef={textareaRef}
      onAttachFiles={() => fileInputRef.current?.click()}
      onCancel={cancelStream}
      onClearError={clearError}
      onFileChange={handleFileChange}
      onInputChange={handleInputChange}
      onKeyDown={handleComposerKeyDown}
      onRemoveAttachment={handleRemoveAttachment}
      onSend={() => void submitMessage()}
      onSubmit={handleSubmit}
    />
  );

  return (
    <SidebarProvider className="h-dvh min-h-0 overflow-hidden bg-background text-foreground">
      <Sidebar collapsible="icon" className="border-r-0">
        <ThreadSidebar
          activeThreadId={thread?.id ?? null}
          disabled={isStreaming}
          filteredThreads={filteredThreads}
          artifacts={artifacts}
          isLoading={isLoadingThreads}
          query={threadQuery}
          threadListError={threadListError}
          totalThreads={threads.length}
          onNewThread={handleNewThread}
          onQueryChange={setThreadQuery}
          onSelectThread={(nextThread) => void handleSelectThread(nextThread)}
        />
      </Sidebar>

      <SidebarInset className="h-dvh min-h-0 overflow-hidden">
        <header className="flex h-14 shrink-0 items-center justify-end bg-background px-3 sm:px-4">
          <UserMenu />
        </header>

        {messages.length === 0 ? (
          <section className="flex min-h-0 flex-1 flex-col items-center justify-center px-3 pb-16 sm:px-6">
            <div className="w-full max-w-3xl -translate-y-10">
              <EmptyState />
              {composer}
            </div>
          </section>
        ) : (
          <>
            <MessageList messages={messages} messagesEndRef={messagesEndRef} />
            <div className="shrink-0 bg-background px-3 pb-5 pt-3 sm:px-6">
              {composer}
            </div>
          </>
        )}
      </SidebarInset>
    </SidebarProvider>
  );
}
