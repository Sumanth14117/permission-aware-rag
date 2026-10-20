import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import { login, logout, register } from "./authApi.js";
import { askQuestion, deleteDocument, listDocuments, uploadDocument } from "./workspaceApi.js";

vi.mock("./authApi.js", () => ({
  login: vi.fn(),
  logout: vi.fn(),
  register: vi.fn(),
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

function openSignup() {
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
}

function fillSignupForm({
  email = "owner@example.test",
  organization = "Example Org",
  password = "correct-horse-battery",
  confirmPassword = password,
} = {}) {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Organization name"), {
    target: { value: organization },
  });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("Confirm password"), {
    target: { value: confirmPassword },
  });
}

function submitSignup() {
  fireEvent.submit(screen.getByLabelText("Organization name").closest("form"));
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

  it("clears the first answer before showing the second question's answer", async () => {
    login.mockResolvedValueOnce(undefined);
    askQuestion.mockResolvedValueOnce({
      answer: "The irrigation schedule answer.",
      citations: [{ document_id: "doc-1", chunk_index: 0 }],
    });
    let resolveSecond;
    askQuestion.mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve; }));
    render(<App />);
    fillLoginForm();
    submitLogin();
    await screen.findByRole("heading", { name: "Ask your documents" });

    fireEvent.change(screen.getByLabelText("Question"), {
      target: { value: "When should irrigation activate?" },
    });
    fireEvent.submit(screen.getByLabelText("Question").closest("form"));
    expect(await screen.findByText("The irrigation schedule answer.")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Question"), {
      target: { value: "What is the exact price in rupees?" },
    });
    fireEvent.submit(screen.getByLabelText("Question").closest("form"));

    await waitFor(() => expect(askQuestion).toHaveBeenLastCalledWith(
      "What is the exact price in rupees?",
    ));
    await waitFor(() => expect(screen.queryByText("The irrigation schedule answer.")).toBeNull());

    resolveSecond({
      answer: "I could not find an answer in the documents you can access.",
      citations: [],
    });
    expect(await screen.findByText(
      "I could not find an answer in the documents you can access.",
    )).toBeTruthy();
    expect(screen.queryByText("The irrigation schedule answer.")).toBeNull();
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

  it("navigates between sign in and account creation", () => {
    render(<App />);

    openSignup();

    expect(screen.getByRole("heading", { name: "Create account" })).toBeTruthy();
    expect(screen.getByLabelText("Email").required).toBe(true);
    expect(screen.getByLabelText("Organization name").required).toBe(true);
    expect(screen.getByLabelText("Password").required).toBe(true);
    expect(screen.getByLabelText("Confirm password").required).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(screen.getByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.queryByLabelText("Organization name")).toBeNull();
  });

  it("clears password fields when switching between sign in and account creation", () => {
    render(<App />);
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner@example.test" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "login-secret" },
    });

    openSignup();

    expect(screen.getByLabelText("Password").value).toBe("");
    expect(screen.getByLabelText("Confirm password").value).toBe("");

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "new-owner@example.test" },
    });
    fireEvent.change(screen.getByLabelText("Organization name"), {
      target: { value: "Example Org" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "signup-secret" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "signup-secret" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(screen.getByLabelText("Email").value).toBe("owner@example.test");
    expect(screen.getByLabelText("Password").value).toBe("");

    openSignup();

    expect(screen.getByLabelText("Password").value).toBe("");
    expect(screen.getByLabelText("Confirm password").value).toBe("");
  });

  it("validates signup fields before calling the API", async () => {
    render(<App />);
    openSignup();
    submitSignup();

    expect((await screen.findByRole("alert")).textContent)
      .toBe("Complete every field to create your account.");
    expect(register).not.toHaveBeenCalled();
  });

  it("rejects short and mismatched passwords before calling the API", async () => {
    render(<App />);
    openSignup();
    fillSignupForm({ password: "short", confirmPassword: "short" });
    submitSignup();

    expect((await screen.findByRole("alert")).textContent)
      .toBe("Use at least 8 characters for your password.");
    expect(register).not.toHaveBeenCalled();

    fillSignupForm({ confirmPassword: "different-password" });
    submitSignup();

    expect((await screen.findByRole("alert")).textContent).toBe("Passwords do not match.");
    expect(register).not.toHaveBeenCalled();
  });

  it("creates an account and enters the workspace when signed in", async () => {
    register.mockResolvedValueOnce({ signedIn: true });
    render(<App />);
    openSignup();
    fillSignupForm({ email: " owner@example.test ", organization: " Example Org " });
    submitSignup();

    await waitFor(() => expect(register).toHaveBeenCalledWith({
      email: "owner@example.test",
      organizationName: "Example Org",
      password: "correct-horse-battery",
    }));
    expect(await screen.findByRole("heading", { name: "You're signed in" })).toBeTruthy();
    expect(await screen.findByText("No accessible documents yet.")).toBeTruthy();
  });

  it("returns to sign in with a notice when automatic sign-in is unavailable", async () => {
    register.mockResolvedValueOnce({ signedIn: false });
    render(<App />);
    openSignup();
    fillSignupForm({ email: "owner@example.test" });
    submitSignup();

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(await screen.findByText("Account created. Sign in with your new credentials."))
      .toBeTruthy();
    expect(screen.getByLabelText("Email").value).toBe("owner@example.test");
    expect(screen.queryByRole("heading", { name: "You're signed in" })).toBeNull();
  });

  it("shows a safe duplicate-email message without backend details", async () => {
    register.mockRejectedValueOnce({ code: "duplicate_email", message: "internal database detail" });
    render(<App />);
    openSignup();
    fillSignupForm();
    submitSignup();

    expect((await screen.findByRole("alert")).textContent)
      .toBe("An account with this email already exists.");
    expect(screen.queryByText("internal database detail")).toBeNull();
    expect(register).toHaveBeenCalledTimes(1);
  });

  it("shows a safe CSRF error on signup and does not retry", async () => {
    register.mockRejectedValueOnce({ code: "csrf_rejected", message: "parser detail" });
    render(<App />);
    openSignup();
    fillSignupForm();
    submitSignup();

    expect((await screen.findByRole("alert")).textContent)
      .toBe("The security check failed. Please submit the form again.");
    expect(screen.queryByText("parser detail")).toBeNull();
    expect(register).toHaveBeenCalledTimes(1);
  });

  it("shows a safe signup-unavailable message", async () => {
    register.mockRejectedValueOnce({ code: "unavailable", message: "database password" });
    render(<App />);
    openSignup();
    fillSignupForm();
    submitSignup();

    expect((await screen.findByRole("alert")).textContent)
      .toBe("Sign up is temporarily unavailable. Please try again.");
    expect(screen.queryByText("database password")).toBeNull();
  });

  it("shows a signup loading state and prevents duplicate submissions", async () => {
    let resolveRegister;
    register.mockReturnValueOnce(new Promise((resolve) => { resolveRegister = resolve; }));
    render(<App />);
    openSignup();
    fillSignupForm();
    submitSignup();
    submitSignup();

    expect(register).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Creating account..." }).disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toBe("Creating account");

    resolveRegister({ signedIn: true });
    expect(await screen.findByRole("heading", { name: "You're signed in" })).toBeTruthy();
  });
});




