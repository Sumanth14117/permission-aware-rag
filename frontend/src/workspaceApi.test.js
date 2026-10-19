import { beforeEach, describe, expect, it, vi } from "vitest";
import { csrfFetch } from "./authApi.js";
import { askQuestion, deleteDocument, listDocuments, uploadDocument } from "./workspaceApi.js";

vi.mock("./authApi.js", () => ({ csrfFetch: vi.fn() }));

function response(status, payload) {
  return { ok: status >= 200 && status < 300, status, json: vi.fn().mockResolvedValue(payload) };
}

describe("workspace API client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("fetch", vi.fn());
    vi.spyOn(Storage.prototype, "setItem");
  });

  it("sends QA requests through the CSRF protected same-origin request helper", async () => {
    csrfFetch.mockResolvedValueOnce(response(200, { answer: "Grounded", citations: [] }));
    await expect(askQuestion("What is known?")).resolves.toMatchObject({ answer: "Grounded" });
    expect(csrfFetch).toHaveBeenCalledWith("/qa", expect.objectContaining({
      method: "POST",
      credentials: "same-origin",
      headers: expect.objectContaining({ "Content-Type": "application/json" }),
      body: JSON.stringify({ question: "What is known?" }),
    }));
    expect(localStorage.setItem).not.toHaveBeenCalled();
    expect(sessionStorage.setItem).not.toHaveBeenCalled();
  });

  it("lists documents with same-origin cookie credentials", async () => {
    fetch.mockResolvedValueOnce(response(200, [{ id: "doc" }]));
    await expect(listDocuments()).resolves.toEqual([{ id: "doc" }]);
    expect(fetch).toHaveBeenCalledWith("/documents", expect.objectContaining({
      method: "GET", credentials: "same-origin",
    }));
  });

  it("uploads the raw file body with filename and visibility query parameters through CSRF", async () => {
    const file = new File(["document text"], "notes.txt", { type: "text/plain" });
    csrfFetch.mockResolvedValueOnce(response(201, { id: "doc" }));
    await uploadDocument(file, "restricted");
    expect(csrfFetch).toHaveBeenCalledWith(
      "/documents?filename=notes.txt&visibility=restricted",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        headers: expect.objectContaining({ "Content-Type": "text/plain" }),
        body: file,
      }),
    );
  });

  it("deletes a document through the CSRF protected same-origin request helper", async () => {
    csrfFetch.mockResolvedValueOnce({ ok: true, status: 204, json: vi.fn() });
    await expect(deleteDocument("doc/1")).resolves.toBeUndefined();
    expect(csrfFetch).toHaveBeenCalledWith("/documents/doc%2F1", expect.objectContaining({
      method: "DELETE",
      credentials: "same-origin",
    }));
  });

  it("maps a missing document to a safe not-found error", async () => {
    csrfFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: vi.fn().mockResolvedValue({ detail: "Document not found" }),
    });
    await expect(deleteDocument("gone")).rejects.toMatchObject({
      code: "not_found",
      message: "That document is no longer available.",
    });
  });

  it("maps backend errors without exposing response bodies", async () => {
    csrfFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: vi.fn().mockResolvedValue({ detail: "database credentials" }),
    });
    await expect(askQuestion("question")).rejects.toMatchObject({
      code: "unavailable",
      message: "The workspace service is temporarily unavailable. Try again.",
    });
  });
});
