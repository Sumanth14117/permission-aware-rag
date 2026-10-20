import { useEffect, useRef, useState } from "react";
import { login, logout, register } from "./authApi.js";
import { askQuestion, deleteDocument, listDocuments, uploadDocument } from "./workspaceApi.js";

function safeAuthMessage(error, action) {
  if (action === "logout") {
    if (error?.code === "csrf_rejected") return "The security check failed. Please sign in again.";
    return "Sign out could not be confirmed. Please sign in again.";
  }
  if (error?.code === "invalid_credentials") return "Invalid email or password.";
  if (error?.code === "csrf_rejected") return "The security check failed. Please submit the form again.";
  if (["unavailable", "network"].includes(error?.code)) return "Sign in is temporarily unavailable. Please try again.";
  return "Could not sign in. Please try again.";
}

function safeSignupMessage(error) {
  if (error?.code === "duplicate_email") return "An account with this email already exists.";
  if (error?.code === "invalid_registration") return "Check your details and try again.";
  if (error?.code === "csrf_rejected") return "The security check failed. Please submit the form again.";
  if (["unavailable", "network"].includes(error?.code)) return "Sign up is temporarily unavailable. Please try again.";
  return "Could not create your account. Please try again.";
}

function safeWorkspaceMessage(error) {
  if (error?.code === "unauthorized") return "Your sign-in has expired. Please sign in again.";
  if (error?.code === "forbidden") return "You do not have access to that request.";
  if (error?.code === "not_found") return "That document is no longer available.";
  if (error?.code === "invalid") return "The request could not be accepted. Check the file or question and try again.";
  if (["unavailable", "network"].includes(error?.code)) return "The workspace service is temporarily unavailable. Try again.";
  return "The request could not be completed. Try again.";
}

