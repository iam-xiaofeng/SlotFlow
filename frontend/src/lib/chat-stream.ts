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

export type ChatMode = "flash" | "pro" | "ultra";

export type ChatStreamRequest = {
  message: string;
  model_name?: string;
  mode?: ChatMode;
  agent_name?: string;
  files?: string[];
  metadata?: Record<string, unknown>;
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

export type SkillRecord = {
  name: string;
  description: string;
  path: string;
  enabled: boolean;
  protected: boolean;
  source: string;
};

export type McpServerRecord = {
  name: string;
  enabled: boolean;
  transport?: string | null;
  url?: string | null;
  source: "environment" | "user";
  protected: boolean;
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
  options: ChatRequestOptions = {},
): Promise<WorkspaceEntryRecord[]> {
  const response = await fetch("/api/workspace/artifacts", {
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`list artifacts failed: ${response.status}`);
  }

  return response.json() as Promise<WorkspaceEntryRecord[]>;
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

export async function setSkillEnabled(
  skillName: string,
  enabled: boolean,
  options: ChatRequestOptions = {},
): Promise<SkillRecord> {
  const response = await fetch(`/api/skills/${encodeURIComponent(skillName)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ enabled }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`update skill failed: ${response.status}`);
  }

  return response.json() as Promise<SkillRecord>;
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

export async function setMcpServerEnabled(
  serverName: string,
  enabled: boolean,
  options: ChatRequestOptions = {},
): Promise<McpServerRecord> {
  const response = await fetch(`/api/mcp/servers/${encodeURIComponent(serverName)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ enabled }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`update MCP server failed: ${response.status}`);
  }

  return response.json() as Promise<McpServerRecord>;
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
