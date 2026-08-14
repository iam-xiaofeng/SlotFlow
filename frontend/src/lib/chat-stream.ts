import { requestJson, requestOk } from "@/lib/api-client";
import { drainSseBuffer, type ChatStreamEvent } from "@/lib/sse-parser";

export type { ChatStreamEvent } from "@/lib/sse-parser";

const localDevStreamBaseUrl = "http://127.0.0.1:8000";

export type ThreadRecord = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type MessageRecord = {
  id: string;
  thread_id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  run_id?: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type ThreadSearchResultRecord = {
  thread: ThreadRecord;
  message?: MessageRecord | null;
  match_type: "title" | "message";
  snippet: string;
  score: number;
};

export type ClarificationOptionRecord = {
  id: string;
  label: string;
};

export type ClarificationRequestRecord = {
  type: "clarification";
  id: string;
  question: string;
  clarification_type: string;
  context?: string | null;
  options: ClarificationOptionRecord[];
  source: string;
  thread_id?: string | null;
  run_id?: string | null;
};

export type ChatMode = "flash" | "pro" | "ultra";

export type ModelProvider = string;

export type ModelOptionRecord = {
  id: string;
  provider: ModelProvider;
  label: string;
  available: boolean;
  source: "api" | "fallback" | "catalog" | string;
};

type ModelProviderRecord = {
  provider: ModelProvider;
  configured: boolean;
  base_url?: string | null;
  status: "available" | "fallback" | "missing" | "error";
  message?: string | null;
  models: ModelOptionRecord[];
};

export type ModelCatalogRecord = {
  default_model: string;
  providers: ModelProviderRecord[];
};

export type ChatStreamRequest = {
  message: string;
  model_name?: string;
  provider?: ModelProvider;
  mode?: ChatMode;
  thinking_enabled?: boolean;
  agent_name?: string;
  files?: string[];
  metadata?: Record<string, unknown>;
  reuse_user_message_id?: string | null;
};

export type UploadedFileRecord = {
  id: string;
  filename: string;
  original_filename?: string | null;
  content_type?: string | null;
  size_bytes: number;
  workspace_path: string;
  created_at: string;
};

export type WorkspaceEntryRecord = {
  path: string;
  kind: "file" | "directory";
  size_bytes?: number | null;
};

export type WorkspaceReadRecord = {
  path: string;
  kind:
    | "text"
    | "document"
    | "spreadsheet"
    | "presentation"
    | "diagram"
    | "pdf"
    | "image"
    | "binary";
  media_type: string;
  size_bytes: number;
  source: string;
  metadata: Record<string, unknown>;
  content?: string | null;
  warning?: string | null;
};

export type ThreadWorkspaceRecord = {
  thread_id: string;
  title: string;
  generated: WorkspaceEntryRecord[];
  uploads: WorkspaceEntryRecord[];
};

export type SkillRecord = {
  name: string;
  description: string;
  path: string;
  enabled: boolean;
  protected: boolean;
  source: string;
  order: number;
  pinned: boolean;
  parent?: string | null;
};

export type SkillInstallRequest = {
  package_url: string;
  skill_name: string;
};

export type McpServerRecord = {
  name: string;
  enabled: boolean;
  transport?: string | null;
  url?: string | null;
  source: "environment" | "user";
  protected: boolean;
  order: number;
  pinned: boolean;
  stateful: boolean;
};

export type MemoryRecord = {
  id: string;
  thread_id?: string | null;
  kind: MemoryKind;
  content: string;
  source_run_id?: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type MemoryKind = "manual" | "preference" | "profile" | "topic" | "fact";

type ChatRequestOptions = {
  signal?: AbortSignal;
};

export async function listChatModels(
  options: ChatRequestOptions = {},
): Promise<ModelCatalogRecord> {
  return requestJson<ModelCatalogRecord>("list chat models failed", resolveChatStreamUrl("/api/chat/models"), {
    signal: options.signal,
  });
}

export async function createThread(
  title?: string,
  options: ChatRequestOptions = {},
): Promise<ThreadRecord> {
  return requestJson<ThreadRecord>("create thread failed", "/api/chat/threads", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ title }),
    signal: options.signal,
  });
}

