import { type ChatUiMessage } from "@/hooks/use-chat-stream";

export type MessageFile = {
  id: string;
  filename: string;
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
