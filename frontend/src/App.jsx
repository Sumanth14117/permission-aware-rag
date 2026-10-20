import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { login, logout, register } from "./authApi.js";
import { askQuestion, deleteDocument, listDocuments, uploadDocument } from "./workspaceApi.js";

const iconPaths = {
  spark: <><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z" /><path d="m19 15 .9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15Z" /></>,
  files: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6M8 13h8M8 17h8" /></>,
  upload: <><path d="M12 16V4m0 0L7 9m5-5 5 5" /><path d="M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" /></>,
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
  arrow: <><path d="M7 17 17 7M7 7h10v10" /></>,
  logout: <><path d="M10 17l5-5-5-5M15 12H3" /><path d="M12 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-6" /></>,
  shield: <><path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z" /><path d="m9 12 2 2 4-4" /></>,
  check: <><path d="m5 12 4 4L19 6" /></>,
  trash: <><path d="M3 6h18M8 6V4h8v2m3 0-1 14H6L5 6m4 4v6m6-6v6" /></>,
  plus: <><path d="M12 5v14M5 12h14" /></>,
  doc: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6M8 13h8M8 17h5" /></>,
};

function Icon({ name, size = 18 }) {
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{iconPaths[name]}</svg>;
}

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

function scrollToTop() {
  if (window.scrollY > 0 || document.documentElement.scrollTop > 0 || document.body.scrollTop > 0) {
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  }
}

function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.valueOf())
    ? new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(date)
    : "—";
}

