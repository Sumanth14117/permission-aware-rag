const CSRF_PATH = "/auth/csrf";
const LOGIN_PATH = "/auth/login";
const LOGOUT_PATH = "/auth/logout";
const REGISTER_PATH = "/auth/register";
const TOKEN_PATTERN = /^v1\.(\d{1,12})\.[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{43}$/;
const EXPIRY_SKEW_SECONDS = 5;

let cachedCsrfToken = null;

const ERROR_MESSAGES = {
  invalid_credentials: "Invalid email or password.",
  duplicate_email: "An account with this email already exists.",
  invalid_registration: "Check your details and try again.",
  csrf_rejected: "The security check failed. Refresh the page and try again.",
  unavailable: "Authentication is temporarily unavailable.",
  network: "Could not reach the authentication service. Try again.",
  unexpected: "The authentication request could not be completed.",
};

export class AuthApiError extends Error {
  constructor(code) {
    super(ERROR_MESSAGES[code] ?? ERROR_MESSAGES.unexpected);
    this.name = "AuthApiError";
    this.code = ERROR_MESSAGES[code] ? code : "unexpected";
  }
}

function tokenIsUsable(token) {
  if (typeof token !== "string") return false;
  const match = TOKEN_PATTERN.exec(token);
  if (!match) return false;
  const expiresAt = Number(match[1]);
  return Number.isSafeInteger(expiresAt)
    && expiresAt > Date.now() / 1000 + EXPIRY_SKEW_SECONDS;
}

function invalidateCsrfToken() {
  cachedCsrfToken = null;
}

function errorForStatus(status) {
  if (status === 401) return new AuthApiError("invalid_credentials");
  if (status === 403) {
    invalidateCsrfToken();
    return new AuthApiError("csrf_rejected");
  }
  if (status === 503) return new AuthApiError("unavailable");
  return new AuthApiError("unexpected");
}

async function request(path, options) {
  try {
    return await fetch(path, options);
  } catch {
    throw new AuthApiError("network");
  }
}

async function bootstrapCsrfToken() {
  invalidateCsrfToken();
  const response = await request(CSRF_PATH, {
    method: "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });

  if (response.status === 403) {
    invalidateCsrfToken();
    throw errorForStatus(403);
  }
  if (response.status !== 200) throw errorForStatus(response.status);

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new AuthApiError("unexpected");
  }

  if (!payload || !tokenIsUsable(payload.csrf_token)) {
    throw new AuthApiError("unexpected");
  }
  cachedCsrfToken = payload.csrf_token;
  return cachedCsrfToken;
}

export async function refreshCsrfToken() {
  await bootstrapCsrfToken();
}

async function getCsrfToken() {
  if (tokenIsUsable(cachedCsrfToken)) return cachedCsrfToken;
  invalidateCsrfToken();
  return bootstrapCsrfToken();
}

export async function csrfFetch(path, options) {
  const csrfToken = await getCsrfToken();
  const response = await request(path, {
    ...options,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...options.headers,
      "X-CSRF-Token": csrfToken,
    },
  });

  if (response.status === 403) invalidateCsrfToken();
  return response;
}

async function sendUnsafeRequest(path, options) {
  const response = await csrfFetch(path, options);
  if (response.status !== 204) throw errorForStatus(response.status);
}

export async function login({ email, password }) {
  return sendUnsafeRequest(LOGIN_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export async function register({ email, organizationName, password }) {
  const response = await csrfFetch(REGISTER_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email,
      organization_name: organizationName,
      password,
    }),
  });

  if (response.status === 201) {
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new AuthApiError("unexpected");
    }
    return { signedIn: payload?.signed_in === true };
  }
  if (response.status === 409) throw new AuthApiError("duplicate_email");
  if (response.status === 400 || response.status === 422) {
    throw new AuthApiError("invalid_registration");
  }
  throw errorForStatus(response.status);
}

export async function logout() {
  return sendUnsafeRequest(LOGOUT_PATH, { method: "POST" });
}

