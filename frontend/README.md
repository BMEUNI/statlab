# Statlab browser scaffold

Requires Node.js 24 or newer. From `frontend/`, run `npm install`, then
`npm run typecheck` or `npm run build`. `make ui` serves Vite; `make dev` starts
the local MCP, API and UI processes with ordered shutdown.

Set `STATLAB_API_ORIGIN` only in the server environment to change the loopback
HTTP API proxy origin. Root `.env` values are server configuration and must
never use a `VITE_` prefix for secrets.

The launcher relies on the supported backend early-signal shutdown path. This
scaffold lists projects, loads a run snapshot and displays its event stream;
REST facts are a snapshot captured when loaded and telemetry does not refresh
them. Offline Node tests are present but were not run under the active runtime
test stop.
