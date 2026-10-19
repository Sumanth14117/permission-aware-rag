import { csrfFetch } from "./authApi.js";

const SAFE_ERRORS = {
  network: "Could not reach the workspace service. Try again.",
  unauthorized: "Your sign-in has expired. Please sign in again.",
  forbidden: "You do not have access to that request.",
  not_found: "That document is no longer available.",
  unavailable: "The workspace service is temporarily unavailable. Try again.",
  invalid: "The request could not be accepted. Check the input and try again.",
  unexpected: "The request could not be completed. Try again.",
};

export class WorkspaceApiError extends Error {
  constructor(code) {
    super(SAFE_ERRORS[code] ?? SAFE_ERRORS.unexpected);
    this.name = "WorkspaceApiError";
    this.code = SAFE_ERRORS[code] ? code : "unexpected";
  }
}

function errorForStatus(status) {
  if (status === 401) return new WorkspaceApiError("unauthorized");
  if (status === 403) return new WorkspaceApiError("forbidden");
  if (status === 404) return new WorkspaceApiError("not_found");
  if (status === 413 || status === 415 || status === 422) return new WorkspaceApiError("invalid");
  if (status === 503) return new WorkspaceApiError("unavailable");
  return new WorkspaceApiError("unexpected");
}

async function fetchResponse(path, options) {
  try {
    const response = options.method && options.method !== "GET"
      ? await csrfFetch(path, options)
      : await fetch(path, { ...options, credentials: "same-origin", headers: { Accept: "application/json", ...options.headers } });
    if (!response.ok) throw errorForStatus(response.status);
    return response;
  } catch (error) {
    if (error instanceof WorkspaceApiError) throw error;
    if (error?.code) throw error;
    throw new WorkspaceApiError("network");
  }
}

async function jsonResponse(path, options) {
  const response = await fetchResponse(path, options);
  try {
    return await response.json();
  } catch {
    throw new WorkspaceApiError("unexpected");
  }
}

export async function askQuestion(question) {
  return jsonResponse("/qa", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
}

export async function listDocuments() {
  return jsonResponse("/documents", { method: "GET" });
}

export async function uploadDocument(file, visibility = "org") {
  const query = new URLSearchParams({ filename: file.name, visibility });
  return jsonResponse(`/documents?${query}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
}

export async function deleteDocument(documentId) {
  // The backend answers 204 with no body; fetchResponse still enforces the
  // same-origin CSRF path and maps failures to safe error codes.
  await fetchResponse(`/documents/${encodeURIComponent(documentId)}`, {
    method: "DELETE",
    credentials: "same-origin",
  });
}
