import { drainSseBuffer, type ChatStreamEvent } from "@/lib/sse-parser";

export type { ChatStreamEvent, ChatStreamEventName } from "@/lib/sse-parser";

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

export type ModelProvider = "deepseek" | "openai" | "anthropic";

export type ModelOptionRecord = {
  id: string;
  provider: ModelProvider;
  label: string;
  available: boolean;
  source: "api" | "fallback" | "catalog" | string;
};

export type ModelProviderRecord = {
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
  kind: "text" | "document" | "pdf" | "image" | "binary";
  media_type: string;
  size_bytes: number;
  source: string;
  metadata: Record<string, unknown>;
  content?: string | null;
  warning?: string | null;
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

export type McpServerRecord = {
  name: string;
  enabled: boolean;
  transport?: string | null;
  url?: string | null;
  source: "environment" | "user";
  protected: boolean;
  order: number;
  pinned: boolean;
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

export type ChatRequestOptions = {
  signal?: AbortSignal;
};

export async function listChatModels(
  options: ChatRequestOptions = {},
): Promise<ModelCatalogRecord> {
  const response = await fetch(resolveChatStreamUrl("/api/chat/models"), {
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`list chat models failed: ${response.status}`);
  }

  return response.json() as Promise<ModelCatalogRecord>;
}

export async function createThread(
  title?: string,
  options: ChatRequestOptions = {},
): Promise<ThreadRecord> {
  const response = await fetch("/api/chat/threads", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ title }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`create thread failed: ${response.status}`);
  }

  return response.json() as Promise<ThreadRecord>;
}

export async function listThreads(
  options: ChatRequestOptions = {},
): Promise<ThreadRecord[]> {
  const response = await fetch("/api/chat/threads", {
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`list threads failed: ${response.status}`);
  }

  return response.json() as Promise<ThreadRecord[]>;
}

export async function searchThreads(
  query: string,
  options: ChatRequestOptions = {},
): Promise<ThreadSearchResultRecord[]> {
  const params = new URLSearchParams({
    q: query,
    limit: "30",
  });
  const response = await fetch(`/api/chat/search?${params.toString()}`, {
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`search threads failed: ${response.status}`);
  }

  return response.json() as Promise<ThreadSearchResultRecord[]>;
}

export async function getThread(
  threadId: string,
  options: ChatRequestOptions = {},
): Promise<ThreadRecord> {
  const response = await fetch(`/api/chat/threads/${threadId}`, {
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`get thread failed: ${response.status}`);
  }

  return response.json() as Promise<ThreadRecord>;
}

export async function deleteThread(
  threadId: string,
  options: ChatRequestOptions = {},
): Promise<void> {
  const response = await fetch(`/api/chat/threads/${threadId}`, {
    method: "DELETE",
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`delete thread failed: ${response.status}`);
  }
}

export async function listThreadMessages(
  threadId: string,
  options: ChatRequestOptions = {},
): Promise<MessageRecord[]> {
  const response = await fetch(`/api/chat/threads/${threadId}/messages`, {
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`list messages failed: ${response.status}`);
  }

  return response.json() as Promise<MessageRecord[]>;
}

export async function uploadFile(
  file: File,
  options: ChatRequestOptions = {},
): Promise<UploadedFileRecord> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch("/api/uploads", {
    method: "POST",
    body: formData,
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`upload file failed: ${response.status}`);
  }

  return response.json() as Promise<UploadedFileRecord>;
}

export async function listArtifacts(
  options: ChatRequestOptions & { path?: string } = {},
): Promise<WorkspaceEntryRecord[]> {
  const params = new URLSearchParams();
  if (options.path) {
    params.set("path", options.path);
  }
  const query = params.toString();
  const response = await fetch(
    `/api/workspace/artifacts${query ? `?${query}` : ""}`,
    {
      signal: options.signal,
    },
  );

  if (!response.ok) {
    throw new Error(`list artifacts failed: ${response.status}`);
  }

  return response.json() as Promise<WorkspaceEntryRecord[]>;
}

export async function readArtifact(
  path: string,
  options: ChatRequestOptions = {},
): Promise<WorkspaceReadRecord> {
  const params = new URLSearchParams({ path });
  const response = await fetch(`/api/workspace/artifacts/read?${params.toString()}`, {
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`read artifact failed: ${response.status}`);
  }

  return response.json() as Promise<WorkspaceReadRecord>;
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
  const response = await fetch(`/api/workspace/artifacts?${params.toString()}`, {
    method: "DELETE",
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`delete artifact failed: ${response.status}`);
  }
}

