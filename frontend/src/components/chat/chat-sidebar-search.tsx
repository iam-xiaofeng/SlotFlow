"use client";

import { useEffect, useState } from "react";
import { MessageSquarePlus, Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  type ThreadRecord,
  type ThreadSearchResultRecord,
  searchThreads,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";

export function SearchMenu({
  query,
  triggerClassName,
  onQueryChange,
  onSelectThread,
}: {
  query: string;
  triggerClassName?: string;
  onQueryChange: (query: string) => void;
  onSelectThread: (thread: ThreadRecord) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [results, setResults] = useState<ThreadSearchResultRecord[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const trimmedQuery = query.trim();

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    if (!trimmedQuery) {
      setResults([]);
      setIsSearching(false);
      setError(null);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setIsSearching(true);
      setError(null);
      searchThreads(trimmedQuery, { signal: controller.signal })
        .then((nextResults) => setResults(nextResults))
        .catch((caught) => {
          if (!controller.signal.aborted) {
            setError(caught instanceof Error ? caught.message : "search failed");
            setResults([]);
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) {
            setIsSearching(false);
          }
        });
    }, 160);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [isOpen, trimmedQuery]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setIsOpen(false);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen]);

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        title="搜索聊天"
        className={cn(
          "size-8 rounded-lg text-muted-foreground transition-all duration-150 hover:-translate-y-0.5 hover:bg-muted hover:text-foreground data-open:bg-muted data-open:text-foreground",
          triggerClassName,
        )}
        onClick={() => setIsOpen(true)}
      >
        <Search className="size-4" />
        <span className="sr-only">搜索聊天</span>
      </Button>
      {isOpen ? (
        <div className="fixed inset-0 z-50 bg-black/35 backdrop-blur-[2px]">
          <button
            type="button"
            aria-label="关闭搜索"
            className="absolute inset-0 cursor-default"
            onClick={() => setIsOpen(false)}
          />
          <div className="relative mx-auto mt-[14vh] flex max-h-[72vh] w-[min(42rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl bg-background shadow-2xl ring-1 ring-border/70">
            <div className="flex h-16 shrink-0 items-center gap-3 border-b px-4">
              <Search className="size-5 shrink-0 text-muted-foreground" />
              <Input
                autoFocus
                value={query}
                onChange={(event) => onQueryChange(event.target.value)}
                placeholder="搜索对话内容..."
                className="h-12 flex-1 border-0 bg-transparent px-0 text-lg shadow-none focus-visible:ring-0"
              />
              <div className="h-7 w-px bg-border" />
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                title="关闭搜索"
                className="size-9 rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
                onClick={() => setIsOpen(false)}
              >
                <X className="size-4" />
                <span className="sr-only">关闭搜索</span>
              </Button>
            </div>
            <SearchResults
              error={error}
              isSearching={isSearching}
              query={trimmedQuery}
              results={results}
              onSelect={(thread) => {
                setIsOpen(false);
                onSelectThread(thread);
              }}
            />
          </div>
        </div>
      ) : null}
    </>
  );
}

function SearchResults({
  error,
  isSearching,
  query,
  results,
  onSelect,
}: {
  error: string | null;
  isSearching: boolean;
  query: string;
  results: ThreadSearchResultRecord[];
  onSelect: (thread: ThreadRecord) => void;
}) {
  if (!query) {
    return (
      <div className="grid min-h-48 place-items-center px-6 py-10 text-sm text-muted-foreground">
        输入关键词搜索所有聊天消息
      </div>
    );
  }

  if (error) {
    return (
      <div className="px-5 py-4 text-sm text-destructive">
        {error}
      </div>
    );
  }

  if (isSearching && results.length === 0) {
    return (
      <div className="grid min-h-48 place-items-center px-6 py-10 text-sm text-muted-foreground">
        正在搜索
      </div>
    );
  }

  if (results.length === 0) {
    return (
      <div className="grid min-h-48 place-items-center px-6 py-10 text-sm text-muted-foreground">
        没有找到相关聊天
      </div>
    );
  }

  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="space-y-1 p-2">
        {results.map((result) => (
          <button
            key={`${result.thread.id}:${result.message?.id ?? "title"}`}
            type="button"
            className="grid w-full grid-cols-[2.25rem_minmax(0,1fr)_auto] items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors hover:bg-muted"
            onClick={() => onSelect(result.thread)}
          >
            <span className="grid size-9 place-items-center rounded-full border text-muted-foreground">
              <MessageSquarePlus className="size-4" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-sm font-semibold text-foreground">
                {result.thread.title}
              </span>
              <span className="block truncate text-sm text-muted-foreground">
                {result.match_type === "message" ? roleLabel(result.message?.role) : "标题"} · {result.snippet}
              </span>
            </span>
            <span className="text-sm text-muted-foreground">
              {formatSearchDate(result.message?.created_at ?? result.thread.updated_at)}
            </span>
          </button>
        ))}
      </div>
    </ScrollArea>
  );
}

function roleLabel(role: string | undefined) {
  if (role === "assistant") {
    return "助手";
  }
  if (role === "user") {
    return "用户";
  }
  return "消息";
}

function formatSearchDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleDateString("zh-CN", {
    month: "numeric",
    day: "numeric",
  });
}
