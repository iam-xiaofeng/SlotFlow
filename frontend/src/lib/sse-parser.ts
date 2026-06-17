export type ChatStreamEventName =
  | "run.prepared"
  | "context.compressing"
  | "message.delta"
  | "tool.delta"
  | "clarification.requested"
  | "todo.updated"
  | "state.snapshot"
  | "run.finished"
  | "run.error";

export type ChatStreamEvent = {
  event: ChatStreamEventName;
  data: Record<string, unknown>;
};

export type SseBufferDrainResult = {
  events: ChatStreamEvent[];
  rest: string;
};

export function drainSseBuffer(
  buffer: string,
  options: { flush?: boolean } = {},
): SseBufferDrainResult {
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

export function parseSseFrame(frame: string): ChatStreamEvent | null {
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