export async function listThreads(
  options: ChatRequestOptions = {},
): Promise<ThreadRecord[]> {
  return requestJson<ThreadRecord[]>("list threads failed", "/api/chat/threads", {
    signal: options.signal,
  });
}

export async function searchThreads(
  query: string,
  options: ChatRequestOptions = {},
): Promise<ThreadSearchResultRecord[]> {
  const params = new URLSearchParams({
    q: query,
    limit: "30",
  });
  return requestJson<ThreadSearchResultRecord[]>("search threads failed", `/api/chat/search?${params.toString()}`, {
    signal: options.signal,
  });
}

export async function deleteThread(
  threadId: string,
  options: ChatRequestOptions = {},
): Promise<void> {
  await requestOk("delete thread failed", `/api/chat/threads/${threadId}`, {
    method: "DELETE",
    signal: options.signal,
  });
}

export async function listThreadMessages(
  threadId: string,
  options: ChatRequestOptions = {},
): Promise<MessageRecord[]> {
  return requestJson<MessageRecord[]>("list messages failed", `/api/chat/threads/${threadId}/messages`, {
    signal: options.signal,
  });
}

// 只作为 getThreadContextUsage 的返回类型在本模块内使用,不对外导出(knip 死代码检查会抓)。
type ThreadContextUsageRecord = {
  thread_id: string;
  run_id: string | null;
  context_tokens: number | null;
  context_cached_tokens: number | null;
  context_window_tokens: number | null;
  context_input_budget_tokens: number | null;
  context_window_source: string | null;
};

export async function getThreadContextUsage(
  threadId: string,
  options: ChatRequestOptions = {},
): Promise<ThreadContextUsageRecord> {
  return requestJson<ThreadContextUsageRecord>(
    "load context usage failed",
    `/api/chat/threads/${threadId}/context-usage`,
    { signal: options.signal },
  );
}

export async function uploadFile(
  file: File,
  options: ChatRequestOptions = {},
): Promise<UploadedFileRecord> {
  const formData = new FormData();
  formData.append("file", file);

  return requestJson<UploadedFileRecord>("upload file failed", "/api/uploads", {
    method: "POST",
    body: formData,
    signal: options.signal,
  });
}

export async function listArtifacts(
  options: ChatRequestOptions & { path?: string } = {},
): Promise<WorkspaceEntryRecord[]> {
  const params = new URLSearchParams();
  if (options.path) {
    params.set("path", options.path);
  }
  const query = params.toString();
  return requestJson<WorkspaceEntryRecord[]>("list artifacts failed",
    `/api/workspace/artifacts${query ? `?${query}` : ""}`,
    {
      signal: options.signal,
    },
  );
}

export async function listThreadWorkspaces(
  options: ChatRequestOptions = {},
): Promise<ThreadWorkspaceRecord[]> {
  return requestJson<ThreadWorkspaceRecord[]>("list thread workspaces failed", "/api/workspace/threads", {
    signal: options.signal,
  });
}

export async function readArtifact(
  path: string,
  options: ChatRequestOptions = {},
): Promise<WorkspaceReadRecord> {
  const params = new URLSearchParams({ path });
  return requestJson<WorkspaceReadRecord>("read artifact failed", `/api/workspace/artifacts/read?${params.toString()}`, {
    signal: options.signal,
  });
}

export function resolveArtifactRawUrl(
  path: string,
  options: { download?: boolean } = {},
): string {
  const params = new URLSearchParams({ path });
  if (options.download) {
    params.set("download", "true");
  }
  return joinBaseUrl(
    resolveChatStreamBaseUrl(),
    `/api/workspace/artifacts/raw?${params.toString()}`,
  );
}

