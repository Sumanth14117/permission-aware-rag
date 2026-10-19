import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import { login, logout } from "./authApi.js";
import { askQuestion, deleteDocument, listDocuments, uploadDocument } from "./workspaceApi.js";

vi.mock("./authApi.js", () => ({
  login: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("./workspaceApi.js", () => ({
  askQuestion: vi.fn(),
  deleteDocument: vi.fn(),
  listDocuments: vi.fn(),
  uploadDocument: vi.fn(),
}));

function fillLoginForm() {
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "member@example.test" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "correct-horse" },
  });
}

function submitLogin() {
  fireEvent.submit(screen.getByLabelText("Email").closest("form"));
}

describe("authentication interface", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.clearAllMocks();
    listDocuments.mockResolvedValue([]);
    uploadDocument.mockResolvedValue({ id: "doc" });
  });

  it("renders labeled required fields and validates empty submissions", async () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.getByLabelText("Email").required).toBe(true);
    expect(screen.getByLabelText("Password").required).toBe(true);
    submitLogin();

    expect((await screen.findByRole("alert")).textContent)
      .toBe("Enter your email and password to continue.");
    expect(login).not.toHaveBeenCalled();
  });

  it("shows the workspace after successful login", async () => {
    login.mockResolvedValueOnce(undefined);
    render(<App />);
    fillLoginForm();
    submitLogin();

    expect(login).toHaveBeenCalledWith({
      email: "member@example.test",
      password: "correct-horse",
    });
    expect(await screen.findByRole("heading", { name: "You're signed in" })).toBeTruthy();
    expect(screen.getByText(/HTTP-only cookie/i)).toBeTruthy();
    expect(await screen.findByText("No accessible documents yet.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
  });

  it("shows a generic invalid-credentials message", async () => {
    login.mockRejectedValueOnce({ code: "invalid_credentials", message: "internal detail" });
    render(<App />);
    fillLoginForm();
    submitLogin();

    expect((await screen.findByRole("alert")).textContent).toBe("Invalid email or password.");
    expect(screen.queryByText("internal detail")).toBeNull();
  });

  it("shows a safe CSRF error and does not replay login", async () => {
    login.mockRejectedValueOnce({ code: "csrf_rejected", message: "parser details" });
    render(<App />);
    fillLoginForm();
    submitLogin();

    expect((await screen.findByRole("alert")).textContent)
      .toBe("The security check failed. Please submit the form again.");
    expect(login).toHaveBeenCalledTimes(1);
  });

  it("shows a generic service-unavailable message", async () => {
    login.mockRejectedValueOnce({ code: "unavailable", message: "database details" });
    render(<App />);
    fillLoginForm();
    submitLogin();

    expect((await screen.findByRole("alert")).textContent)
      .toBe("Sign in is temporarily unavailable. Please try again.");
    expect(screen.queryByText("database details")).toBeNull();
  });

  it("returns to the login form after successful logout", async () => {
    login.mockResolvedValueOnce(undefined);
    logout.mockResolvedValueOnce(undefined);
    render(<App />);
    fillLoginForm();
    submitLogin();
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.getByLabelText("Password").value).toBe("");
  });

  it("returns to login after logout 503 without claiming revocation", async () => {
    login.mockResolvedValueOnce(undefined);
    logout.mockRejectedValueOnce({ code: "unavailable", message: "raw storage detail" });
    render(<App />);
    fillLoginForm();
    submitLogin();
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    expect((await screen.findByRole("alert")).textContent)
      .toBe("Sign out could not be confirmed. Please sign in again.");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.getByLabelText("Password").value).toBe("");
    expect(screen.queryByText("raw storage detail")).toBeNull();
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it("returns to login after logout 403 without retrying", async () => {
    login.mockResolvedValueOnce(undefined);
    logout.mockRejectedValueOnce({ code: "csrf_rejected", message: "parser detail" });
    render(<App />);
    fillLoginForm();
    submitLogin();
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    expect((await screen.findByRole("alert")).textContent)
      .toBe("The security check failed. Please sign in again.");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.getByLabelText("Password").value).toBe("");
    expect(screen.queryByText("parser detail")).toBeNull();
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it("shows a loading state and prevents duplicate login submissions", async () => {
    let resolveLogin;
    login.mockReturnValueOnce(new Promise((resolve) => { resolveLogin = resolve; }));
    render(<App />);
    fillLoginForm();
    submitLogin();
    submitLogin();

    expect(login).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Signing in..." }).disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toBe("Signing in");

    resolveLogin();
    expect(await screen.findByRole("heading", { name: "You're signed in" })).toBeTruthy();
  });

  it("submits a question and renders answer source citations", async () => {
    login.mockResolvedValueOnce(undefined);
    askQuestion.mockResolvedValueOnce({
      answer: "A grounded answer.",
      citations: [{ document_id: "doc-id", chunk_index: 3 }],
    });
    render(<App />);
    fillLoginForm();
    submitLogin();
    await screen.findByRole("heading", { name: "Ask your documents" });
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "What is known?" } });
    fireEvent.submit(screen.getByLabelText("Question").closest("form"));

    expect(askQuestion).toHaveBeenCalledWith("What is known?");
    expect(await screen.findByText("A grounded answer.")).toBeTruthy();
    expect(screen.getByText(/chunk 3/).textContent).toContain("doc-id");
  });

  it("shows a safe question error without backend details", async () => {
    login.mockResolvedValueOnce(undefined);
    askQuestion.mockRejectedValueOnce({ code: "unavailable", message: "database password" });
    render(<App />);
    fillLoginForm();
    submitLogin();
    await screen.findByRole("heading", { name: "Ask your documents" });
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "Question" } });
    fireEvent.submit(screen.getByLabelText("Question").closest("form"));

    expect((await screen.findByRole("alert")).textContent)
      .toBe("The workspace service is temporarily unavailable. Try again.");
    expect(screen.queryByText("database password")).toBeNull();
  });

  it("uploads a selected file through the document API", async () => {
    login.mockResolvedValueOnce(undefined);
    render(<App />);
    fillLoginForm();
    submitLogin();
    await screen.findByRole("heading", { name: "Documents" });
    const file = new File(["some content"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText(/Upload a text or PDF/), { target: { files: [file] } });
    fireEvent.submit(screen.getByLabelText(/Upload a text or PDF/).closest("form"));

    await waitFor(() => expect(uploadDocument).toHaveBeenCalledWith(file, "org"));
  });

  it("deletes a document and refreshes the list", async () => {
    login.mockResolvedValueOnce(undefined);
    listDocuments
      .mockResolvedValueOnce([{ id: "doc-1", original_filename: "notes.txt", status: "ready" }])
      .mockResolvedValueOnce([]);
    deleteDocument.mockResolvedValueOnce(undefined);
    render(<App />);
    fillLoginForm();
    submitLogin();
    fireEvent.click(await screen.findByRole("button", { name: "Delete notes.txt" }));

    await waitFor(() => expect(deleteDocument).toHaveBeenCalledWith("doc-1"));
    expect(await screen.findByText("No accessible documents yet.")).toBeTruthy();
  });

  it("shows a safe message when a document is already gone", async () => {
    login.mockResolvedValueOnce(undefined);
    listDocuments.mockResolvedValueOnce([
      { id: "doc-1", original_filename: "notes.txt", status: "ready" },
    ]);
    deleteDocument.mockRejectedValueOnce({ code: "not_found", message: "Document not found" });
    render(<App />);
    fillLoginForm();
    submitLogin();
    fireEvent.click(await screen.findByRole("button", { name: "Delete notes.txt" }));

    expect((await screen.findByRole("alert")).textContent)
      .toBe("That document is no longer available.");
    expect(screen.queryByText("Document not found")).toBeNull();
  });
});




