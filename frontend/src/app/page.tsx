import { Send } from "lucide-react";

import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="min-h-screen bg-[var(--background)] text-[var(--foreground)]">
      <div className="mx-auto flex min-h-screen w-full max-w-5xl flex-col px-6 py-6">
        <header className="flex items-center justify-between border-b border-[var(--border)] pb-4">
          <div>
            <h1 className="text-2xl font-semibold">SlotFlow</h1>
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">
              A smaller agent interface for learning the full request stream.
            </p>
          </div>
          <Button type="button">
            <Send className="size-4" />
            New Chat
          </Button>
        </header>

        <section className="grid flex-1 place-items-center">
          <div className="w-full max-w-2xl border border-[var(--border)] bg-white p-5">
            <div className="text-sm font-medium text-[var(--muted-foreground)]">
              Backend
            </div>
            <div className="mt-2 text-lg font-semibold">/health is ready</div>
            <p className="mt-2 text-sm leading-6 text-[var(--muted-foreground)]">
              The chat stream will be added after the backend agent boundary is
              verified.
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}
