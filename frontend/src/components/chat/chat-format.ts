import { type ChatUiMessage } from "@/hooks/use-chat-stream";
import { type WorkspaceEntryRecord } from "@/lib/chat-stream";

export type MessageFile = {
  id: string;
  filename: string;
  original_filename?: string | null;
  content_type?: string | null;
  size_bytes?: number;
};

export function makeThreadTitle(message: string) {
  const compact = message.replace(/\s+/g, " ").trim();
  if (compact.length <= 48) {
    return compact || "New chat";
  }
  return `${compact.slice(0, 45)}...`;
}

export function formatFileSize(sizeBytes: number) {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function normalizeMathForMarkdown(content: string) {
  return content
    .replace(/\\\[([\s\S]*?)\\\]/g, (_match, math: string) => {
      return `\n$$\n${math.trim()}\n$$\n`;
    })
    .replace(/\\\(([\s\S]*?)\\\)/g, (_match, math: string) => {
      return `$${math.trim()}$`;
    })
    .replace(
      /(^|\n)\s*\[\s*(\\begin\{([a-zA-Z*]+)\}[\s\S]*?\\end\{\3\})\s*\]\s*(?=\n|$)/g,
      (_match, prefix: string, math: string) => {
        return `${prefix}$$\n${math.trim()}\n$$`;
      },
    )
    .replace(
      /\[\s*(\\begin\{([a-zA-Z*]+)\}[\s\S]*?\\end\{\2\})\s*\]/g,
      (_match, math: string) => {
        return `\n$$\n${math.trim()}\n$$\n`;
      },
    );
}

export function getMessageFiles(message: ChatUiMessage): MessageFile[] {
  const uploadedFiles = message.metadata?.uploaded_files;
  if (!Array.isArray(uploadedFiles)) {
    return [];
  }

  return uploadedFiles.flatMap((item) => {
    if (
      typeof item === "object" &&
      item !== null &&
      "id" in item &&
      "filename" in item &&
      typeof item.id === "string" &&
      typeof item.filename === "string"
    ) {
      return [
        {
          id: item.id,
          filename: item.filename,
          original_filename:
            "original_filename" in item &&
            (typeof item.original_filename === "string" ||
              item.original_filename === null)
              ? item.original_filename
              : undefined,
          content_type:
            "content_type" in item &&
            (typeof item.content_type === "string" || item.content_type === null)
              ? item.content_type
              : undefined,
          size_bytes:
            "size_bytes" in item && typeof item.size_bytes === "number"
              ? item.size_bytes
              : undefined,
        },
      ];
    }
    return [];
  });
}

export function isImageFile(file: {
  filename: string;
  content_type?: string | null;
}) {
  return (
    file.content_type?.startsWith("image/") ||
    /\.(png|jpe?g|gif|webp)$/i.test(file.filename)
  );
}

export function displayFileName(file: {
  filename: string;
  original_filename?: string | null;
}) {
  return file.original_filename || file.filename;
}

export function filterThreadArtifacts(
  threadId: string,
  artifacts: WorkspaceEntryRecord[],
  threadArtifactPaths: Record<string, string[]>,
  messages: Pick<ChatUiMessage, "content">[] = [],
): WorkspaceEntryRecord[] {
  const explicitPaths = new Set(threadArtifactPaths[threadId] ?? []);
  const threadPrefix = `artifacts/${threadId}/`;
  const messageText = messages.map((message) => message.content).join("\n");
  return artifacts.filter(
    (artifact) =>
      explicitPaths.has(artifact.path) ||
      artifact.path.startsWith(threadPrefix) ||
      messageText.includes(artifact.path) ||
      messageText.includes(artifact.path.replace(/^artifacts\//, "")),
  );
}
