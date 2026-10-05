# Frontend local development

Requires Node.js 24 or newer and npm. From the repository root:

```sh
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173. Vite uses a fixed port and fails if 5173 is occupied.
The UI needs the API for project/run data; start MCP, then the API using the
[backend guide](../backend/README.md).

Alternatively, after the [common setup](../README.md), run `make dev` from the
repository root to start all three services. `make ui` starts only Vite.
With `make dev`, Ctrl-C stops the frontend, then drains the API before stopping
MCP. With separate terminals, stop the services in that same order.

## API proxy

Browser requests to `/api` pass through Vite to `http://127.0.0.1:8000` by
default. For a different API port, set `STATLAB_API_ORIGIN` in the frontend
process environment. From `frontend/`, for example:

```sh
STATLAB_API_ORIGIN=http://127.0.0.1:8001 npm run dev
```

The value must be a loopback HTTP origin without a path, credentials, query or
fragment. `make dev` supplies the configured API origin automatically.
Standalone `npm run dev` and `make ui` do not obtain this value from the root
`.env`; export it or set it inline as above. Restart Vite after changing it.

The root `.env` belongs to the Python services. Do not copy credentials into
the frontend or give secrets a `VITE_` prefix, which exposes them to browser code.

## Static checks and build

From `frontend/`:

```sh
npm run typecheck
npm run build
```

The build writes `frontend/dist/`. It does not start a server.

## Current scope

The browser scaffold lists projects, loads a run snapshot and displays its
event stream. REST facts are captured when loaded; telemetry does not refresh
them. The setup wizard and interactive workflow screens are still pending.
Offline Node tests are present but were not run under the active runtime test
stop; see [plan/WHATS_NEXT.md](../plan/WHATS_NEXT.md) for validation status.
