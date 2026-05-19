# Track Anywhere Frontend

Next.js 16 App Router frontend for the Track Anywhere web experience.

Run the Django backend first:

```bash
uv run python backend_django/manage.py runserver 8001
```

Then run the frontend:

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

`TRACK_ANYWHERE_BACKEND_URL` is used by `next.config.mjs` to rewrite `/api/*`
to the backend. The header auth flow calls:

- `GET /api/v1/auth/session`
- `POST /api/v1/auth/password/login`
- `POST /api/v1/auth/password/signup`
- `POST /api/v1/session/dev-local`
- `POST /api/v1/auth/logout`
- `POST /api/v1/oauth/authorize`

The CLI browser login lands on `/auth/callback`, authorizes the default platform
client, and displays a callback URL that the CLI exchanges with
`/api/v1/oauth/token`.