export async function listSkills(
  options: ChatRequestOptions = {},
): Promise<SkillRecord[]> {
  const response = await fetch("/api/skills", {
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`list skills failed: ${response.status}`);
  }

  return response.json() as Promise<SkillRecord[]>;
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

  const response = await fetch("/api/skills/upload", {
    method: "POST",
    body: formData,
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`upload skill folder failed: ${response.status}`);
  }

  return response.json() as Promise<SkillRecord[]>;
}

export async function installSkill(
  body: {
    package_url: string;
    skill_name: string;
  },
  options: ChatRequestOptions = {},
): Promise<SkillRecord> {
  const response = await fetch("/api/skills/install", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`install skill failed: ${response.status}`);
  }

  return response.json() as Promise<SkillRecord>;
}

async function updateSkillState(
  skillName: string,
  body: {
    enabled?: boolean;
    pinned?: boolean;
  },
  options: ChatRequestOptions = {},
): Promise<SkillRecord> {
  const response = await fetch(`/api/skills/${encodeURIComponent(skillName)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`update skill failed: ${response.status}`);
  }

  return response.json() as Promise<SkillRecord>;
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
  const response = await fetch("/api/skills/reorder", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ names }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`reorder skills failed: ${response.status}`);
  }

  return response.json() as Promise<SkillRecord[]>;
}

export async function deleteSkill(
  skillName: string,
  options: ChatRequestOptions = {},
): Promise<void> {
  const response = await fetch(`/api/skills/${encodeURIComponent(skillName)}`, {
    method: "DELETE",
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`delete skill failed: ${response.status}`);
  }
}

export async function listMcpServers(
  options: ChatRequestOptions = {},
): Promise<McpServerRecord[]> {
  const response = await fetch("/api/mcp/servers", {
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`list MCP servers failed: ${response.status}`);
  }

  return response.json() as Promise<McpServerRecord[]>;
}

export async function createHttpMcpServer(
  body: {
    name: string;
    url: string;
  },
  options: ChatRequestOptions = {},
): Promise<McpServerRecord> {
  const response = await fetch("/api/mcp/servers", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`create MCP server failed: ${response.status}`);
  }

  return response.json() as Promise<McpServerRecord>;
}

async function updateMcpServerState(
  serverName: string,
  body: {
    enabled?: boolean;
    pinned?: boolean;
  },
  options: ChatRequestOptions = {},
): Promise<McpServerRecord> {
  const response = await fetch(`/api/mcp/servers/${encodeURIComponent(serverName)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`update MCP server failed: ${response.status}`);
  }

  return response.json() as Promise<McpServerRecord>;
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
  const response = await fetch("/api/mcp/servers/reorder", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ names }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`reorder MCP servers failed: ${response.status}`);
  }

  return response.json() as Promise<McpServerRecord[]>;
}

export async function deleteMcpServer(
  serverName: string,
  options: ChatRequestOptions = {},
): Promise<void> {
  const response = await fetch(`/api/mcp/servers/${encodeURIComponent(serverName)}`, {
    method: "DELETE",
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`delete MCP server failed: ${response.status}`);
  }
}

export async function listMemories(
  options: ChatRequestOptions = {},
): Promise<MemoryRecord[]> {
  const response = await fetch("/api/memory", {
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`list memories failed: ${response.status}`);
  }

  return response.json() as Promise<MemoryRecord[]>;
}

export async function createMemory(
  content: string,
  kind: MemoryKind = "manual",
  options: ChatRequestOptions = {},
): Promise<MemoryRecord> {
  const response = await fetch("/api/memory", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content, kind }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`create memory failed: ${response.status}`);
  }

  return response.json() as Promise<MemoryRecord>;
}

export async function updateMemory(
  memoryId: string,
  content: string,
  kind?: MemoryKind,
  options: ChatRequestOptions = {},
): Promise<MemoryRecord> {
  const response = await fetch(`/api/memory/${encodeURIComponent(memoryId)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content, kind }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`update memory failed: ${response.status}`);
  }

  return response.json() as Promise<MemoryRecord>;
}

export async function deleteMemory(
  memoryId: string,
  options: ChatRequestOptions = {},
): Promise<void> {
  const response = await fetch(`/api/memory/${encodeURIComponent(memoryId)}`, {
    method: "DELETE",
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`delete memory failed: ${response.status}`);
  }
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

export function resolveChatStreamUrl(path: string): string {
  return joinBaseUrl(resolveChatStreamBaseUrl(), path);
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