function AuthScreen({
  authView, setAuthView, isLoading, email, setEmail, password, setPassword,
  signupEmail, setSignupEmail, organizationName, setOrganizationName,
  signupPassword, setSignupPassword, confirmPassword, setConfirmPassword,
  errorMessage, signupError, signupNotice, handleLogin, handleSignup,
  showLogin, showSignup,
}) {
  const signingUp = authView === "signup";
  return (
    <main className="auth-page">
      <section className="auth-card" aria-label="Permission-aware workspace authentication">
        <div className="auth-form-side">
          <div className="brand-lockup">
            <span className="brand-mark"><Icon name="spark" size={20} /></span>
            <span>Verdant<span className="brand-period">.</span><small>KNOWLEDGE WORKSPACE</small></span>
          </div>

          {signingUp ? (
            <form className="auth-form" onSubmit={handleSignup} noValidate aria-labelledby="signup-title">
              <div className="auth-heading">
                <p className="eyebrow">A clearer way to work</p>
                <h1 id="signup-title">Create account</h1>
                <p>Bring your team&apos;s knowledge together, with access kept in the right hands.</p>
              </div>
              <div className="field-group">
                <label htmlFor="email">Email</label>
                <input id="email" name="email" type="email" autoComplete="email" placeholder="you@company.com" value={signupEmail} onChange={(event) => setSignupEmail(event.target.value)} required disabled={isLoading} />
              </div>
              <div className="field-group">
                <label htmlFor="organization-name">Organization name</label>
                <input id="organization-name" name="organization" type="text" autoComplete="organization" placeholder="Your organization" maxLength="200" value={organizationName} onChange={(event) => setOrganizationName(event.target.value)} required disabled={isLoading} />
              </div>
              <div className="field-group">
                <label htmlFor="password">Password</label>
                <input id="password" name="new-password" type="password" autoComplete="new-password" minLength="8" aria-describedby="password-hint" placeholder="At least 8 characters" value={signupPassword} onChange={(event) => setSignupPassword(event.target.value)} required disabled={isLoading} />
                <small id="password-hint" className="field-hint">Use at least 8 characters.</small>
              </div>
              <div className="field-group">
                <label htmlFor="confirm-password">Confirm password</label>
                <input id="confirm-password" name="confirm-password" type="password" autoComplete="new-password" placeholder="Enter your password again" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required disabled={isLoading} />
              </div>
              {signupError && <p className="message error" role="alert">{signupError}</p>}
              <button className="button primary auth-submit" type="submit" disabled={isLoading}>{isLoading ? "Creating workspace..." : "Create workspace"}</button>
              {isLoading && <p className="visually-hidden" role="status">Creating account</p>}
              <p className="auth-switch">Already have a workspace? <button type="button" className="link-button" onClick={showLogin} disabled={isLoading}>Sign in</button></p>
            </form>
          ) : (
            <form className="auth-form" onSubmit={handleLogin} noValidate aria-labelledby="login-title">
              <div className="auth-heading">
                <p className="eyebrow">Welcome back</p>
                <h1 id="login-title">Sign in</h1>
                <p>Sign in to pick up where your team&apos;s knowledge leaves off.</p>
              </div>
              {signupNotice && <p className="message success" role="status">{signupNotice}</p>}
              <div className="field-group">
                <label htmlFor="email">Email</label>
                <input id="email" name="email" type="email" autoComplete="username" placeholder="you@company.com" value={email} onChange={(event) => setEmail(event.target.value)} required disabled={isLoading} />
              </div>
              <div className="field-group">
                <div className="label-row"><label htmlFor="password">Password</label><span>Secure sign in</span></div>
                <input id="password" name="password" type="password" autoComplete="current-password" placeholder="Enter your password" value={password} onChange={(event) => setPassword(event.target.value)} required disabled={isLoading} />
              </div>
              {errorMessage && <p className="message error" role="alert">{errorMessage}</p>}
              <button className="button primary auth-submit" type="submit" disabled={isLoading}>{isLoading ? "Signing in..." : "Sign in"}</button>
              {isLoading && <p className="visually-hidden" role="status">Signing in</p>}
              <p className="auth-switch">New to Verdant? <button type="button" className="link-button" onClick={showSignup} disabled={isLoading}>Create account</button></p>
            </form>
          )}
          <p className="auth-footnote"><Icon name="shield" size={15} /> Your session stays private and secure.</p>
        </div>
        <aside className="auth-story" aria-label="Workspace features">
          <div className="story-orbit orbit-one" /><div className="story-orbit orbit-two" />
          <span className="story-kicker"><span className="status-dot" /> YOUR KNOWLEDGE, IN CONTEXT</span>
          <div className="story-copy">
            <div className="story-symbol"><Icon name="spark" size={30} /></div>
            <h2>Find the signal<br />in your <em>knowledge.</em></h2>
            <p>Ask naturally. Get grounded answers from the documents you&apos;re allowed to see.</p>
          </div>
          <div className="story-trust"><span className="trust-icon"><Icon name="shield" size={17} /></span><span><strong>Private by design</strong><small>Answers respect your workspace access</small></span><Icon name="check" size={17} /></div>
          <span className="story-caption">A calmer place for better questions.</span>
        </aside>
      </section>
    </main>
  );
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
  const [uploadError, setUploadError] = useState("");
  const [file, setFile] = useState(null);
  const [visibility, setVisibility] = useState("org");
  const [uploading, setUploading] = useState(false);
  const [uploadNotice, setUploadNotice] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [deleteError, setDeleteError] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState(null);
  const [asking, setAsking] = useState(false);
  const [qaError, setQaError] = useState("");
  const [activeView, setActiveView] = useState("ask");
  const [documentSearch, setDocumentSearch] = useState("");
  const inFlight = useRef(false);
  const fileInput = useRef(null);

  useLayoutEffect(() => {
    const previousScrollRestoration = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";
    scrollToTop();
    return () => {
      window.history.scrollRestoration = previousScrollRestoration;
    };
  }, []);

  useLayoutEffect(() => {
    scrollToTop();
  }, [authView, isAuthenticated]);

  function navigateToView(view) {
    if (activeView === view) return;
    setActiveView(view);
    scrollToTop();
  }

  async function refreshDocuments() {
    setDocumentsLoading(true);
    setDocumentError("");
    try {
      const result = await listDocuments();
      setDocuments(Array.isArray(result) ? result : []);
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
      scrollToTop();
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
      const result = await register({ email: signupEmail.trim(), organizationName: organizationName.trim(), password: signupPassword });
      setSignupPassword("");
      setConfirmPassword("");
      setEmail(signupEmail.trim());
      setErrorMessage("");
      if (result.signedIn) {
        scrollToTop();
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
    } catch (error) {
      setErrorMessage(safeAuthMessage(error, "logout"));
    } finally {
      scrollToTop();
      setIsAuthenticated(false);
      setDocuments([]);
      setPassword("");
      setQuestion("");
      setAnswer(null);
      setActiveView("ask");
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

  function selectFile(nextFile) {
    if (!nextFile) return;
    setFile(nextFile);
    setUploadNotice("");
    setUploadError("");
    setDocumentError("");
  }

  async function handleUpload(event) {
    event.preventDefault();
    if (!file || uploading) return;
    const form = event.currentTarget;
    setUploading(true);
    setUploadError("");
    setUploadNotice("");
    try {
      const result = await uploadDocument(file, visibility);
      const uploadedName = file.name;
      setFile(null);
      form.reset();
      setUploadNotice(`${uploadedName} · ${result?.status ?? "uploaded"}`);
      await refreshDocuments();
    } catch (error) {
      setUploadError(safeWorkspaceMessage(error));
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

  const readyCount = documents.filter((document) => document.status === "ready").length;
  const processingCount = documents.filter((document) => ["pending", "processing"].includes(document.status)).length;
  const visibleDocuments = documents.filter((document) => document.original_filename?.toLowerCase().includes(documentSearch.trim().toLowerCase()));

  if (!isAuthenticated) {
    return <AuthScreen
      authView={authView} setAuthView={setAuthView} isLoading={isLoading}
      email={email} setEmail={setEmail} password={password} setPassword={setPassword}
      signupEmail={signupEmail} setSignupEmail={setSignupEmail}
      organizationName={organizationName} setOrganizationName={setOrganizationName}
      signupPassword={signupPassword} setSignupPassword={setSignupPassword}
      confirmPassword={confirmPassword} setConfirmPassword={setConfirmPassword}
      errorMessage={errorMessage} signupError={signupError} signupNotice={signupNotice}
      handleLogin={handleLogin} handleSignup={handleSignup}
      showLogin={showLogin} showSignup={showSignup}
    />;
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand-lockup app-brand" href="#workspace" aria-label="Verdant workspace">
          <span className="brand-mark"><Icon name="spark" size={19} /></span>
          <span>Verdant<span className="brand-period">.</span><small>KNOWLEDGE WORKSPACE</small></span>
        </a>
        <nav className="top-nav" aria-label="Main navigation">
          <button type="button" aria-current={activeView === "ask" ? "page" : undefined} className={activeView === "ask" ? "top-nav-item active" : "top-nav-item"} onClick={() => navigateToView("ask")}>Ask your docs</button>
          <button type="button" aria-current={activeView === "documents" ? "page" : undefined} className={activeView === "documents" ? "top-nav-item active" : "top-nav-item"} onClick={() => navigateToView("documents")}>Documents</button>
        </nav>
        <div className="topbar-actions">
          <label className="top-search"><Icon name="search" size={17} /><span className="visually-hidden">Search documents</span><input aria-label="Search documents" placeholder="Search documents" value={documentSearch} onFocus={() => setActiveView("documents")} onChange={(event) => { setDocumentSearch(event.target.value); setActiveView("documents"); }} /></label>
          <span className="profile-pill"><span className="profile-avatar">V</span><span>Workspace</span></span>
          <button className="icon-button logout-button" type="button" onClick={handleLogout} disabled={isLoading} aria-label={isLoading ? "Signing out" : "Sign out"} title="Sign out"><Icon name="logout" /></button>
        </div>
      </header>

      <div className="workspace-layout">
        <aside className="sidebar" aria-label="Workspace sections">
          <div className="sidebar-label">WORKSPACE</div>
          <button type="button" className={`side-link ${activeView === "ask" ? "selected" : ""}`} onClick={() => navigateToView("ask")}><span className="side-icon"><Icon name="spark" /></span><span>Ask your docs</span></button>
          <button type="button" className={`side-link ${activeView === "documents" ? "selected" : ""}`} onClick={() => navigateToView("documents")}><span className="side-icon"><Icon name="files" /></span><span>Documents</span><span className="side-count">{documents.length}</span></button>
          <div className="sidebar-note"><span className="note-mark"><Icon name="shield" size={17} /></span><p><strong>Your workspace, protected</strong><br />Answers only use sources you can access.</p></div>
          <div className="sidebar-bottom"><span className="online-indicator" /> Session held in a secure HTTP-only cookie</div>
        </aside>

        <main className="main-content" id="workspace">
          {errorMessage && <p className="message error top-error" role="alert">{errorMessage}</p>}
          {activeView === "ask" ? (
            <section className="ask-view" aria-labelledby="ask-title">
              <div className="page-heading ask-heading">
                <div><p className="eyebrow">YOUR KNOWLEDGE, IN CONTEXT</p><h1 id="ask-title">Ask your documents</h1><p>Get clear answers grounded in the sources you&apos;re allowed to access.</p></div>
                <div className="heading-decoration" aria-hidden="true"><span /><span /><span /></div>
              </div>

              <div className="overview-grid" aria-label="Accessible document overview">
                <div className="overview-card"><span className="overview-icon"><Icon name="files" /></span><span className="overview-copy"><small>Accessible documents</small><strong>{documentsLoading ? "—" : documents.length}</strong></span><span className="overview-caption">in your workspace</span></div>
                <div className="overview-card"><span className="overview-icon ready"><Icon name="check" /></span><span className="overview-copy"><small>Ready to search</small><strong>{documentsLoading ? "—" : readyCount}</strong></span><span className="overview-caption">processed sources</span></div>
                <div className="overview-card"><span className="overview-icon pending"><span className="mini-spinner" /></span><span className="overview-copy"><small>Processing</small><strong>{documentsLoading ? "—" : processingCount}</strong></span><span className="overview-caption">documents in progress</span></div>
              </div>

              <section className="question-card" aria-labelledby="question-card-title">
                <div className="section-title-row"><div><span className="section-kicker">ASK THE KNOWLEDGE BASE</span><h2 id="question-card-title">What would you like to know?</h2></div><span className="private-badge"><Icon name="shield" size={14} /> Private search</span></div>
                <form className="question-form" onSubmit={handleAsk}>
                  <label className="visually-hidden" htmlFor="question">Question</label>
                  <textarea id="question" rows="3" maxLength="10000" value={question} onChange={(event) => setQuestion(event.target.value)} disabled={asking} placeholder="Ask a question about your organization's documents..." required />
                  <div className="composer-footer"><span>Answers are based on your accessible documents</span><button className="button primary ask-button" type="submit" disabled={asking || !question.trim()}>{asking ? <><span className="button-spinner" /> Searching</> : <>Ask question <Icon name="arrow" size={16} /></>}</button></div>
                </form>
                {qaError && <p className="message error" role="alert">{qaError}</p>}
                {asking && <div className="search-state" role="status"><span className="search-pulse"><Icon name="spark" /></span><span><strong>Searching your sources</strong><small>Checking documents you can access...</small></span></div>}
              </section>

              {answer ? (
                <section className="answer-card" aria-live="polite" aria-labelledby="answer-title">
                  <div className="answer-header"><span className="answer-icon"><Icon name="spark" size={18} /></span><div><span className="section-kicker">VERDANT ANSWER</span><h2 id="answer-title">Here&apos;s what I found</h2></div><span className="answer-source-count">{answer.citations?.length ?? 0} {(answer.citations?.length ?? 0) === 1 ? "source" : "sources"}</span></div>
                  <p className="answer-text">{answer.answer}</p>
                  {answer.citations?.length ? <div className="source-block"><div className="source-heading"><span>Sources</span><span>Verified references</span></div><ul className="source-list">{answer.citations.map((citation) => <li key={`${citation.document_id}:${citation.chunk_index}`}><span className="source-file-icon"><Icon name="doc" size={16} /></span><span className="source-details"><strong>Document source</strong><code>{citation.document_id}</code></span><span className="chunk-badge">Chunk {citation.chunk_index}</span></li>)}</ul></div> : <p className="answer-empty">{answer.has_retrieved_sources && answer.abstained ? "The available sources did not provide enough evidence to answer this question. Try asking about another detail." : answer.has_retrieved_sources ? "No source citations were returned for this response." : "No accessible source chunks matched this question. Try asking about another document."}</p>}
                </section>
              ) : !asking && !qaError ? (
                <section className="quiet-empty" aria-label="Ask a question to begin"><span className="empty-orbit"><Icon name="spark" size={23} /></span><div><strong>Your answers start with a good question.</strong><p>Ask about policies, plans, or details in your accessible files. Every answer comes with source citations.</p></div></section>
              ) : null}
            </section>
          ) : (
            <section className="library-view" aria-labelledby="documents-title">
              <div className="page-heading library-heading"><div><p className="eyebrow">YOUR SOURCE LIBRARY</p><h1 id="documents-title">Documents</h1><p>Manage the files available to your workspace.</p></div><button className="button primary add-document-button" type="button" onClick={() => fileInput.current?.click()}><Icon name="plus" /> Upload document</button></div>
              <div className="library-layout">
                <section className="upload-card" aria-labelledby="upload-title">
                  <div className="upload-card-heading"><span className="upload-heading-icon"><Icon name="upload" /></span><div><h2 id="upload-title">Add a document</h2><p>Upload a file to make it searchable.</p></div></div>
                  <form className="upload-form" onSubmit={handleUpload}>
                    <label className={`dropzone ${dragActive ? "is-dragging" : ""} ${file ? "has-file" : ""}`} htmlFor="document-file" onDragOver={(event) => { event.preventDefault(); setDragActive(true); }} onDragLeave={() => setDragActive(false)} onDrop={(event) => { event.preventDefault(); setDragActive(false); selectFile(event.dataTransfer.files?.[0]); }}>
                      <input ref={fileInput} id="document-file" aria-label="Upload a text or PDF document" type="file" accept=".txt,.pdf,text/plain,application/pdf" onChange={(event) => selectFile(event.target.files?.[0])} disabled={uploading} />
                      <span className="dropzone-icon"><Icon name={file ? "doc" : "upload"} size={22} /></span>
                      {file ? <><strong>{file.name}</strong><small>{formatSize(file.size)} · Ready to upload</small></> : <><strong>Drop your file here</strong><small>or <span className="browse-link">browse files</span></small><span className="file-types">PDF or TXT · Up to 10 MB</span></>}
                    </label>
                    <div className="field-group visibility-field"><label htmlFor="visibility">Who can access this?</label><select id="visibility" value={visibility} onChange={(event) => setVisibility(event.target.value)} disabled={uploading}><option value="org">Everyone in my organization</option><option value="restricted">Restricted access</option></select></div>
                    {uploadError && <p className="message error" role="alert">{uploadError}</p>}
                    {uploadNotice && <p className="message success" role="status"><Icon name="check" size={16} /> Uploaded · {uploadNotice}</p>}
                    <button className="button primary upload-button" type="submit" disabled={uploading || !file}>{uploading ? <><span className="button-spinner" /> Uploading and processing</> : "Upload document"}</button>
                    {uploading && <div className="upload-progress" role="status"><span /><small>Uploading and preparing your document…</small></div>}
                  </form>
                  <p className="upload-privacy"><Icon name="shield" size={14} /> Stored securely in your organization workspace</p>
                </section>

                <section className="documents-card" aria-labelledby="library-list-title">
                  <div className="documents-card-header"><div><span className="section-kicker">WORKSPACE FILES</span><h2 id="library-list-title">Your documents <span className="count-pill">{documentsLoading ? "…" : documents.length}</span></h2></div><button type="button" className="icon-button refresh-button" onClick={refreshDocuments} disabled={documentsLoading} aria-label="Refresh document list" title="Refresh"><span className={documentsLoading ? "refresh-glyph spinning" : "refresh-glyph"}>↻</span></button></div>
                  <label className="library-search"><Icon name="search" size={16} /><span className="visually-hidden">Filter documents by filename</span><input value={documentSearch} onChange={(event) => setDocumentSearch(event.target.value)} placeholder="Filter by filename" /></label>
                  {deleteError && <p className="message error" role="alert">{deleteError}</p>}
                  {documentsLoading ? <div className="library-state" role="status"><span className="button-spinner" /> Loading your documents…</div>
                    : documentError ? <div className="library-empty"><span className="empty-file-icon"><Icon name="files" /></span><strong>Documents are unavailable</strong><p>{documentError}</p><button type="button" className="text-button" onClick={refreshDocuments}>Try again</button></div>
                      : visibleDocuments.length === 0 ? <div className="library-empty"><span className="empty-file-icon"><Icon name="files" /></span><strong>{documents.length ? "No matching documents" : "Your library is ready"}</strong><p>{documents.length ? "Try a different filename." : "Upload your first file to make it searchable by your team."}</p></div>
                        : <div className="document-table-wrap"><table className="document-table"><thead><tr><th scope="col">Name</th><th scope="col">Access</th><th scope="col">Added</th><th scope="col">Status</th><th scope="col"><span className="visually-hidden">Actions</span></th></tr></thead><tbody>{visibleDocuments.map((document) => <tr key={document.id}>
                          <td><span className="table-file-icon"><Icon name="doc" size={17} /></span><span className="table-file-name"><strong>{document.original_filename}</strong><small>{(document.content_type || "Document").replace("application/", "").replace("text/", "").toUpperCase()} · {formatSize(document.size_bytes)}</small></span></td>
                          <td><span className={`access-label ${document.visibility === "restricted" ? "restricted" : ""}`}>{document.visibility === "restricted" ? "Restricted" : "Organization"}</span></td>
                          <td className="date-cell">{formatDate(document.created_at)}</td>
                          <td><span className={`status-badge status-${document.status || "pending"}`}><span />{document.status || "pending"}</span></td>
                          <td><button type="button" className="row-delete" aria-label={`Delete ${document.original_filename}`} onClick={() => handleDelete(document.id)} disabled={deletingId !== null}>{deletingId === document.id ? <span className="button-spinner" /> : <Icon name="trash" size={16} />}</button></td>
                        </tr>)}</tbody></table></div>}
                  <div className="library-footer"><span>Showing accessible documents only</span><span className="footer-lock"><Icon name="shield" size={13} /> Private to your workspace</span></div>
                </section>
              </div>
            </section>
          )}
          <footer className="workspace-footer"><span>VERDANT · PRIVATE KNOWLEDGE, CLEAR ANSWERS</span><span><Icon name="shield" size={13} /> Session protected</span></footer>
        </main>
      </div>
    </div>
  );
}
