# Track Anywhere Frontend

The browser UI is a Next.js 16 static export. Node.js 22 is a build dependency,
not a production service: the final Docker image copies `frontend/out` into the
FastAPI runtime, which serves the UI, REST API, OAuth discovery, and MCP from
one origin.

Build and validate the export:

```bash
npm ci
npm test
npm run lint
npm run build
```

For an integrated local run, start PostgreSQL and the application with
`scripts/deploy-local.sh`. To exercise a source-built export without Docker,
build it and point FastAPI at the result:

```bash
npm run build
cd ..
TRACK_ANYWHERE_STATIC_DIRECTORY=frontend/out \
TRACK_ANYWHERE_DATABASE_URL='postgresql+psycopg://...' \
TRACK_ANYWHERE_PUBLIC_BASE_URL=http://127.0.0.1:8000 \
uv run uvicorn track_anywhere.server:app \
  --app-dir backend/app --host 127.0.0.1 --port 8000
```

The browser uses same-origin endpoints including sessions, OAuth, discovery,
REST under `/api/v2`, and MCP at `/mcp`. The CLI authorization callback is
`/auth/callback`; device authorization uses `/auth/device`.

On a private instance, `/auth/signup` requires the existing owner's personal
API key as a one-time setup proof, binds the password to that exact user, and
then closes. Returning users sign in with email and password at `/auth/login`;
the personal API key remains available there as a secondary login method.

`npm run dev` remains useful for presentation-only UI iteration. Run a static
build through FastAPI before testing authentication or protocol behavior.
