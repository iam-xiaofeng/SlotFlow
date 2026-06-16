import { type AnchorHTMLAttributes, type ReactNode } from "react";
import { Link2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { cn } from "@/lib/utils";

import { normalizeMathForMarkdown } from "./chat-format";

type MarkdownContentProps = {
  className?: string;
  compact?: boolean;
  content: string;
};

export function MarkdownContent({
  className,
  compact = false,
  content,
}: MarkdownContentProps) {
  return (
    <div className={cn("slotflow-markdown", compact && "text-sm leading-6", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { strict: false, throwOnError: false }]]}
        components={{
          a: SourceAnchor,
        }}
      >
        {autoLinkBareUrls(normalizeMathForMarkdown(content))}
      </ReactMarkdown>
    </div>
  );
}

function SourceAnchor({
  href,
  children,
}: AnchorHTMLAttributes<HTMLAnchorElement>) {
  const label = childrenToText(children).trim();
  const isBareSource = !label || label === href;

  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className={cn(
        "mx-0.5 inline-flex items-center justify-center align-baseline text-muted-foreground underline-offset-2 hover:text-foreground hover:underline",
        isBareSource
          ? "size-5 rounded-md border bg-muted"
          : "gap-1 rounded-md px-1",
      )}
      title={href}
    >
      <Link2 className="size-3.5 shrink-0" />
      {isBareSource ? <span className="sr-only">来源链接</span> : <span>{children}</span>}
    </a>
  );
}

function childrenToText(children: ReactNode): string {
  if (children === null || children === undefined || typeof children === "boolean") {
    return "";
  }
  if (typeof children === "string" || typeof children === "number") {
    return String(children);
  }
  if (Array.isArray(children)) {
    return children.map(childrenToText).join("");
  }
  return "";
}

function autoLinkBareUrls(content: string): string {
  return content.replace(
    /(^|\s)(https?:\/\/[^\s<>)]+)/g,
    (_match, prefix: string, url: string) => `${prefix}[${url}](${url})`,
  );
}
