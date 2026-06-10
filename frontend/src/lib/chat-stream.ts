import { drainSseBuffer, type ChatStreamEvent } from "@/lib/sse-parser";

export type { ChatStreamEvent, ChatStreamEventName } from "@/lib/sse-parser";

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
  content_type?: string | null;
  size_bytes: number;
  workspace_path: string;
  created_at: string;
};

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

export async function* streamThreadRun(
  threadId: string,
  body: ChatStreamRequest,
  options: ChatRequestOptions = {},
): AsyncGenerator<ChatStreamEvent> {
  const response = await fetch(`/api/chat/threads/${threadId}/runs/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: options.signal,
  });

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
