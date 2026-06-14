import { type RefObject } from "react";
import { FileText } from "lucide-react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { ScrollArea } from "@/components/ui/scroll-area";
import { type ChatUiMessage } from "@/hooks/use-chat-stream";
import { cn } from "@/lib/utils";

import {
  formatFileSize,
  getMessageFiles,
  displayFileName,
  type MessageFile,
  normalizeMathForMarkdown,
} from "./chat-format";

type MessageListProps = {
  messages: ChatUiMessage[];
  messagesEndRef: RefObject<HTMLDivElement | null>;
};

export function MessageList({ messages, messagesEndRef }: MessageListProps) {
  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-4 py-6 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-5">
          {messages.map((message) => (
            <MessageBubble key={message.id} message={message} />
          ))}
        </div>
        <div ref={messagesEndRef} />
      </div>
    </ScrollArea>
  );
}

export function EmptyState() {
  return (
    <div className="grid flex-1 place-items-center px-4 py-16 text-center">
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
        有什么可以帮忙的？
      </h1>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatUiMessage }) {
  const isUser = message.role === "user";
  const files = getMessageFiles(message);
  const content = message.content || (message.status === "streaming" ? "..." : "");

  return (
    <article className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "flex min-w-0 flex-col gap-2 text-base leading-7",
          isUser
            ? "max-w-[82%] items-end text-foreground"
            : "w-full max-w-3xl px-1 text-foreground",
        )}
      >
        {files.length > 0 ? <MessageAttachments files={files} /> : null}
        <div
          className={cn(
            "min-w-0 break-words",
            isUser ? "rounded-2xl bg-muted px-4 py-2.5" : "w-full",
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{content}</p>
          ) : (
            <MarkdownContent content={content} />
          )}
        </div>
      </div>
    </article>
  );
}

function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="slotflow-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { strict: false, throwOnError: false }]]}
      >
        {normalizeMathForMarkdown(content)}
      </ReactMarkdown>
    </div>
  );
}

function MessageAttachments({ files }: { files: MessageFile[] }) {
  return (
    <div className="flex max-w-full flex-wrap justify-end gap-2">
      {files.map((file) => (
        <div
          key={file.id}
          className="flex max-w-72 items-center gap-3 rounded-2xl border border-border bg-card px-3 py-2 text-left shadow-sm"
        >
          <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-muted">
            <FileText className="size-5" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">{displayFileName(file)}</div>
            {typeof file.size_bytes === "number" ? (
              <div className="text-xs text-muted-foreground">
                {formatFileSize(file.size_bytes)}
              </div>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}