export function resolveUploadRawUrl(fileId: string): string {
  return joinBaseUrl(resolveChatStreamBaseUrl(), `/api/uploads/${fileId}/raw`);
}

export async function deleteArtifact(
  path: string,
  options: ChatRequestOptions = {},
): Promise<void> {
  const params = new URLSearchParams({ path });
  await requestOk("delete artifact failed", `/api/workspace/artifacts?${params.toString()}`, {
    method: "DELETE",
    signal: options.signal,
  });
}

export async function listSkills(
  options: ChatRequestOptions = {},
): Promise<SkillRecord[]> {
  return requestJson<SkillRecord[]>("list skills failed", "/api/skills", {
    signal: options.signal,
  });
}

export async function uploadSkillFolder(
  files: File[],
  options: ChatRequestOptions = {},
): Promise<SkillRecord[]> {
  const formData = new FormData();
  for (const file of files) {
    const relativePath = (file as File & { webkitRelativePath?: string })
      .webkitRelativePath || file.name;
    formData.append("files", file, relativePath);
  }

  return requestJson<SkillRecord[]>("upload skill folder failed", "/api/skills/upload", {
    method: "POST",
    body: formData,
    signal: options.signal,
  });
}

export async function installSkill(
  body: SkillInstallRequest,
  options: ChatRequestOptions = {},
): Promise<SkillRecord> {
  return requestJson<SkillRecord>("install skill failed", "/api/skills/install", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

type SkillGroupRequest = {
  name: string;
  description: string;
  content?: string;
  members: string[];
};

export async function groupSkills(
  body: SkillGroupRequest,
  options: ChatRequestOptions = {},
): Promise<SkillRecord> {
  return requestJson<SkillRecord>("group skills failed", "/api/skills/group", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

async function updateSkillState(
  skillName: string,
  body: {
    enabled?: boolean;
    pinned?: boolean;
  },
  options: ChatRequestOptions = {},
): Promise<SkillRecord> {
  return requestJson<SkillRecord>("update skill failed", `/api/skills/${encodeURIComponent(skillName)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export async function setSkillEnabled(
  skillName: string,
  enabled: boolean,
  options: ChatRequestOptions = {},
): Promise<SkillRecord> {
  return updateSkillState(skillName, { enabled }, options);
}

export async function setSkillPinned(
  skillName: string,
  pinned: boolean,
  options: ChatRequestOptions = {},
): Promise<SkillRecord> {
  return updateSkillState(skillName, { pinned }, options);
}

export async function reorderSkills(
  names: string[],
  options: ChatRequestOptions = {},
): Promise<SkillRecord[]> {
  return requestJson<SkillRecord[]>("reorder skills failed", "/api/skills/reorder", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ names }),
    signal: options.signal,
  });
}

export async function deleteSkill(
  skillName: string,
  options: ChatRequestOptions = {},
): Promise<void> {
  await requestOk("delete skill failed", `/api/skills/${encodeURIComponent(skillName)}`, {
    method: "DELETE",
    signal: options.signal,
  });
}

export async function listMcpServers(
  options: ChatRequestOptions = {},
): Promise<McpServerRecord[]> {
  return requestJson<McpServerRecord[]>("list MCP servers failed", "/api/mcp/servers", {
    signal: options.signal,
  });
}

export async function createHttpMcpServer(
  body: {
    name: string;
    url: string;
  },
  options: ChatRequestOptions = {},
): Promise<McpServerRecord> {
  return requestJson<McpServerRecord>("create MCP server failed", "/api/mcp/servers", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

async function updateMcpServerState(
  serverName: string,
  body: {
    enabled?: boolean;
    pinned?: boolean;
  },
  options: ChatRequestOptions = {},
): Promise<McpServerRecord> {
  return requestJson<McpServerRecord>("update MCP server failed", `/api/mcp/servers/${encodeURIComponent(serverName)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export async function setMcpServerEnabled(
  serverName: string,
  enabled: boolean,
  options: ChatRequestOptions = {},
): Promise<McpServerRecord> {
  return updateMcpServerState(serverName, { enabled }, options);
}

export async function setMcpServerPinned(
  serverName: string,
  pinned: boolean,
  options: ChatRequestOptions = {},
): Promise<McpServerRecord> {
  return updateMcpServerState(serverName, { pinned }, options);
}

export async function reorderMcpServers(
  names: string[],
  options: ChatRequestOptions = {},
): Promise<McpServerRecord[]> {
  return requestJson<McpServerRecord[]>("reorder MCP servers failed", "/api/mcp/servers/reorder", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ names }),
    signal: options.signal,
  });
}

export async function deleteMcpServer(
  serverName: string,
  options: ChatRequestOptions = {},
): Promise<void> {
  await requestOk("delete MCP server failed", `/api/mcp/servers/${encodeURIComponent(serverName)}`, {
    method: "DELETE",
    signal: options.signal,
  });
}

export async function listMemories(
  options: ChatRequestOptions = {},
): Promise<MemoryRecord[]> {
  return requestJson<MemoryRecord[]>("list memories failed", "/api/memory", {
    signal: options.signal,
  });
}

export async function createMemory(
  content: string,
  kind: MemoryKind = "manual",
  options: ChatRequestOptions = {},
): Promise<MemoryRecord> {
  return requestJson<MemoryRecord>("create memory failed", "/api/memory", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content, kind }),
    signal: options.signal,
  });
}

export async function updateMemory(
  memoryId: string,
  content: string,
  kind?: MemoryKind,
  options: ChatRequestOptions = {},
): Promise<MemoryRecord> {
  return requestJson<MemoryRecord>("update memory failed", `/api/memory/${encodeURIComponent(memoryId)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content, kind }),
    signal: options.signal,
  });
}

export async function deleteMemory(
  memoryId: string,
  options: ChatRequestOptions = {},
): Promise<void> {
  await requestOk("delete memory failed", `/api/memory/${encodeURIComponent(memoryId)}`, {
    method: "DELETE",
    signal: options.signal,
  });
}

export async function* streamThreadRun(
  threadId: string,
  body: ChatStreamRequest,
  options: ChatRequestOptions = {},
): AsyncGenerator<ChatStreamEvent> {
  const response = await fetch(
    resolveChatStreamUrl(`/api/chat/threads/${threadId}/runs/stream`),
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: options.signal,
    },
  );

  if (!response.ok) {
    throw new Error(`stream run failed: ${response.status}`);
  }

  if (!response.body) {
    throw new Error("stream run failed: response body is empty");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const parsed = drainSseBuffer(buffer);
      buffer = parsed.rest;

      for (const event of parsed.events) {
        yield event;
      }
    }

    buffer += decoder.decode();
    const parsed = drainSseBuffer(buffer, { flush: true });
    for (const event of parsed.events) {
      yield event;
    }
  } finally {
    reader.releaseLock();
  }
}

function resolveChatStreamUrl(path: string): string {
  return joinBaseUrl(resolveChatStreamBaseUrl(), path);
}

export function resolveTerminalWebSocketUrl(): string {
  const httpUrl = resolveChatStreamUrl("/api/terminal/ws");
  if (typeof window === "undefined") {
    return httpUrl;
  }
  const url = new URL(httpUrl, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function resolveChatStreamBaseUrl(): string {
  const configuredBaseUrl =
    process.env.NEXT_PUBLIC_SLOTFLOW_STREAM_BASE_URL ??
    process.env.NEXT_PUBLIC_SLOTFLOW_API_BASE_URL;

  if (configuredBaseUrl) {
    return configuredBaseUrl;
  }

  if (isLocalBrowserHost()) {
    return localDevStreamBaseUrl;
  }

  return "";
}

function isLocalBrowserHost(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  return ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
}

function joinBaseUrl(baseUrl: string, path: string): string {
  if (!baseUrl) {
    return path;
  }

  return `${baseUrl.replace(/\/+$/, "")}${path}`;
}
