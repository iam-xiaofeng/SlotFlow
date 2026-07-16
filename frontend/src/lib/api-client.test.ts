import { afterEach, describe, expect, it, vi } from "vitest";

import { requestJson, requestOk } from "./api-client";

afterEach(() => vi.unstubAllGlobals());

describe("API request contract", () => {
  it("forwards the request and decodes JSON", async () => {
    const response = new Response(JSON.stringify({ value: 7 }), {
      headers: { "Content-Type": "application/json" },
    });
    const fetchMock = vi.fn().mockResolvedValue(response);
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      requestJson<{ value: number }>("load failed", "/resource", {
        method: "POST",
        body: "payload",
      }),
    ).resolves.toEqual({ value: 7 });
    expect(fetchMock).toHaveBeenCalledWith("/resource", {
      method: "POST",
      body: "payload",
    });
  });

  it("preserves the endpoint-specific status error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 418 })));

    await expect(requestOk("update failed", "/resource")).rejects.toThrow(
      "update failed: 418",
    );
  });
});
