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
import {
  type ChatMode,
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
  deleteMcpServer,
  deleteMemory,
  deleteSkill,
  installSkill,
  listArtifacts,
  listMcpServers,
  listMemories,
  listSkills,
  listThreads,
  readArtifact,
  setMcpServerEnabled,
  setSkillEnabled,
  updateMemory,
  uploadFile,
  uploadSkillFolder,
} from "@/lib/chat-stream";

import { ArtifactWorkspacePanel } from "./artifact-panel";
import { ChatComposer } from "./chat-composer";
import { makeThreadTitle } from "./chat-format";
import { ThreadSidebar, UserMenu } from "./chat-sidebar";
import { EmptyState, MessageList } from "./message-list";

const defaultModelName = "deepseek-v4-flash";
const defaultMode: ChatMode = "pro";

export function ChatApp() {
  const [threads, setThreads] = useState<ThreadRecord[]>([]);
  const [threadQuery, setThreadQuery] = useState("");
  const [threadListError, setThreadListError] = useState<string | null>(null);
  const [isLoadingThreads, setIsLoadingThreads] = useState(true);
  const [attachments, setAttachments] = useState<UploadedFileRecord[]>([]);
  const [artifacts, setArtifacts] = useState<WorkspaceEntryRecord[]>([]);
  const [skills, setSkills] = useState<SkillRecord[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerRecord[]>([]);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [artifactPreview, setArtifactPreview] = useState<WorkspaceReadRecord | null>(null);
  const [artifactPreviewError, setArtifactPreviewError] = useState<string | null>(null);
  const [isLoadingArtifactPreview, setIsLoadingArtifactPreview] = useState(false);
  const [isArtifactPanelOpen, setIsArtifactPanelOpen] = useState(false);
  const [artifactPanelWidth, setArtifactPanelWidth] = useState(560);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const skillFolderInputRef = useRef<HTMLInputElement | null>(null);
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
      if (nextThreads[0]) {
        await loadThread(nextThreads[0]);
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
  }, [loadThread, refreshArtifacts, refreshMcpServers, refreshMemories, refreshSkills, refreshThreads]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

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
      setAttachments([]);
    }
  }

  async function submitMessage(rawText: string): Promise<boolean> {
    const text = rawText.trim();
    if (!text || isStreaming || isUploading) {
      return false;
    }

    const currentAttachments = attachments;
    const previousArtifactPaths = new Set(artifactFiles.map((artifact) => artifact.path));
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
      const [, nextArtifacts] = await Promise.all([
        refreshThreads(),
        refreshArtifacts(),
        refreshSkills(),
        refreshMcpServers(),
        refreshMemories(),
      ]);
      const newArtifact = nextArtifacts.find(
        (artifact) => artifact.kind === "file" && !previousArtifactPaths.has(artifact.path),
      );
      if (newArtifact) {
        void handlePreviewArtifact(newArtifact);
      }
    } else {
      setAttachments(currentAttachments);
    }
    return result.accepted;
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

  function handleRemoveAttachment(fileId: string) {
    setAttachments((current) => current.filter((item) => item.id !== fileId));
  }

  const composer = (
    <ChatComposer
      attachments={attachments}
      error={error}
      fileInputRef={fileInputRef}
      isStreaming={isStreaming}
      isUploading={isUploading}
      onAttachFiles={() => fileInputRef.current?.click()}
      onCancel={cancelStream}
      onClearError={clearError}
      onFileChange={handleFileChange}
      onRemoveAttachment={handleRemoveAttachment}
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
          disabled={isStreaming}
          filteredThreads={filteredThreads}
          artifacts={artifacts}
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
          onEditMemory={(memory, content, kind) => void handleEditMemory(memory, content, kind)}
          onInstallSkill={() => void handleInstallSkillFromRegistry()}
          onNewThread={handleNewThread}
          onOpenArtifacts={handleOpenArtifactPanel}
          onPreviewArtifact={(artifact) => void handlePreviewArtifact(artifact)}
          onQueryChange={setThreadQuery}
          onToggleMcpServer={(server, enabled) => void handleToggleMcpServer(server, enabled)}
          onToggleSkill={(skill, enabled) => void handleToggleSkill(skill, enabled)}
          onUploadSkill={() => skillFolderInputRef.current?.click()}
          onSelectThread={(nextThread) => void handleSelectThread(nextThread)}
        />
      </Sidebar>

      <SidebarInset className="h-dvh min-h-0 overflow-hidden">
        <header className="flex h-14 shrink-0 items-center justify-end bg-background px-3 sm:px-4">
          <UserMenu />
        </header>

        <div className="flex min-h-0 flex-1">
          <section className="flex min-w-0 flex-1 flex-col">
            {messages.length === 0 ? (
              <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-3 pb-16 sm:px-6">
                <div className="w-full max-w-3xl -translate-y-10">
                  <EmptyState />
                  {composer}
                </div>
              </div>
            ) : (
              <>
                <MessageList messages={messages} messagesEndRef={messagesEndRef} />
                <div className="shrink-0 bg-background px-3 pb-5 pt-3 sm:px-6">
                  {composer}
                </div>
              </>
            )}
          </section>

          {isArtifactPanelOpen && artifactFiles.length > 0 ? (
            <ArtifactWorkspacePanel
              artifacts={artifactFiles}
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
