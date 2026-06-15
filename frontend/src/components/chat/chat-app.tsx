"use client";

import {
  type ChangeEvent,
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
import { type ChatUiMessage, useChatStream } from "@/hooks/use-chat-stream";
import {
  type ChatMode,
  type ClarificationOptionRecord,
  type ClarificationRequestRecord,
  type McpServerRecord,
  type MemoryKind,
  type MemoryRecord,
  type SkillRecord,
  type ThreadRecord,
  type UploadedFileRecord,
  type WorkspaceEntryRecord,
  type WorkspaceReadRecord,
  createHttpMcpServer,
  createMemory,
  deleteArtifact,
  deleteMcpServer,
  deleteMemory,
  deleteSkill,
  deleteThread,
  installSkill,
  listArtifacts,
  listMcpServers,
  listMemories,
  listSkills,
  listThreads,
  readArtifact,
  reorderMcpServers,
  reorderSkills,
  setMcpServerEnabled,
  setMcpServerPinned,
  setSkillEnabled,
  setSkillPinned,
  updateMemory,
  uploadFile,
  uploadSkillFolder,
} from "@/lib/chat-stream";

import { ArtifactWorkspacePanel, ArtifactWorkspaceToolbar } from "./artifact-panel";
import { ChatComposer } from "./chat-composer";
import { makeThreadTitle } from "./chat-format";
import { ThreadSidebar, UserMenu } from "./chat-sidebar";
import { EmptyState, MessageList } from "./message-list";

const defaultModelName = "deepseek-v4-flash";
const defaultMode: ChatMode = "pro";

type QueuedChatMessage = {
  id: string;
  text: string;
  files: string[];
  metadata: Record<string, unknown>;
  attachmentCount: number;
};

type ThreadArtifactIndex = Record<string, string[]>;

const threadArtifactStorageKey = "slotflow.thread-artifacts.v1";
const artifactPanelWidthVariable = "--slotflow-artifact-panel-width";

export function ChatApp() {
  const [threads, setThreads] = useState<ThreadRecord[]>([]);
  const [threadQuery, setThreadQuery] = useState("");
  const [threadListError, setThreadListError] = useState<string | null>(null);
  const [isLoadingThreads, setIsLoadingThreads] = useState(true);
  const [attachments, setAttachments] = useState<UploadedFileRecord[]>([]);
  const [artifacts, setArtifacts] = useState<WorkspaceEntryRecord[]>([]);
  const [threadArtifactPaths, setThreadArtifactPaths] = useState<ThreadArtifactIndex>({});
  const [skills, setSkills] = useState<SkillRecord[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerRecord[]>([]);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [artifactPreview, setArtifactPreview] = useState<WorkspaceReadRecord | null>(null);
  const [artifactPreviewError, setArtifactPreviewError] = useState<string | null>(null);
  const [isLoadingArtifactPreview, setIsLoadingArtifactPreview] = useState(false);
  const [isArtifactPanelOpen, setIsArtifactPanelOpen] = useState(false);
  const [selectedArtifactPath, setSelectedArtifactPath] = useState<string | null>(null);
  const [artifactPanelWidth, setArtifactPanelWidth] = useState(560);
  const [isUploading, setIsUploading] = useState(false);
  const [isQueueDraining, setIsQueueDraining] = useState(false);
  const [queuedMessages, setQueuedMessages] = useState<QueuedChatMessage[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const skillFolderInputRef = useRef<HTMLInputElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const isDrainingQueueRef = useRef(false);
  const hasLoadedThreadArtifactIndexRef = useRef(false);

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
      const nextArtifacts = await listArtifacts();
      setArtifacts(nextArtifacts);
      return nextArtifacts;
    } catch {
      setArtifacts([]);
      return [];
    }
  }, []);

  const refreshSkills = useCallback(async () => {
    try {
      setSkills(await listSkills());
    } catch {
      setSkills([]);
    }
  }, []);

  const refreshMcpServers = useCallback(async () => {
    try {
      setMcpServers(await listMcpServers());
    } catch {
      setMcpServers([]);
    }
  }, []);

  const refreshMemories = useCallback(async () => {
    try {
      setMemories(await listMemories());
    } catch {
      setMemories([]);
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
      await Promise.all([
        refreshArtifacts(),
        refreshSkills(),
        refreshMcpServers(),
        refreshMemories(),
      ]);
      if (active) {
        setIsLoadingThreads(false);
      }
    }

    void bootstrap();

    return () => {
      active = false;
    };
  }, [refreshArtifacts, refreshMcpServers, refreshMemories, refreshSkills, refreshThreads]);

  useEffect(() => {
    setThreadArtifactPaths(readThreadArtifactIndex());
    hasLoadedThreadArtifactIndexRef.current = true;
  }, []);

  useEffect(() => {
    if (!hasLoadedThreadArtifactIndexRef.current) {
      return;
    }
    writeThreadArtifactIndex(threadArtifactPaths);
  }, [threadArtifactPaths]);

  const filteredThreads = useMemo(() => {
    const query = threadQuery.trim().toLowerCase();
    if (!query) {
      return threads;
    }
    return threads.filter((item) => item.title.toLowerCase().includes(query));
  }, [threadQuery, threads]);

  const artifactFiles = useMemo(
    () => artifacts.filter((artifact) => artifact.kind === "file"),
    [artifacts],
  );
  const isConversationBusy = isStreaming || isQueueDraining || queuedMessages.length > 0;

  const rememberThreadArtifacts = useCallback((threadId: string, paths: string[]) => {
    if (paths.length === 0) {
      return;
    }
    setThreadArtifactPaths((current) => {
      const existing = current[threadId] ?? [];
      const merged = [...existing];
      for (const path of paths) {
        if (!merged.includes(path)) {
          merged.push(path);
        }
      }
      return {
        ...current,
        [threadId]: merged,
      };
    });
  }, []);

  const sendPreparedMessage = useCallback(
    async (
      prepared: {
        text: string;
        files: string[];
        metadata: Record<string, unknown>;
        threadTitle: string;
        reuseUserMessageId?: string;
      },
      options: {
        restoreAttachments?: UploadedFileRecord[];
      } = {},
    ): Promise<boolean> => {
      const previousArtifactPaths = new Set(artifactFiles.map((artifact) => artifact.path));
      const result = await sendMessage(prepared.text, {
        mode: defaultMode,
        model_name: defaultModelName,
        agent_name: "default",
        files: prepared.files,
        metadata: prepared.metadata,
        reuse_user_message_id: prepared.reuseUserMessageId,
        threadTitle: prepared.threadTitle,
      });

      if (result.accepted) {
        const [, nextArtifacts] = await Promise.all([
          refreshThreads(),
          refreshArtifacts(),
          refreshSkills(),
          refreshMcpServers(),
          refreshMemories(),
        ]);
        const newArtifacts = nextArtifacts.filter(
          (artifact) => artifact.kind === "file" && !previousArtifactPaths.has(artifact.path),
        );
        if (result.thread && newArtifacts.length > 0) {
          rememberThreadArtifacts(
            result.thread.id,
            newArtifacts.map((artifact) => artifact.path),
          );
        }
        const newArtifact = newArtifacts[0];
        if (newArtifact) {
          void handlePreviewArtifact(newArtifact);
        }
      } else if (options.restoreAttachments) {
        setAttachments(options.restoreAttachments);
      }
      return result.accepted;
    },
    [
      artifactFiles,
      refreshArtifacts,
      refreshMcpServers,
      refreshMemories,
      refreshSkills,
      refreshThreads,
      rememberThreadArtifacts,
      sendMessage,
    ],
  );

  useEffect(() => {
    if (
      isStreaming ||
      isUploading ||
      isQueueDraining ||
      isDrainingQueueRef.current ||
      queuedMessages.length === 0
    ) {
      return;
    }

    const nextMessage = queuedMessages[0];
    isDrainingQueueRef.current = true;
    setIsQueueDraining(true);
    setQueuedMessages((current) =>
      current[0]?.id === nextMessage.id ? current.slice(1) : current,
    );
    void sendPreparedMessage({
      text: nextMessage.text,
      files: nextMessage.files,
      metadata: nextMessage.metadata,
      threadTitle: makeThreadTitle(nextMessage.text),
    }).finally(() => {
      isDrainingQueueRef.current = false;
      setIsQueueDraining(false);
    });
  }, [isQueueDraining, isStreaming, isUploading, queuedMessages, sendPreparedMessage]);

  async function handleSelectThread(nextThread: ThreadRecord) {
    if (isConversationBusy || nextThread.id === thread?.id) {
      return;
    }

    const loaded = await loadThread(nextThread);
    if (loaded) {
      setAttachments([]);
      setQueuedMessages([]);
      clearError();
    }
  }

  function handleNewThread() {
    if (resetThread()) {
      setAttachments([]);
      setQueuedMessages([]);
    }
  }

  async function handleDeleteThread(targetThread: ThreadRecord) {
    if (isConversationBusy) {
      return;
    }

    try {
      await deleteThread(targetThread.id);
      if (targetThread.id === thread?.id) {
        resetThread();
        setAttachments([]);
        setQueuedMessages([]);
      }
      setThreadArtifactPaths((current) => {
        if (!(targetThread.id in current)) {
          return current;
        }
        const next = { ...current };
        delete next[targetThread.id];
        return next;
      });
      await refreshThreads();
      toast.success("聊天记录已删除");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "delete thread failed";
      toast.error(message);
    }
  }

  async function submitMessage(
    rawText: string,
    options: {
      reuseUserMessageId?: string;
      files?: string[];
      metadata?: Record<string, unknown>;
    } = {},
  ): Promise<boolean> {
    const text = rawText.trim();
    if (!text || isUploading) {
      return false;
    }

    const currentAttachments = attachments;
    const isReusingUserMessage = Boolean(options.reuseUserMessageId);
    if (isReusingUserMessage && isConversationBusy) {
      return false;
    }

    const files = options.files ?? currentAttachments.map((file) => file.id);
    if (!isReusingUserMessage) {
      setAttachments([]);
    }
    const uploadedFilesMetadata = isReusingUserMessage
      ? extractUploadedFilesFromMetadata(options.metadata)
      : currentAttachments.map((file) => ({
          id: file.id,
          filename: file.filename,
          original_filename: file.original_filename,
          content_type: file.content_type,
          size_bytes: file.size_bytes,
        }));
    const metadata = {
      source: "chat-ui",
      ...(options.metadata ?? {}),
      uploaded_file_count: uploadedFilesMetadata.length,
      uploaded_files: uploadedFilesMetadata,
    };

    if (isStreaming || isQueueDraining || queuedMessages.length > 0) {
      setQueuedMessages((current) => [
        ...current,
        {
          id: makeQueueId(),
          text,
          files,
          metadata,
          attachmentCount: files.length,
        },
      ]);
      return true;
    }

    return sendPreparedMessage(
      {
        text,
        files,
        metadata,
        reuseUserMessageId: options.reuseUserMessageId,
        threadTitle: makeThreadTitle(text),
      },
      {
        restoreAttachments: isReusingUserMessage ? undefined : currentAttachments,
      },
    );
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

  async function handleSkillFolderChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFiles = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (selectedFiles.length === 0) {
      return;
    }

    try {
      const uploadedSkills = await uploadSkillFolder(selectedFiles);
      await refreshSkills();
      toast.success(
        uploadedSkills.length === 1
          ? `${uploadedSkills[0].name} skill added`
          : `${uploadedSkills.length} skills added`,
      );
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "upload skill folder failed";
      toast.error(message);
    }
  }

  async function handleInstallSkillFromRegistry() {
    const packageUrl = window.prompt(
      "Skill package URL",
      "https://github.com/vercel-labs/skills",
    );
    if (!packageUrl?.trim()) {
      return;
    }

    const skillName = window.prompt("Skill name", "find-skills");
    if (!skillName?.trim()) {
      return;
    }

    try {
      const skill = await installSkill({
        package_url: packageUrl.trim(),
        skill_name: skillName.trim(),
      });
      await refreshSkills();
      toast.success(`${skill.name} skill installed`);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "install skill failed";
      toast.error(message);
    }
  }

  async function handleToggleSkill(skill: SkillRecord, enabled: boolean) {
    try {
      await setSkillEnabled(skill.name, enabled);
      await refreshSkills();
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "update skill failed";
      toast.error(message);
    }
  }

  async function handlePinSkill(skill: SkillRecord, pinned: boolean) {
    try {
      await setSkillPinned(skill.name, pinned);
      await refreshSkills();
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "update skill failed";
      toast.error(message);
    }
  }

  async function handleReorderSkills(names: string[]) {
    setSkills((current) => sortRecordsByNames(current, names));
    try {
      setSkills(await reorderSkills(names));
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "reorder skills failed";
      toast.error(message);
      await refreshSkills();
    }
  }

  async function handleDeleteSkill(skill: SkillRecord) {
    if (skill.protected) {
      return;
    }
    try {
      await deleteSkill(skill.name);
      await refreshSkills();
      toast.success(`${skill.name} skill deleted`);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "delete skill failed";
      toast.error(message);
    }
  }

  async function handleAddHttpMcpServer() {
    const name = window.prompt("MCP name");
    if (!name?.trim()) {
      return;
    }

    const url = window.prompt("MCP HTTP URL");
    if (!url?.trim()) {
      return;
    }

    try {
      const server = await createHttpMcpServer({
        name: name.trim(),
        url: url.trim(),
      });
      await refreshMcpServers();
      toast.success(`${server.name} MCP added`);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "create MCP server failed";
      toast.error(message);
    }
  }

  async function handleToggleMcpServer(server: McpServerRecord, enabled: boolean) {
    try {
      await setMcpServerEnabled(server.name, enabled);
      await refreshMcpServers();
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "update MCP server failed";
      toast.error(message);
    }
  }

  async function handlePinMcpServer(server: McpServerRecord, pinned: boolean) {
    try {
      await setMcpServerPinned(server.name, pinned);
      await refreshMcpServers();
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "update MCP server failed";
      toast.error(message);
    }
  }

  async function handleReorderMcpServers(names: string[]) {
    setMcpServers((current) => sortRecordsByNames(current, names));
    try {
      setMcpServers(await reorderMcpServers(names));
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "reorder MCP servers failed";
      toast.error(message);
      await refreshMcpServers();
    }
  }

  async function handleDeleteMcpServer(server: McpServerRecord) {
    if (server.protected) {
      return;
    }
    try {
      await deleteMcpServer(server.name);
      await refreshMcpServers();
      toast.success(`${server.name} MCP deleted`);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "delete MCP server failed";
      toast.error(message);
    }
  }

  async function handleAddMemory(content: string, kind: MemoryKind) {
    if (!content.trim()) {
      return;
    }

    try {
      await createMemory(content.trim(), kind);
      await refreshMemories();
      toast.success("记忆已添加");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "create memory failed";
      toast.error(message);
    }
  }

  async function handleEditMemory(memory: MemoryRecord, content: string, kind: MemoryKind) {
    if (!content.trim()) {
      return;
    }

    try {
      await updateMemory(memory.id, content.trim(), kind);
      await refreshMemories();
      toast.success("记忆已更新");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "update memory failed";
      toast.error(message);
    }
  }

  async function handleDeleteMemory(memory: MemoryRecord) {
    setMemories((current) => current.filter((item) => item.id !== memory.id));
    try {
      await deleteMemory(memory.id);
      await refreshMemories();
      toast.success("记忆已删除");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "delete memory failed";
      await refreshMemories();
      toast.error(message);
    }
  }

  async function handlePreviewArtifact(artifact: WorkspaceEntryRecord) {
    if (artifact.kind !== "file") {
      return;
    }

    setSelectedArtifactPath(artifact.path);
    setIsArtifactPanelOpen(true);
    setIsLoadingArtifactPreview(true);
    setArtifactPreviewError(null);
    try {
      setArtifactPreview(await readArtifact(artifact.path));
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "read artifact failed";
      setArtifactPreview(null);
      setArtifactPreviewError(message);
      toast.error(message);
    } finally {
      setIsLoadingArtifactPreview(false);
    }
  }

  function handleOpenArtifactPanel() {
    const firstFile = artifactFiles[0];
    if (!firstFile) {
      toast.info("暂无产物");
      return;
    }

    setIsArtifactPanelOpen(true);
    if (!artifactPreview) {
      void handlePreviewArtifact(firstFile);
    }
  }

  async function handleDeleteArtifact(artifact: WorkspaceEntryRecord) {
    if (artifact.kind !== "file") {
      return;
    }

    try {
      await deleteArtifact(artifact.path);
      const nextArtifacts = await refreshArtifacts();
      if (selectedArtifactPath === artifact.path || artifactPreview?.path === artifact.path) {
        setSelectedArtifactPath(null);
        setArtifactPreview(null);
        setArtifactPreviewError(null);
        const nextFile = nextArtifacts.find((item) => item.kind === "file");
        if (nextFile && isArtifactPanelOpen) {
          void handlePreviewArtifact(nextFile);
        } else if (!nextFile) {
          setIsArtifactPanelOpen(false);
        }
      }
      toast.success("产物已删除");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "delete artifact failed";
      toast.error(message);
    }
  }

  async function handleCopyMessage(content: string) {
    if (!content.trim()) {
      return;
    }

    try {
      await navigator.clipboard.writeText(content);
      toast.success("已复制");
    } catch {
      toast.error("复制失败");
    }
  }

  async function handleEditLatestUserMessage(messageId: string, content: string) {
    if (isConversationBusy) {
      return false;
    }
    const targetMessage = messages.find((message) => message.id === messageId);
    return submitMessage(content, {
      reuseUserMessageId: messageId,
      files: extractFileIdsFromMessage(targetMessage),
      metadata: targetMessage?.metadata,
    });
  }

  async function handleRetryLatestAssistantMessage() {
    if (isConversationBusy) {
      return;
    }
    const latestUserMessage = [...messages].reverse().find((message) => message.role === "user");
    if (!latestUserMessage?.content.trim()) {
      return;
    }
    await submitMessage(latestUserMessage.content, {
      reuseUserMessageId: latestUserMessage.id,
      files: extractFileIdsFromMessage(latestUserMessage),
      metadata: latestUserMessage.metadata,
    });
  }

  async function handleSelectClarification(
    clarification: ClarificationRequestRecord,
    option: ClarificationOptionRecord,
  ) {
    if (isConversationBusy) {
      return;
    }
    await submitMessage(`我选择 ${option.id}：${option.label}`, {
      files: [],
      metadata: {
        source: "clarification-choice",
        clarification_id: clarification.id,
        clarification_question: clarification.question,
        option_id: option.id,
      },
    });
  }

  function handleRemoveAttachment(fileId: string) {
    setAttachments((current) => current.filter((item) => item.id !== fileId));
  }

  function handleRemoveQueuedMessage(messageId: string) {
    setQueuedMessages((current) => current.filter((message) => message.id !== messageId));
  }

  const composer = (
    <ChatComposer
      attachments={attachments}
      error={error}
      fileInputRef={fileInputRef}
      isStreaming={isStreaming}
      isUploading={isUploading}
      queuedMessages={queuedMessages.map((message, index) => ({
        id: message.id,
        text: message.text,
        attachmentCount: message.attachmentCount,
        position: index + 1,
      }))}
      onAttachFiles={() => fileInputRef.current?.click()}
      onCancel={cancelStream}
      onClearError={clearError}
      onFileChange={handleFileChange}
      onRemoveAttachment={handleRemoveAttachment}
      onRemoveQueuedMessage={handleRemoveQueuedMessage}
      onSendMessage={submitMessage}
    />
  );

  return (
    <SidebarProvider className="h-dvh min-h-0 overflow-hidden bg-background text-foreground">
      <input
        ref={skillFolderInputRef}
        type="file"
        multiple
        {...{ webkitdirectory: "", directory: "" }}
        className="hidden"
        onChange={(event) => void handleSkillFolderChange(event)}
      />
      <Sidebar collapsible="icon" className="border-r-0">
        <ThreadSidebar
          activeThreadId={thread?.id ?? null}
          disabled={isConversationBusy}
          filteredThreads={filteredThreads}
          artifacts={artifacts}
          threadArtifactPaths={threadArtifactPaths}
          skills={skills}
          mcpServers={mcpServers}
          memories={memories}
          isLoading={isLoadingThreads}
          query={threadQuery}
          threadListError={threadListError}
          totalThreads={threads.length}
          onAddHttpMcpServer={() => void handleAddHttpMcpServer()}
          onAddMemory={(content, kind) => void handleAddMemory(content, kind)}
          onDeleteMcpServer={(server) => void handleDeleteMcpServer(server)}
          onDeleteMemory={(memory) => void handleDeleteMemory(memory)}
          onDeleteSkill={(skill) => void handleDeleteSkill(skill)}
          onDeleteArtifact={(artifact) => void handleDeleteArtifact(artifact)}
          onDeleteThread={(targetThread) => void handleDeleteThread(targetThread)}
          onEditMemory={(memory, content, kind) => void handleEditMemory(memory, content, kind)}
          onInstallSkill={() => void handleInstallSkillFromRegistry()}
          onNewThread={handleNewThread}
          onOpenArtifacts={handleOpenArtifactPanel}
          onPreviewArtifact={(artifact) => void handlePreviewArtifact(artifact)}
          onQueryChange={setThreadQuery}
          onPinMcpServer={(server, pinned) => void handlePinMcpServer(server, pinned)}
          onPinSkill={(skill, pinned) => void handlePinSkill(skill, pinned)}
          onReorderMcpServers={(names) => void handleReorderMcpServers(names)}
          onReorderSkills={(names) => void handleReorderSkills(names)}
          onToggleMcpServer={(server, enabled) => void handleToggleMcpServer(server, enabled)}
          onToggleSkill={(skill, enabled) => void handleToggleSkill(skill, enabled)}
          onUploadSkill={() => skillFolderInputRef.current?.click()}
          onSelectThread={(nextThread) => void handleSelectThread(nextThread)}
        />
      </Sidebar>

      <SidebarInset className="h-dvh min-h-0 overflow-hidden">
        <header className="flex h-14 shrink-0 items-center justify-end bg-background pl-3 sm:pl-4">
          <div className={isArtifactPanelOpen ? "" : "pr-3 sm:pr-4"}>
            <UserMenu />
          </div>
          {isArtifactPanelOpen && artifactFiles.length > 0 ? (
            <div
              className="ml-2 min-w-0 shrink-0"
              style={{ width: `var(${artifactPanelWidthVariable}, ${artifactPanelWidth}px)` }}
            >
              <ArtifactWorkspaceToolbar
                artifacts={artifactFiles}
                activePath={selectedArtifactPath ?? artifactPreview?.path ?? null}
                onClose={() => setIsArtifactPanelOpen(false)}
                onPreviewArtifact={(artifact) => void handlePreviewArtifact(artifact)}
              />
            </div>
          ) : null}
        </header>

        <div className="flex min-h-0 flex-1 overflow-hidden">
          <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            {messages.length === 0 ? (
              <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-3 pb-16 sm:px-6">
                <div className="w-full max-w-3xl -translate-y-10">
                  <EmptyState />
                  {composer}
                </div>
              </div>
            ) : (
              <>
                <MessageList
                  messages={messages}
                  messagesEndRef={messagesEndRef}
                  isStreaming={isStreaming}
                  onCopyMessage={(content) => void handleCopyMessage(content)}
                  onEditLatestUserMessage={(messageId, content) =>
                    handleEditLatestUserMessage(messageId, content)
                  }
                  onRetryLatestAssistantMessage={() => void handleRetryLatestAssistantMessage()}
                  onSelectClarification={(clarification, option) =>
                    void handleSelectClarification(clarification, option)
                  }
                />
                <div className="shrink-0 bg-background px-3 pb-5 pt-3 sm:px-6">
                  {composer}
                </div>
              </>
            )}
          </section>

          {isArtifactPanelOpen && artifactFiles.length > 0 ? (
            <ArtifactWorkspacePanel
              preview={artifactPreview}
              previewError={artifactPreviewError}
              isLoadingPreview={isLoadingArtifactPreview}
              width={artifactPanelWidth}
              onWidthChange={setArtifactPanelWidth}
            />
          ) : null}
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}

function extractFileIdsFromMessage(message: ChatUiMessage | undefined): string[] {
  const files = message?.metadata?.files;
  if (Array.isArray(files)) {
    return files.filter((fileId): fileId is string => typeof fileId === "string");
  }

  return extractUploadedFilesFromMetadata(message?.metadata).flatMap((item) => {
    if (
      typeof item === "object" &&
      item !== null &&
      "id" in item &&
      typeof item.id === "string"
    ) {
      return [item.id];
    }
    return [];
  });
}

function extractUploadedFilesFromMetadata(
  metadata: Record<string, unknown> | undefined,
): unknown[] {
  const uploadedFiles = metadata?.uploaded_files;
  return Array.isArray(uploadedFiles) ? uploadedFiles : [];
}

function makeQueueId() {
  return `queued_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function sortRecordsByNames<T extends { name: string }>(records: T[], names: string[]): T[] {
  const position = new Map(names.map((name, index) => [name, index]));
  return [...records].sort(
    (left, right) =>
      (position.get(left.name) ?? records.length) -
      (position.get(right.name) ?? records.length),
  );
}

function readThreadArtifactIndex(): ThreadArtifactIndex {
  if (typeof window === "undefined") {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(threadArtifactStorageKey);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).flatMap(([threadId, paths]) => {
        if (!Array.isArray(paths)) {
          return [];
        }
        return [
          [
            threadId,
            paths.filter((path): path is string => typeof path === "string" && path.length > 0),
          ],
        ];
      }),
    );
  } catch {
    return {};
  }
}

function writeThreadArtifactIndex(index: ThreadArtifactIndex) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(threadArtifactStorageKey, JSON.stringify(index));
}
