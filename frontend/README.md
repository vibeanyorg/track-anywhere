# Track Anywhere Frontend

Next.js 16 App Router frontend for the Track Anywhere web experience.

Node.js 22 is the supported frontend runtime.

Run the FastAPI backend first:

```bash
uv run uvicorn track_anywhere.api:app --app-dir ../backend/app --host 127.0.0.1 --port 8000
```

Then run the frontend:

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

`TRACK_ANYWHERE_BACKEND_URL` is used by the App Router `/api/v2/*` route handler
to proxy browser calls to the backend. The supported browser auth flow calls:

- `POST /api/v2/auth/session/api-key`
- `GET /api/v2/auth/session`
- `POST /api/v2/auth/logout`
- `POST /api/v2/oauth/register`
- `POST /api/v2/oauth/authorize`
- `POST /api/v2/oauth/token`
- `POST /api/v2/oauth/revoke`

The CLI browser login lands on `/auth/callback`, authorizes the default platform
client, and displays a callback URL that the CLI exchanges with
`/api/v2/oauth/token`. OAuth device authorization is also available through the
V2 backend for CLI clients.

Password login/signup, local development tokens, and credential creation,
listing, or revocation are not exposed by the V2 frontend. API keys must be
provisioned through an approved administrative workflow before sign-in.
