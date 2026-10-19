import { beforeEach, describe, expect, it, vi } from "vitest";

const futureExpiry = Math.floor(Date.now() / 1000) + 600;
const csrfToken = `v1.${futureExpiry}.${"n".repeat(43)}.${"s".repeat(43)}`;
const anotherCsrfToken = `v1.${futureExpiry + 60}.${"m".repeat(43)}.${"t".repeat(43)}`;

function jsonResponse(status, payload) {
  return { status, json: vi.fn().mockResolvedValue(payload) };
}

function emptyResponse(status) {
  return { status };
}

async function loadClient() {
  vi.resetModules();
  return import("./authApi.js");
}

describe("authentication API client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    localStorage.clear();
    sessionStorage.clear();
    vi.spyOn(Storage.prototype, "setItem");
  });

  it("bootstraps once, keeps a usable token in memory, and sends it on login/logout", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: csrfToken }))
      .mockResolvedValueOnce(emptyResponse(204))
      .mockResolvedValueOnce(emptyResponse(204));
    const { login, logout } = await loadClient();

    await login({ email: "member@example.test", password: "private-password" });
    await logout();

    expect(fetch).toHaveBeenCalledTimes(3);
    expect(fetch.mock.calls[0][0]).toBe("/auth/csrf");
    expect(fetch.mock.calls[0][1]).toMatchObject({
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
    });
    expect(fetch.mock.calls[1][0]).toBe("/auth/login");
    expect(fetch.mock.calls[1][1]).toMatchObject({
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        email: "member@example.test",
        password: "private-password",
      }),
    });
    expect(fetch.mock.calls[2][0]).toBe("/auth/logout");
    expect(fetch.mock.calls[2][1]).toMatchObject({
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": csrfToken },
    });
    expect(localStorage.setItem).not.toHaveBeenCalled();
    expect(sessionStorage.setItem).not.toHaveBeenCalled();
  });

  it("allows the caller to refresh the in-memory token without returning it", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: csrfToken }))
      .mockResolvedValueOnce(emptyResponse(204))
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: anotherCsrfToken }))
      .mockResolvedValueOnce(emptyResponse(204));
    const { login, refreshCsrfToken } = await loadClient();

    await login({ email: "member@example.test", password: "secret" });
    await expect(refreshCsrfToken()).resolves.toBeUndefined();
    await login({ email: "member@example.test", password: "secret" });
    expect(fetch.mock.calls[2][0]).toBe("/auth/csrf");
    expect(fetch.mock.calls[3][1].headers["X-CSRF-Token"]).toBe(anotherCsrfToken);
  });

  it.each([
    [401, "invalid_credentials", "Invalid email or password."],
    [403, "csrf_rejected", "The security check failed. Refresh the page and try again."],
    [503, "unavailable", "Authentication is temporarily unavailable."],
  ])("maps HTTP %s to a safe error", async (status, code, message) => {
    fetch
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: csrfToken }))
      .mockResolvedValueOnce({
        status,
        json: vi.fn().mockResolvedValue({ detail: "sensitive internal detail" }),
      });
    const { AuthApiError, login } = await loadClient();

    await expect(login({ email: "member@example.test", password: "secret" }))
      .rejects.toMatchObject({ name: AuthApiError.name, code, message });
    expect(fetch.mock.calls[1][1].body).not.toContain("sensitive internal detail");
  });

  it("invalidates a token after 403 without replaying the request", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: csrfToken }))
      .mockResolvedValueOnce(emptyResponse(403));
    const { AuthApiError, login } = await loadClient();

    await expect(login({ email: "member@example.test", password: "secret" }))
      .rejects.toMatchObject({ code: "csrf_rejected" });
    expect(fetch).toHaveBeenCalledTimes(2);

    fetch
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: anotherCsrfToken }))
      .mockResolvedValueOnce(emptyResponse(204));
    const { logout } = await import("./authApi.js");
    await logout();

    expect(fetch).toHaveBeenCalledTimes(4);
    expect(fetch.mock.calls[2][0]).toBe("/auth/csrf");
    expect(fetch.mock.calls[3][0]).toBe("/auth/logout");
    expect(fetch.mock.calls[3][1].headers["X-CSRF-Token"]).toBe(anotherCsrfToken);
    expect(AuthApiError).toBeDefined();
  });

  it("rejects unexpected success statuses without exposing response bodies", async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: csrfToken }))
      .mockResolvedValueOnce({
        status: 200,
        json: vi.fn().mockResolvedValue({ detail: "internal stack trace" }),
      });
    const { login } = await loadClient();

    await expect(login({ email: "member@example.test", password: "secret" }))
      .rejects.toMatchObject({ code: "unexpected" });
  });

  it("rejects malformed bootstrap tokens and does not send an unsafe request", async () => {
    fetch.mockResolvedValueOnce(jsonResponse(200, { csrf_token: "not-a-token" }));
    const { login } = await loadClient();

    await expect(login({ email: "member@example.test", password: "secret" }))
      .rejects.toMatchObject({ code: "unexpected" });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});


