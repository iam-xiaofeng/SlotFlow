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

export type ChatStreamEventName =
  | "run.prepared"
  | "message.delta"
  | "tool.delta"
  | "state.snapshot"
  | "run.finished"
  | "run.error";

export type ChatStreamEvent = {
  event: ChatStreamEventName;
  data: Record<string, unknown>;
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

function drainSseBuffer(
  buffer: string,
  options: { flush?: boolean } = {},
): { events: ChatStreamEvent[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const events: ChatStreamEvent[] = [];
  let rest = normalized;

  while (true) {
    const boundary = rest.indexOf("\n\n");
    if (boundary === -1) {
      break;
    }

    const frame = rest.slice(0, boundary);
    rest = rest.slice(boundary + 2);

    const event = parseSseFrame(frame);
    if (event) {
      events.push(event);
    }
  }

  if (options.flush && rest.trim()) {
    const event = parseSseFrame(rest);
    if (event) {
      events.push(event);
    }
    rest = "";
  }

  return { events, rest };
}

function parseSseFrame(frame: string): ChatStreamEvent | null {
  const lines = frame.split("\n");
  const eventLine = lines.find((line) => line.startsWith("event:"));
  const dataLines = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trimStart());

  if (!eventLine || dataLines.length === 0) {
    return null;
  }

  const event = eventLine.slice("event:".length).trim() as ChatStreamEventName;
  const rawData = dataLines.join("\n");

  return {
    event,
    data: JSON.parse(rawData) as Record<string, unknown>,
  };
}