export default function App() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [authView, setAuthView] = useState("login");
  const [signupEmail, setSignupEmail] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [signupPassword, setSignupPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [signupError, setSignupError] = useState("");
  const [signupNotice, setSignupNotice] = useState("");
  const [documents, setDocuments] = useState([]);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [documentError, setDocumentError] = useState("");
  const [file, setFile] = useState(null);
  const [visibility, setVisibility] = useState("org");
  const [uploading, setUploading] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [deleteError, setDeleteError] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState(null);
  const [asking, setAsking] = useState(false);
  const [qaError, setQaError] = useState("");
  const inFlight = useRef(false);

  async function refreshDocuments() {
    setDocumentsLoading(true);
    setDocumentError("");
    try {
      setDocuments(await listDocuments());
    } catch (error) {
      setDocumentError(safeWorkspaceMessage(error));
    } finally {
      setDocumentsLoading(false);
    }
  }

  useEffect(() => {
    if (isAuthenticated) refreshDocuments();
  }, [isAuthenticated]);

  async function handleLogin(event) {
    event.preventDefault();
    if (inFlight.current) return;
    if (!email.trim() || !password) {
      setErrorMessage("Enter your email and password to continue.");
      return;
    }
    inFlight.current = true;
    setIsLoading(true);
    setErrorMessage("");
    try {
      await login({ email: email.trim(), password });
      setPassword("");
      setSignupNotice("");
      setIsAuthenticated(true);
    } catch (error) {
      setErrorMessage(safeAuthMessage(error, "login"));
    } finally {
      inFlight.current = false;
      setIsLoading(false);
    }
  }

  function showSignup() {
    setAuthView("signup");
    setPassword("");
    setErrorMessage("");
    setSignupNotice("");
    setSignupError("");
  }

  function showLogin() {
    setAuthView("login");
    setSignupPassword("");
    setConfirmPassword("");
    setSignupError("");
    setSignupNotice("");
  }

  async function handleSignup(event) {
    event.preventDefault();
    if (inFlight.current) return;
    if (!signupEmail.trim() || !organizationName.trim() || !signupPassword || !confirmPassword) {
      setSignupError("Complete every field to create your account.");
      return;
    }
    if (signupPassword.length < 8) {
      setSignupError("Use at least 8 characters for your password.");
      return;
    }
    if (signupPassword !== confirmPassword) {
      setSignupError("Passwords do not match.");
      return;
    }
    inFlight.current = true;
    setIsLoading(true);
    setSignupError("");
    try {
      const result = await register({
        email: signupEmail.trim(),
        organizationName: organizationName.trim(),
        password: signupPassword,
      });
      setSignupPassword("");
      setConfirmPassword("");
      setEmail(signupEmail.trim());
      setErrorMessage("");
      if (result.signedIn) {
        setSignupNotice("");
        setIsAuthenticated(true);
      } else {
        setPassword("");
        setAuthView("login");
        setSignupNotice("Account created. Sign in with your new credentials.");
      }
    } catch (error) {
      setSignupError(safeSignupMessage(error));
    } finally {
      inFlight.current = false;
      setIsLoading(false);
    }
  }

  async function handleLogout() {
    if (inFlight.current) return;
    inFlight.current = true;
    setIsLoading(true);
    setErrorMessage("");
    try {
      await logout();
      setErrorMessage("");
    } catch (error) {
      setErrorMessage(safeAuthMessage(error, "logout"));
    } finally {
      setIsAuthenticated(false);
      setDocuments([]);
      setPassword("");
      inFlight.current = false;
      setIsLoading(false);
    }
  }

  async function handleAsk(event) {
    event.preventDefault();
    if (!question.trim() || asking) return;
    setAsking(true);
    setQaError("");
    setAnswer(null);
    try {
      setAnswer(await askQuestion(question.trim()));
    } catch (error) {
      setQaError(safeWorkspaceMessage(error));
    } finally {
      setAsking(false);
    }
  }

  async function handleUpload(event) {
    event.preventDefault();
    if (!file || uploading) return;
    const form = event.currentTarget;
    setUploading(true);
    setDocumentError("");
    try {
      await uploadDocument(file, visibility);
      setFile(null);
      form.reset();
      await refreshDocuments();
    } catch (error) {
      setDocumentError(safeWorkspaceMessage(error));
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(documentId) {
    if (deletingId) return;
    setDeletingId(documentId);
    setDeleteError("");
    try {
      await deleteDocument(documentId);
      await refreshDocuments();
    } catch (error) {
      setDeleteError(safeWorkspaceMessage(error));
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <main className="page-shell">
      <section className="welcome-card" aria-labelledby="page-title">
        <p className="eyebrow">Permission-aware knowledge workspace</p>
        <h1 id="page-title">Your organization&apos;s knowledge, connected.</h1>
        <p className="intro">
          A secure workspace for asking questions across the documents your
          organization is allowed to access.
        </p>

        {isAuthenticated ? (
          <section className="workspace-panel" aria-labelledby="signed-in-title">
            <div className="workspace-heading">
              <div>
                <h2 id="signed-in-title">You&apos;re signed in</h2>
                <p className="auth-copy">Your session is held in a secure, HTTP-only cookie.</p>
              </div>
              <button className="button secondary" type="button" onClick={handleLogout} disabled={isLoading}>
                {isLoading ? "Signing out..." : "Sign out"}
              </button>
            </div>
            {errorMessage && <p className="message error" role="alert">{errorMessage}</p>}

            <section className="workspace-section" aria-labelledby="ask-title">
              <h2 id="ask-title">Ask your documents</h2>
              <form className="stack" onSubmit={handleAsk}>
                <div className="field-group">
                  <label htmlFor="question">Question</label>
                  <textarea id="question" rows="3" maxLength="10000" value={question}
                    onChange={(event) => setQuestion(event.target.value)} disabled={asking} required />
                </div>
                {qaError && <p className="message error" role="alert">{qaError}</p>}
                <button className="button primary" type="submit" disabled={asking || !question.trim()}>
                  {asking ? "Searching your accessible documents..." : "Ask question"}
                </button>
              </form>
              {asking && <p className="message loading" role="status">Searching your accessible documents...</p>}
              {answer && (
                <div className="answer-panel" aria-live="polite">
                  <h3>Answer</h3>
                  <p className="answer-text">{answer.answer}</p>
                  {answer.citations?.length ? (
                    <div>
                      <h4>Sources</h4>
                      <ul className="citation-list">
                        {answer.citations.map((citation) => (
                          <li key={`${citation.document_id}:${citation.chunk_index}`}>
                            Document <code>{citation.document_id}</code>, chunk {citation.chunk_index}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : <p className="message empty-state">No accessible source chunks matched this question.</p>}
                </div>
              )}
            </section>

            <section className="workspace-section" aria-labelledby="documents-title">
              <h2 id="documents-title">Documents</h2>
              <form className="stack" onSubmit={handleUpload}>
                <div className="field-group">
                  <label htmlFor="document-file">Upload a text or PDF document</label>
                  <input id="document-file" type="file" accept=".txt,.pdf,text/plain,application/pdf"
                    onChange={(event) => setFile(event.target.files?.[0] ?? null)} disabled={uploading} />
                </div>
                <div className="field-group">
                  <label htmlFor="visibility">Document access</label>
                  <select id="visibility" value={visibility} onChange={(event) => setVisibility(event.target.value)} disabled={uploading}>
                    <option value="org">Organization</option>
                    <option value="restricted">Restricted</option>
                  </select>
                </div>
                {documentError && <p className="message error" role="alert">{documentError}</p>}
                <button className="button primary" type="submit" disabled={uploading || !file}>
                  {uploading ? "Uploading..." : "Upload document"}
                </button>
              </form>
              <div className="document-heading">
                <h3>Your accessible documents</h3>
                <button type="button" className="text-button" onClick={refreshDocuments} disabled={documentsLoading}>
                  Refresh
                </button>
              </div>
              {deleteError && <p className="message error" role="alert">{deleteError}</p>}
              {documentsLoading ? <p className="message loading" role="status">Loading documents...</p>
                : documentError ? <p className="message empty-state">Document list is unavailable.</p>
                  : documents.length === 0 ? <p className="message empty-state">No accessible documents yet.</p>
                    : <ul className="document-list">{documents.map((document) => (
                      <li key={document.id}>
                        <span>{document.original_filename}</span>
                        <small>{document.status}</small>
                        <button type="button" className="text-button"
                          aria-label={`Delete ${document.original_filename}`}
                          onClick={() => handleDelete(document.id)} disabled={deletingId !== null}>
                          {deletingId === document.id ? "Deleting..." : "Delete"}
                        </button>
                      </li>
                    ))}</ul>}
            </section>
          </section>
        ) : (
          <div className="auth-area">
            {authView === "signup" ? (
              <form className="auth-panel" onSubmit={handleSignup} noValidate aria-labelledby="signup-title">
                <div className="auth-panel-heading">
                  <p className="eyebrow small">New workspace</p>
                  <h2 id="signup-title">Create account</h2>
                  <p className="auth-copy">Start a secure workspace for your organization.</p>
                </div>
                <div className="field-group">
                  <label htmlFor="signup-email">Email</label>
                  <input id="signup-email" name="email" type="email" autoComplete="email" value={signupEmail}
                    onChange={(event) => setSignupEmail(event.target.value)} required disabled={isLoading} />
                </div>
                <div className="field-group">
                  <label htmlFor="organization-name">Organization name</label>
                  <input id="organization-name" name="organization" type="text" autoComplete="organization"
                    maxLength="200" value={organizationName}
                    onChange={(event) => setOrganizationName(event.target.value)} required disabled={isLoading} />
                </div>
                <div className="field-group">
                  <label htmlFor="signup-password">Password</label>
                  <input id="signup-password" name="new-password" type="password" autoComplete="new-password"
                    minLength="8" aria-describedby="password-hint" value={signupPassword}
                    onChange={(event) => setSignupPassword(event.target.value)} required disabled={isLoading} />
                  <small id="password-hint" className="field-hint">Use at least 8 characters.</small>
                </div>
                <div className="field-group">
                  <label htmlFor="confirm-password">Confirm password</label>
                  <input id="confirm-password" name="confirm-password" type="password" autoComplete="new-password"
                    value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)}
                    required disabled={isLoading} />
                </div>
                {signupError && <p className="message error" role="alert">{signupError}</p>}
                <button className="button primary" type="submit" disabled={isLoading}>
                  {isLoading ? "Creating account..." : "Create account"}
                </button>
                {isLoading && <p className="visually-hidden" role="status">Creating account</p>}
                <p className="auth-switch">
                  Already have an account?{" "}
                  <button type="button" className="link-button" onClick={showLogin} disabled={isLoading}>
                    Sign in
                  </button>
                </p>
              </form>
            ) : (
              <form className="auth-panel" onSubmit={handleLogin} noValidate aria-labelledby="login-title">
                <div className="auth-panel-heading">
                  <p className="eyebrow small">Welcome back</p>
                  <h2 id="login-title">Sign in</h2>
                  <p className="auth-copy">Continue to your organization&apos;s workspace.</p>
                </div>
                {signupNotice && <p className="message success" role="status">{signupNotice}</p>}
                <div className="field-group">
                  <label htmlFor="email">Email</label>
                  <input id="email" name="email" type="email" autoComplete="username" value={email}
                    onChange={(event) => setEmail(event.target.value)} required disabled={isLoading} />
                </div>
                <div className="field-group">
                  <label htmlFor="password">Password</label>
                  <input id="password" name="password" type="password" autoComplete="current-password" value={password}
                    onChange={(event) => setPassword(event.target.value)} required disabled={isLoading} />
                </div>
                {errorMessage && <p className="message error" role="alert">{errorMessage}</p>}
                <button className="button primary" type="submit" disabled={isLoading}>
                  {isLoading ? "Signing in..." : "Sign in"}
                </button>
                {isLoading && <p className="visually-hidden" role="status">Signing in</p>}
                <p className="auth-switch">
                  New to the workspace?{" "}
                  <button type="button" className="link-button" onClick={showSignup} disabled={isLoading}>
                    Create account
                  </button>
                </p>
              </form>
            )}
          </div>
        )}
      </section>
    </main>
  );
}
