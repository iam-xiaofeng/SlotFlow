"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { LoaderCircle, RotateCcw, Send, Signal, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useChatStream } from "@/hooks/use-chat-stream";

const starterPrompt = "用三句话解释 SlotFlow 当前后端链路。";
const defaultModelName = "deepseek-v4-flash";

export default function Home() {
  const [input, setInput] = useState(starterPrompt);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const {
    thread,
    messages,
    events,
    isStreaming,
    error,
    sendMessage,
    startNewThread,
    cancelStream,
  } = useChatStream({
    defaultThreadTitle: "SlotFlow smoke test",
    defaultModelName,
    defaultMode: "pro",
    defaultAgentName: "default",
    defaultMetadata: {
      source: "frontend-smoke",
    },
    maxEventLogItems: 12,
  });

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  async function handleNewChat() {
    if (isStreaming) {
      return;
    }

    setInput(starterPrompt);
    await startNewThread("SlotFlow smoke test");
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const text = input.trim();
    if (!text || isStreaming) {
      return;
    }

    setInput("");
    const result = await sendMessage(text);
    if (!result.accepted) {
      setInput(text);
    }
  }

  return (
    <main className="min-h-screen overflow-hidden bg-[var(--background)] text-[var(--foreground)]">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_20%_10%,rgba(37,99,235,0.14),transparent_28%),radial-gradient(circle_at_85%_18%,rgba(245,158,11,0.12),transparent_24%),linear-gradient(135deg,rgba(255,255,255,0.7),rgba(231,229,221,0.65))]" />

      <div className="relative mx-auto flex min-h-screen w-full max-w-6xl flex-col px-5 py-5 sm:px-8 sm:py-7">
        <header className="flex flex-col gap-4 border-b border-[var(--border)] pb-5 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 border border-[var(--border)] bg-white/70 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-[var(--muted-foreground)]">
              <Signal className="size-3.5 text-[var(--primary)]" />
              SSE Smoke Test
            </div>
            <h1 className="text-3xl font-semibold tracking-tight sm:text-5xl">
              SlotFlow Chat Stream
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--muted-foreground)]">
              最小前端验证页：创建 thread，调用后端 runs/stream，解析 SSE，并把
              assistant 的文本增量显示出来。
            </p>
          </div>

          <Button
            type="button"
            variant="outline"
            onClick={handleNewChat}
            disabled={isStreaming}
            className="w-fit bg-white/70"
          >
            <RotateCcw className="size-4" />
            New Chat
          </Button>
        </header>

        <section className="grid flex-1 gap-5 py-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="flex min-h-[620px] flex-col border border-[var(--border)] bg-white/80 shadow-[0_24px_80px_rgba(24,24,27,0.08)] backdrop-blur">
            <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
              <div>
                <div className="text-sm font-semibold">Chat</div>
                <div className="text-xs text-[var(--muted-foreground)]">
                  {thread ? thread.id : "thread will be created on first send"}
                </div>
              </div>
              <div className="text-xs font-medium text-[var(--muted-foreground)]">
                {isStreaming ? "streaming" : "idle"}
              </div>
            </div>

            <div className="flex-1 space-y-4 overflow-y-auto px-4 py-5">
              {messages.length === 0 ? (
                <div className="grid h-full place-items-center text-center">
                  <div className="max-w-md">
                    <div className="text-lg font-semibold">发送一条消息验证链路</div>
                    <p className="mt-2 text-sm leading-6 text-[var(--muted-foreground)]">
                      后端默认 static runtime，不需要 API key。启动 FastAPI 后，点击
                      Send 应该能看到流式文本和事件日志。
                    </p>
                  </div>
                </div>
              ) : (
                messages.map((message) => (
                  <article
                    key={message.id}
                    className={
                      message.role === "user"
                        ? "ml-auto max-w-[82%] bg-[var(--primary)] px-4 py-3 text-[var(--primary-foreground)]"
                        : "mr-auto max-w-[86%] border border-[var(--border)] bg-[var(--surface)] px-4 py-3"
                    }
                  >
                    <div className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] opacity-70">
                      {message.role}
                      {message.status === "streaming" ? (
                        <LoaderCircle className="size-3 animate-spin" />
                      ) : null}
                    </div>
                    <p className="whitespace-pre-wrap text-sm leading-6">
                      {message.content || "等待后端流式返回..."}
                    </p>
                  </article>
                ))
              )}
              <div ref={messagesEndRef} />
            </div>

            <form
              onSubmit={handleSubmit}
              className="border-t border-[var(--border)] bg-[var(--surface)] p-4"
            >
              {error ? (
                <div className="mb-3 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {error}
                </div>
              ) : null}

              <div className="flex flex-col gap-3 sm:flex-row">
                <textarea
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  rows={3}
                  disabled={isStreaming}
                  className="min-h-24 flex-1 resize-none border border-[var(--border)] bg-white px-3 py-2 text-sm leading-6 outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-blue-100 disabled:opacity-60"
                  placeholder="输入一条消息，验证后端 SSE 流..."
                />
                {isStreaming ? (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={cancelStream}
                    className="h-auto min-h-11 bg-white sm:w-32"
                  >
                    <Square className="size-4" />
                    Cancel
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    disabled={!input.trim()}
                    className="h-auto min-h-11 sm:w-32"
                  >
                    <Send className="size-4" />
                    Send
                  </Button>
                )}
              </div>
            </form>
          </div>

          <aside className="flex max-h-[420px] min-h-0 flex-col border border-[var(--border)] bg-[#151713] p-4 text-[#f1f0e8] shadow-[0_24px_80px_rgba(24,24,27,0.10)] lg:h-[620px] lg:max-h-[620px]">
            <div className="mb-4">
              <div className="text-sm font-semibold">Event Log</div>
              <p className="mt-1 text-xs leading-5 text-[#aaa99f]">
                最近 12 条 SlotFlow 业务 SSE 事件。
              </p>
            </div>

            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
              {events.length === 0 ? (
                <div className="border border-[#303229] bg-[#1d1f19] p-3 text-xs text-[#aaa99f]">
                  发送消息后这里会显示 run.prepared、message.delta、state.snapshot
                  和 run.finished。
                </div>
              ) : (
                events.map((event, index) => (
                  <div
                    key={`${event.event}-${index}`}
                    className="border border-[#303229] bg-[#1d1f19] p-3"
                  >
                    <div className="text-xs font-semibold text-[#f7c948]">
                      {event.event}
                    </div>
                    <pre className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-[#d8d6ca]">
                      {JSON.stringify(event.data, null, 2)}
                    </pre>
                  </div>
                ))
              )}
            </div>
          </aside>
        </section>
      </div>
    </main>
  );
}
