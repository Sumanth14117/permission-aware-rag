# Frontend development

The frontend is a React application built with Vite. Browser API paths stay
same-origin (for example, `/auth/csrf`); Vite proxies `/auth` to the local
FastAPI server during development.

## Start the development servers

Start FastAPI from the `backend` directory using the environment where the
backend dependencies are installed:

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers
```

`--no-proxy-headers` keeps the ASGI scheme tied to the direct local HTTP
connection. The Vite proxy preserves the browser's `Host` header, which the
CSRF same-origin bootstrap check uses. Do not configure Uvicorn to trust
forwarded headers for this local flow.

Ensure the backend's existing `CORS_ORIGINS` setting includes
`http://localhost:5173`. The frontend requests the API through its own origin;
this setup does not require credentialed CORS.

In a second terminal, from the `frontend` directory:

```powershell
npm install
npm run dev
```

Vite listens at `http://localhost:5173` and proxies `/auth` requests to
`http://127.0.0.1:8000` without rewriting the incoming Host authority.

## Checks

Run from the `frontend` directory:

```powershell
npm test
npm run build
```

Production deployments should route the frontend and `/auth` API paths through
the same origin. The production reverse-proxy configuration is not defined by
this development setup.
