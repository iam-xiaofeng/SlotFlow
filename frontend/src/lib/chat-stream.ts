import { drainSseBuffer, type ChatStreamEvent } from "@/lib/sse-parser";

export type { ChatStreamEvent, ChatStreamEventName } from "@/lib/sse-parser";

export type ThreadRecord = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
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

export async function createThread(title?: string): Promise<ThreadRecord> {
  const response = await fetch("/api/chat/threads", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ title }),
  });

  if (!response.ok) {
    throw new Error(`create thread failed: ${response.status}`);
  }

  return response.json() as Promise<ThreadRecord>;
}

export async function* streamThreadRun(
  threadId: string,
  body: ChatStreamRequest,
): AsyncGenerator<ChatStreamEvent> {
  const response = await fetch(`/api/chat/threads/${threadId}/runs/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
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
}
