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
import { useChatStream } from "@/hooks/use-chat-stream";
import { mergeWorkspaceEntries } from "@/hooks/use-chat-stream-helpers";
import {
  type ChatMode,
  type ClarificationOptionRecord,
  type ClarificationRequestRecord,
  type McpServerRecord,
  type MemoryKind,
  type MemoryRecord,
  type ModelCatalogRecord,
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
  listChatModels,
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

import { ArtifactWorkspacePanel } from "./artifact-panel";
import {
  type ThreadArtifactIndex,
  defaultModelName,
  extractFileIdsFromMessage,
  extractUploadedFilesFromMetadata,
  flattenModelOptions,
  formatUploadToast,
  makeQueueId,
  modelExists,
  readThreadArtifactIndex,
  removeArtifactPathFromThreadIndex,
  sortRecordsByNames,
  writeThreadArtifactIndex,
} from "./chat-app-helpers";
import { ChatComposer } from "./chat-composer";
import { filterThreadArtifacts, makeThreadTitle } from "./chat-format";
import { ThreadSidebar } from "./chat-sidebar";
import { EmptyState, MessageList } from "./message-list";

const defaultMode: ChatMode = "pro";

type QueuedChatMessage = {
  id: string;
  text: string;
  files: string[];
  metadata: Record<string, unknown>;
  attachmentCount: number;
  modelName: string;
  mode: ChatMode;
  thinkingEnabled: boolean;
};

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
  const [modelCatalog, setModelCatalog] = useState<ModelCatalogRecord | null>(null);
  const [selectedModelName, setSelectedModelName] = useState(defaultModelName);
  const [selectedMode, setSelectedMode] = useState<ChatMode>(defaultMode);
  const [selectedThinkingEnabled, setSelectedThinkingEnabled] = useState(
    defaultMode !== "flash",
  );
  const [isLoadingModels, setIsLoadingModels] = useState(true);
  const [artifactPreview, setArtifactPreview] = useState<WorkspaceReadRecord | null>(null);
  const [artifactPreviewError, setArtifactPreviewError] = useState<string | null>(null);
  const [isLoadingArtifactPreview, setIsLoadingArtifactPreview] = useState(false);
  const [isArtifactPanelOpen, setIsArtifactPanelOpen] = useState(false);
  const [selectedArtifactPath, setSelectedArtifactPath] = useState<string | null>(null);
  const [artifactPanelWidth, setArtifactPanelWidth] = useState(680);
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
    todos,
    todoRevision,
    isStreaming,
    error,
    sendMessage,
    cancelStream,
    loadThread,
    resetThread,
    removeMessage,
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
      await refreshThreads();
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

  const artifactFiles = useMemo(
    () => artifacts.filter((artifact) => artifact.kind === "file"),
    [artifacts],
  );
  const activeThreadArtifactFiles = useMemo(() => {
    if (!thread) {
      return [];
    }
    return filterThreadArtifacts(thread.id, artifactFiles, threadArtifactPaths, messages);
  }, [artifactFiles, messages, thread?.id, threadArtifactPaths]);
  const isConversationBusy = isStreaming || isQueueDraining || queuedMessages.length > 0;
  const isRunSettingsLocked = messages.length > 0;
  const modelOptions = useMemo(() => flattenModelOptions(modelCatalog), [modelCatalog]);

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
        modelName: string;
        mode: ChatMode;
        thinkingEnabled: boolean;
        reuseUserMessageId?: string;
      },
      options: {
        restoreAttachments?: UploadedFileRecord[];
      } = {},
    ): Promise<boolean> => {
      const previousArtifactPaths = new Set(artifactFiles.map((artifact) => artifact.path));
      const result = await sendMessage(prepared.text, {
        mode: prepared.mode,
        model_name: prepared.modelName,
        thinking_enabled: prepared.thinkingEnabled,
        agent_name: "default",
        files: prepared.files,
        metadata: prepared.metadata,
        reuse_user_message_id: prepared.reuseUserMessageId,
        threadTitle: prepared.threadTitle,
      });

      if (result.accepted) {
        const discoveredArtifacts = result.artifacts.filter(
          (artifact) => artifact.kind === "file",
        );
        if (discoveredArtifacts.length > 0) {
          setArtifacts((current) => mergeWorkspaceEntries(current, discoveredArtifacts));
        }
        const [, nextArtifacts] = await Promise.all([
          refreshThreads(),
          refreshArtifacts(),
          refreshSkills(),
          refreshMcpServers(),
          refreshMemories(),
        ]);
        const newArtifacts = mergeWorkspaceEntries(
          discoveredArtifacts,
          nextArtifacts.filter(
            (artifact) => artifact.kind === "file" && !previousArtifactPaths.has(artifact.path),
          ),
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
    let active = true;

    async function refreshModelCatalog() {
      setIsLoadingModels(true);
      try {
        const catalog = await listChatModels();
        if (!active) {
          return;
        }
        setModelCatalog(catalog);
        setSelectedModelName((current) =>
          modelExists(catalog, current) ? current : catalog.default_model,
        );
      } catch (caught) {
        const message = caught instanceof Error ? caught.message : "load models failed";
        toast.error(message);
        if (active) {
          setModelCatalog(null);
        }
      } finally {
        if (active) {
          setIsLoadingModels(false);
        }
      }
    }

    void refreshModelCatalog();

    return () => {
      active = false;
    };
  }, []);

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
      modelName: nextMessage.modelName,
      mode: nextMessage.mode,
      thinkingEnabled: nextMessage.thinkingEnabled,
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

  function handleModeChange(nextMode: ChatMode) {
    if (isRunSettingsLocked) {
      return;
    }
    setSelectedMode(nextMode);
    setSelectedThinkingEnabled(nextMode !== "flash");
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
      model_name: selectedModelName,
      mode: selectedMode,
      thinking_enabled: selectedThinkingEnabled,
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
          modelName: selectedModelName,
          mode: selectedMode,
          thinkingEnabled: selectedThinkingEnabled,
        },
      ]);
      return true;
    }

    return sendPreparedMessage(
      {
        text,
        files,
        metadata,
        modelName: selectedModelName,
        mode: selectedMode,
        thinkingEnabled: selectedThinkingEnabled,
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
    await uploadSelectedFiles(selectedFiles);
  }

  async function uploadSelectedFiles(selectedFiles: File[]) {
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
        toast.success(formatUploadToast(uploadedFiles));
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
    const firstFile = activeThreadArtifactFiles[0];
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
      setThreadArtifactPaths((current) => removeArtifactPathFromThreadIndex(current, artifact.path));
      if (selectedArtifactPath === artifact.path || artifactPreview?.path === artifact.path) {
        setSelectedArtifactPath(null);
        setArtifactPreview(null);
        setArtifactPreviewError(null);
        const nextFile = thread
          ? filterThreadArtifacts(
              thread.id,
              nextArtifacts.filter((item) => item.kind === "file"),
              removeArtifactPathFromThreadIndex(threadArtifactPaths, artifact.path),
              messages,
            )[0]
          : undefined;
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

  useEffect(() => {
    if (!isArtifactPanelOpen) {
      return;
    }

    if (activeThreadArtifactFiles.length === 0) {
      setSelectedArtifactPath(null);
      setArtifactPreview(null);
      setArtifactPreviewError(null);
      setIsArtifactPanelOpen(false);
      return;
    }

    const activePaths = new Set(activeThreadArtifactFiles.map((artifact) => artifact.path));
    const currentPath = selectedArtifactPath ?? artifactPreview?.path ?? null;
    if (currentPath && activePaths.has(currentPath)) {
      return;
    }

    void handlePreviewArtifact(activeThreadArtifactFiles[0]);
  }, [
    activeThreadArtifactFiles,
    artifactPreview?.path,
    isArtifactPanelOpen,
    selectedArtifactPath,
  ]);

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
    messageId: string,
    clarification: ClarificationRequestRecord,
    option: ClarificationOptionRecord,
  ) {
    if (isConversationBusy) {
      return;
    }
    removeMessage(messageId);
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
      todos={todos}
      todoRevision={todoRevision}
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
      modelOptions={modelOptions}
      isRunSettingsLocked={isRunSettingsLocked}
      selectedMode={selectedMode}
      selectedModelName={selectedModelName}
      selectedThinkingEnabled={selectedThinkingEnabled}
      showPromptChips={messages.length === 0}
      isLoadingModels={isLoadingModels}
      onAttachFiles={() => fileInputRef.current?.click()}
      onCancel={cancelStream}
      onClearError={clearError}
      onFileChange={handleFileChange}
      onPasteFiles={(files) => uploadSelectedFiles(files)}
      onRemoveAttachment={handleRemoveAttachment}
      onRemoveQueuedMessage={handleRemoveQueuedMessage}
      onSendMessage={submitMessage}
      onModeChange={handleModeChange}
      onModelChange={setSelectedModelName}
      onThinkingEnabledChange={setSelectedThinkingEnabled}
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
      <Sidebar collapsible="icon" resizable className="border-r-0">
        <ThreadSidebar
          activeThreadId={thread?.id ?? null}
          disabled={isConversationBusy}
          filteredThreads={threads}
          artifacts={artifacts}
          threadArtifactPaths={threadArtifactPaths}
          skills={skills}
          mcpServers={mcpServers}
          memories={memories}
          isLoading={isLoadingThreads}
          query={threadQuery}
          threadListError={threadListError}
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
                  onSelectClarification={(messageId, clarification, option) =>
                    void handleSelectClarification(messageId, clarification, option)
                  }
                />
                <div className="shrink-0 bg-background px-3 pb-5 pt-3 sm:px-6">
                  {composer}
                </div>
              </>
            )}
          </section>

          {isArtifactPanelOpen && activeThreadArtifactFiles.length > 0 ? (
            <ArtifactWorkspacePanel
              activePath={selectedArtifactPath ?? artifactPreview?.path ?? null}
              artifacts={activeThreadArtifactFiles}
              preview={artifactPreview}
              previewError={artifactPreviewError}
              isLoadingPreview={isLoadingArtifactPreview}
              width={artifactPanelWidth}
              onClose={() => setIsArtifactPanelOpen(false)}
              onPreviewArtifact={(artifact) => void handlePreviewArtifact(artifact)}
              onWidthChange={setArtifactPanelWidth}
            />
          ) : null}
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}

