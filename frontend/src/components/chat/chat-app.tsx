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
import { PanelRightOpen, SquareTerminal } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Sidebar,
  SidebarInset,
  SidebarProvider,
} from "@/components/ui/sidebar";
import { useChatStream } from "@/hooks/use-chat-stream";
import { mergeWorkspaceEntries } from "@/hooks/use-chat-stream-helpers";
import {
  type ChatMode,
  type ModelProvider,
  type ClarificationOptionRecord,
  type ClarificationRequestRecord,
  type McpServerRecord,
  type MemoryKind,
  type MemoryRecord,
  type SkillRecord,
  type SkillInstallRequest,
  type ThreadRecord,
  type UploadedFileRecord,
  type WorkspaceEntryRecord,
  createHttpMcpServer,
  createMemory,
  deleteArtifact,
  deleteMcpServer,
  deleteMemory,
  deleteSkill,
  deleteThread,
  groupSkills,
  installSkill,
  listThreads,
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

import { WorkspacePanel, type WorkspacePanelRequest } from "./workspace-panel";
import { WorkspaceDirectoryModal } from "./workspace-directory-modal";
import {
  defaultModelName,
  extractFileIdsFromMessage,
  extractUploadedFilesFromMetadata,
  formatUploadToast,
  makeQueueId,
  providerForModel,
  removeArtifactPathFromThreadIndex,
  sortRecordsByNames,
} from "./chat-app-helpers";
import { ChatComposer } from "./chat-composer";
import { filterThreadArtifacts, makeThreadTitle } from "./chat-format";
import { ThreadSidebar } from "./chat-sidebar";
import { EmptyState, MessageList } from "./message-list";
import { useModelCatalog } from "./use-model-catalog";
import { useThreadArtifactIndex } from "./use-thread-artifact-index";
import { useWorkspaceData } from "./use-workspace-data";

const defaultMode: ChatMode = "pro";

type QueuedChatMessage = {
  id: string;
  /** 排队时所在的线程;null 表示"新聊天"视图。只在用户回到该线程时出队。 */
  threadId: string | null;
  text: string;
  files: string[];
  metadata: Record<string, unknown>;
  attachmentCount: number;
  modelName: string;
  provider?: ModelProvider;
  mode: ChatMode;
  thinkingEnabled: boolean;
};

type UiActionOptions<T> = {
  failure: string;
  after?: (value: T) => void | Promise<void>;
  recover?: () => void | Promise<void>;
  success?: string | ((value: T) => string);
};

async function runUiAction<T>(
  action: () => Promise<T>,
  options: UiActionOptions<T>,
): Promise<T | undefined> {
  try {
    const value = await action();
    await options.after?.(value);
    if (options.success) {
      toast.success(
        typeof options.success === "function" ? options.success(value) : options.success,
      );
    }
    return value;
  } catch (caught) {
    toast.error(caught instanceof Error ? caught.message : options.failure);
    await options.recover?.();
  }
}

export function ChatApp() {
  const [threads, setThreads] = useState<ThreadRecord[]>([]);
  const [threadQuery, setThreadQuery] = useState("");
  const [threadListError, setThreadListError] = useState<string | null>(null);
  const [isLoadingThreads, setIsLoadingThreads] = useState(true);
  const [attachments, setAttachments] = useState<UploadedFileRecord[]>([]);
  const [selectedMode, setSelectedMode] = useState<ChatMode>(defaultMode);
  const [selectedThinkingEnabled, setSelectedThinkingEnabled] = useState(
    defaultMode !== "flash",
  );
  const [isArtifactPanelOpen, setIsArtifactPanelOpen] = useState(false);
  const [isWorkspaceDirectoryOpen, setIsWorkspaceDirectoryOpen] = useState(false);
  const [selectedArtifactPath, setSelectedArtifactPath] = useState<string | null>(null);
  const [panelRequest, setPanelRequest] = useState<WorkspacePanelRequest | null>(null);
  const [artifactPanelWidth, setArtifactPanelWidth] = useState(680);
  const [workspaceRefreshKey, setWorkspaceRefreshKey] = useState(0);
  const [isUploading, setIsUploading] = useState(false);
  const [isQueueDraining, setIsQueueDraining] = useState(false);
  const [queuedMessages, setQueuedMessages] = useState<QueuedChatMessage[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const skillFolderInputRef = useRef<HTMLInputElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const isDrainingQueueRef = useRef(false);

  const {
    thread,
    messages,
    todos,
    todoListKey,
    isStreaming,
    runStates,
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
  });

  const { isLoadingModels, selectedModelName, setSelectedModelName, modelOptions } =
    useModelCatalog();

  const {
    artifacts,
    setArtifacts,
    skills,
    setSkills,
    mcpServers,
    setMcpServers,
    memories,
    setMemories,
    refreshArtifacts,
    refreshSkills,
    refreshMcpServers,
    refreshMemories,
  } = useWorkspaceData();

  const { threadArtifactPaths, setThreadArtifactPaths, rememberThreadArtifacts } =
    useThreadArtifactIndex();

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
  // 忙碌只作用于"当前查看的线程":其他线程后台生成时,这里的一切照常可用。
  const activeThreadKey = thread?.id ?? null;
  const queuedForActiveThread = useMemo(
    () => queuedMessages.filter((message) => message.threadId === activeThreadKey),
    [activeThreadKey, queuedMessages],
  );
  const isConversationBusy =
    isStreaming || isQueueDraining || queuedForActiveThread.length > 0;
  const isRunSettingsLocked = messages.length > 0;

  const sendPreparedMessage = useCallback(
    async (
      prepared: {
        text: string;
        files: string[];
        metadata: Record<string, unknown>;
        threadTitle: string;
        modelName: string;
        provider?: ModelProvider;
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
        provider: prepared.provider,
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
          setWorkspaceRefreshKey((current) => current + 1);
        }
        const [, nextArtifacts] = await Promise.all([
          refreshThreads(),
          refreshArtifacts(),
          refreshSkills(),
          refreshMcpServers(),
          refreshMemories(),
        ]);
        // Only auto-open artifacts that belong to THIS thread and are genuinely new.
        // `nextArtifacts` is the full recursive listing under artifacts/ (every thread +
        // legacy), so naively taking newArtifacts[0] could pop up an older thread's file
        // after answering an unrelated question (issue #8). Restrict to this thread's
        // namespaced path + remembered paths, then to files not seen before this run.
        const threadId = result.thread?.id ?? thread?.id;
        const threadPrefix = threadId ? `artifacts/${threadId}/` : null;
        const threadOwned = (artifact: WorkspaceEntryRecord) =>
          threadPrefix !== null && artifact.path.startsWith(threadPrefix);
        const newArtifacts = mergeWorkspaceEntries(
          discoveredArtifacts.filter(threadOwned),
          nextArtifacts.filter(
            (artifact) =>
              artifact.kind === "file" &&
              threadOwned(artifact) &&
              !previousArtifactPaths.has(artifact.path),
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
          handlePreviewArtifact(newArtifact);
          setWorkspaceRefreshKey((current) => current + 1);
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

    // 队列按线程归属出队:只发当前正在查看的线程的排队消息,切走的线程等用户回来再发。
    const nextMessage = queuedMessages.find(
      (message) => message.threadId === (thread?.id ?? null),
    );
    if (!nextMessage) {
      return;
    }
    isDrainingQueueRef.current = true;
    setIsQueueDraining(true);
    setQueuedMessages((current) =>
      current.filter((message) => message.id !== nextMessage.id),
    );
    void sendPreparedMessage({
      text: nextMessage.text,
      files: nextMessage.files,
      metadata: nextMessage.metadata,
      threadTitle: makeThreadTitle(nextMessage.text),
      modelName: nextMessage.modelName,
      provider: nextMessage.provider,
      mode: nextMessage.mode,
      thinkingEnabled: nextMessage.thinkingEnabled,
    }).finally(() => {
      isDrainingQueueRef.current = false;
      setIsQueueDraining(false);
    });
  }, [isQueueDraining, isStreaming, isUploading, queuedMessages, sendPreparedMessage, thread?.id]);

  async function handleSelectThread(nextThread: ThreadRecord) {
    if (nextThread.id === thread?.id) {
      return;
    }

    // 允许在其他线程生成时切走查看;当前线程的运行继续在后台进行。
    const loaded = await loadThread(nextThread);
    if (loaded) {
      setAttachments([]);
      setSelectedArtifactPath(null);
    }
  }

  function handleNewThread() {
    if (resetThread()) {
      setAttachments([]);
      setSelectedArtifactPath(null);
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
    try {
      await deleteThread(targetThread.id);
      setQueuedMessages((current) =>
        current.filter((message) => message.threadId !== targetThread.id),
      );
      if (targetThread.id === thread?.id) {
        resetThread();
        setAttachments([]);
        setSelectedArtifactPath(null);
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

    if (isConversationBusy) {
      setQueuedMessages((current) => [
        ...current,
        {
          id: makeQueueId(),
          threadId: thread?.id ?? null,
          text,
          files,
          metadata,
          attachmentCount: files.length,
          modelName: selectedModelName,
          provider: providerForModel(modelOptions, selectedModelName),
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
        provider: providerForModel(modelOptions, selectedModelName),
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
    if (selectedFiles.length === 0) return;

    await runUiAction(() => uploadSkillFolder(selectedFiles), {
      failure: "upload skill folder failed",
      after: refreshSkills,
      success: (skills) =>
        skills.length === 1
          ? `${skills[0].name} skill added`
          : `${skills.length} skills added`,
    });
  }

  async function handleInstallSkillFromRegistry(request?: SkillInstallRequest) {
    const packageUrl =
      request?.package_url ??
      window.prompt("Skill package URL", "https://github.com/vercel-labs/skills");
    if (!packageUrl?.trim()) return;
    const skillName = request?.skill_name ?? window.prompt("Skill name", "find-skills");
    if (!skillName?.trim()) return;

    await runUiAction(
      () =>
        installSkill({
          package_url: packageUrl.trim(),
          skill_name: skillName.trim(),
        }),
      {
        failure: "install skill failed",
        after: refreshSkills,
        success: (skill) => `${skill.name} skill installed`,
      },
    );
  }

  async function handleGroupSkills(input: {
    name: string;
    description: string;
    content: string;
    members: string[];
  }) {
    await runUiAction(() => groupSkills(input), {
      failure: "group skills failed",
      after: refreshSkills,
      success: (group) => `????? Skill?${group.name}`,
    });
  }

  async function handleToggleSkill(skill: SkillRecord, enabled: boolean) {
    await runUiAction(() => setSkillEnabled(skill.name, enabled), {
      failure: "update skill failed",
      after: refreshSkills,
    });
  }

  async function handlePinSkill(skill: SkillRecord, pinned: boolean) {
    await runUiAction(() => setSkillPinned(skill.name, pinned), {
      failure: "update skill failed",
      after: refreshSkills,
    });
  }

  async function handleReorderSkills(names: string[]) {
    setSkills((current) => sortRecordsByNames(current, names));
    await runUiAction(() => reorderSkills(names), {
      failure: "reorder skills failed",
      after: setSkills,
      recover: refreshSkills,
    });
  }

  async function handleDeleteSkill(skill: SkillRecord) {
    if (skill.protected) return;
    await runUiAction(() => deleteSkill(skill.name), {
      failure: "delete skill failed",
      after: refreshSkills,
      success: `${skill.name} skill deleted`,
    });
  }

  async function handleAddHttpMcpServer() {
    const name = window.prompt("MCP name")?.trim();
    if (!name) return;
    const url = window.prompt("MCP HTTP URL")?.trim();
    if (!url) return;

    await runUiAction(() => createHttpMcpServer({ name, url }), {
      failure: "create MCP server failed",
      after: refreshMcpServers,
      success: (server) => `${server.name} MCP added`,
    });
  }

  async function handleToggleMcpServer(server: McpServerRecord, enabled: boolean) {
    await runUiAction(() => setMcpServerEnabled(server.name, enabled), {
      failure: "update MCP server failed",
      after: refreshMcpServers,
    });
  }

  async function handlePinMcpServer(server: McpServerRecord, pinned: boolean) {
    await runUiAction(() => setMcpServerPinned(server.name, pinned), {
      failure: "update MCP server failed",
      after: refreshMcpServers,
    });
  }

  async function handleReorderMcpServers(names: string[]) {
    setMcpServers((current) => sortRecordsByNames(current, names));
    await runUiAction(() => reorderMcpServers(names), {
      failure: "reorder MCP servers failed",
      after: setMcpServers,
      recover: refreshMcpServers,
    });
  }

  async function handleDeleteMcpServer(server: McpServerRecord) {
    if (server.protected) return;
    await runUiAction(() => deleteMcpServer(server.name), {
      failure: "delete MCP server failed",
      after: refreshMcpServers,
      success: `${server.name} MCP deleted`,
    });
  }

  async function handleAddMemory(content: string, kind: MemoryKind) {
    const trimmed = content.trim();
    if (!trimmed) return;
    await runUiAction(() => createMemory(trimmed, kind), {
      failure: "create memory failed",
      after: refreshMemories,
      success: "?????",
    });
  }

  async function handleEditMemory(
    memory: MemoryRecord,
    content: string,
    kind: MemoryKind,
  ) {
    const trimmed = content.trim();
    if (!trimmed) return;
    await runUiAction(() => updateMemory(memory.id, trimmed, kind), {
      failure: "update memory failed",
      after: refreshMemories,
      success: "?????",
    });
  }

  async function handleDeleteMemory(memory: MemoryRecord) {
    setMemories((current) => current.filter((item) => item.id !== memory.id));
    await runUiAction(() => deleteMemory(memory.id), {
      failure: "delete memory failed",
      after: refreshMemories,
      recover: refreshMemories,
      success: "?????",
    });
  }

  function handlePreviewArtifact(artifact: WorkspaceEntryRecord) {
    if (artifact.kind !== "file") return;
    setSelectedArtifactPath(artifact.path);
    setIsArtifactPanelOpen(true);
    setPanelRequest({ mode: "files", nonce: Date.now() });
  }

  async function handleOpenWorkspaceFile(threadId: string, file: WorkspaceEntryRecord) {
    if (file.kind !== "file") {
      return;
    }

    if (threadId !== "__legacy_artifacts__" && threadId !== thread?.id) {
      let targetThread = threads.find((item) => item.id === threadId);
      if (!targetThread) {
        const nextThreads = await refreshThreads();
        targetThread = nextThreads.find((item) => item.id === threadId);
      }

      if (!targetThread) {
        toast.error("找不到对应对话");
        return;
      }

      const confirmed = window.confirm(
        `该文件属于另一个对话「${targetThread.title || "未命名会话"}」。\n` +
          "切换过去查看吗？当前未发送的附件会被清空。",
      );
      if (!confirmed) {
        return;
      }

      const loaded = await loadThread(targetThread);
      if (!loaded) {
        toast.error("打开对应对话失败");
        return;
      }
      setAttachments([]);
    }

    setSelectedArtifactPath(file.path);
    setIsArtifactPanelOpen(true);
    setPanelRequest({ mode: "files", nonce: Date.now() });
    setWorkspaceRefreshKey((current) => current + 1);
  }

  function handleOpenWorkspaceDirectory() {
    setIsWorkspaceDirectoryOpen(true);
    setWorkspaceRefreshKey((current) => current + 1);
  }

  function handleOpenWorkspacePanel(mode: "files" | "terminal") {
    setIsArtifactPanelOpen(true);
    setPanelRequest({ mode, nonce: Date.now() });
  }

  async function handleDeleteArtifact(artifact: WorkspaceEntryRecord) {
    if (artifact.kind !== "file") {
      return;
    }

    try {
      await deleteArtifact(artifact.path);
      const nextArtifacts = await refreshArtifacts();
      setWorkspaceRefreshKey((current) => current + 1);
      setThreadArtifactPaths((current) => removeArtifactPathFromThreadIndex(current, artifact.path));
      if (selectedArtifactPath === artifact.path) {
        setSelectedArtifactPath(null);
        const nextFile = thread
          ? filterThreadArtifacts(
              thread.id,
              nextArtifacts.filter((item) => item.kind === "file"),
              removeArtifactPathFromThreadIndex(threadArtifactPaths, artifact.path),
              messages,
            )[0]
          : undefined;
        if (nextFile && isArtifactPanelOpen) {
          handlePreviewArtifact(nextFile);
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

    if (selectedArtifactPath) {
      return;
    }

    const firstFile = activeThreadArtifactFiles[0];
    if (firstFile) {
      handlePreviewArtifact(firstFile);
    }
  }, [activeThreadArtifactFiles, isArtifactPanelOpen, selectedArtifactPath]);

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
    await submitMessage(option.label, {
      files: [],
      metadata: {
        source: "clarification-choice",
        clarification_id: clarification.id,
        clarification_question: clarification.question,
        option_id: option.id,
        option_label: option.label,
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
      todoListKey={todoListKey}
      error={error}
      fileInputRef={fileInputRef}
      isStreaming={isStreaming}
      isUploading={isUploading}
      queuedMessages={queuedForActiveThread.map((message, index) => ({
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
    <SidebarProvider className="slotflow-app-bg h-dvh min-h-0 overflow-hidden text-foreground">
      <input
        ref={skillFolderInputRef}
        type="file"
        multiple
        {...{ webkitdirectory: "", directory: "" }}
        className="hidden"
        onChange={(event) => void handleSkillFolderChange(event)}
      />
      <WorkspaceDirectoryModal
        open={isWorkspaceDirectoryOpen}
        onOpenChange={setIsWorkspaceDirectoryOpen}
        onOpenFile={(threadId, file) => void handleOpenWorkspaceFile(threadId, file)}
      />
      <Sidebar collapsible="icon" resizable className="border-r-0">
        <ThreadSidebar
          activeThreadId={thread?.id ?? null}
          disabled={false}
          runStates={runStates}
          filteredThreads={threads}
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
          onDeleteThread={(targetThread) => void handleDeleteThread(targetThread)}
          onEditMemory={(memory, content, kind) => void handleEditMemory(memory, content, kind)}
          onInstallSkill={handleInstallSkillFromRegistry}
          onGroupSkills={handleGroupSkills}
          onNewThread={handleNewThread}
          onOpenWorkspaceDirectory={handleOpenWorkspaceDirectory}
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

      <SidebarInset className="h-dvh min-h-0 overflow-hidden bg-transparent">
        <div className="flex min-h-0 flex-1 overflow-hidden">
          <section className="slotflow-surface relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden border-l border-border/30">
            {!isArtifactPanelOpen ? (
              <div className="slotflow-rise-in absolute right-3 top-3 z-20 flex items-center gap-1 rounded-lg border bg-background/95 p-0.5 shadow-sm backdrop-blur">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  title="打开工作区面板"
                  className="slotflow-hover-lift"
                  onClick={() => handleOpenWorkspacePanel("files")}
                >
                  <PanelRightOpen className="size-4" />
                  <span className="sr-only">打开工作区面板</span>
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  title="打开终端"
                  className="slotflow-hover-lift"
                  onClick={() => handleOpenWorkspacePanel("terminal")}
                >
                  <SquareTerminal className="size-4" />
                  <span className="sr-only">打开终端</span>
                </Button>
              </div>
            ) : null}
            {messages.length === 0 ? (
              <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-3 pb-16 sm:px-6">
                <div className="w-full max-w-3xl -translate-y-6">
                  <EmptyState onSuggestion={(prompt) => void submitMessage(prompt)} />
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
                <div className="shrink-0 px-3 pb-5 pt-3 sm:px-6">
                  {composer}
                </div>
              </>
            )}
          </section>

          <WorkspacePanel
            open={isArtifactPanelOpen}
            selectedPath={selectedArtifactPath}
            width={artifactPanelWidth}
            refreshKey={workspaceRefreshKey}
            requestedMode={panelRequest}
            onClose={() => setIsArtifactPanelOpen(false)}
            onOpenFile={(threadId, file) => void handleOpenWorkspaceFile(threadId, file)}
            onWidthChange={setArtifactPanelWidth}
          />
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}
