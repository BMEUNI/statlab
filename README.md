# Statlab

Statistical modelling workbench with a Python API, a separate MCP compute server,
and a React frontend.

## Local development setup

Requires Python 3.14 or newer, `uv`, Node.js 24 or newer, npm, and Make with Bash.
Run the following from the repository root:

```sh
make install
cd frontend
npm ci
cd ..
```

If you do not already have a root `.env`, create it:

```sh
cp .env.example .env
```

Edit `.env` and set `STATLAB_MCP_TOKEN` to a long random secret. The API and MCP
server share this token. Provider credentials are deliberately omitted from the
example file. Run the configuration check, add the credential variables named
in any missing-configuration error to `.env`, and repeat until it succeeds:

```sh
make check-env
```

Current model selection and credential handling live in
[backend/statlab/llm/routing.py](backend/statlab/llm/routing.py). Shell environment
variables override `.env`. The check validates local configuration; it does not
verify credentials over the network or prove that services are ready.

## Start all services

From the repository root:

```sh
make dev
```

The launcher starts MCP, waits for its health endpoint, then starts the API and
frontend. Wait for the API startup message before using the browser.

| Service | Default address |
| --- | --- |
| Frontend | http://127.0.0.1:5173 |
| API documentation | http://127.0.0.1:8000/docs |
| MCP transport | http://127.0.0.1:8765/mcp |
| MCP health | http://127.0.0.1:8765/health |

Press Ctrl-C to stop. Shutdown runs frontend → API → MCP and waits for ongoing
work to drain. Backend source and configuration changes require a restart;
the launcher does not enable Python autoreload.

## Start services separately

Use three terminals, all at the repository root, in this order:

```sh
# Terminal 1: wait for MCP startup before starting the API.
make mcp
```

```sh
# Terminal 2
make api
```

```sh
# Terminal 3
make ui
```

Stop them in reverse order: frontend, API, then MCP. Use `make api` or
`.venv/bin/python -m statlab.main` so API shutdown closes event streams before
draining work. Do not run these alongside `make dev` on the same ports.

`make dev` sets the frontend proxy to the configured API address automatically.
When starting the frontend separately with a custom API port, set its server
environment explicitly, for example:

```sh
STATLAB_API_ORIGIN=http://127.0.0.1:8001 make ui
```

Keep the development services on loopback. Never put secrets in `VITE_`
variables; those can be exposed to browser code.

## Data and component guides

By default, local data persists in `projects/` and `statlab.db` at the repository
root. The API applies database migrations at startup. Restarting services keeps
this data. Paths can be changed through `.env`.

- [Backend development](backend/README.md)
- [Frontend development](frontend/README.md)

The current frontend supports project/run inspection and event streaming.
The setup wizard and interactive workflow screens are still pending. Recent
implementation acceptance is static only; runtime and coverage gaps are
recorded in [plan/WHATS_NEXT.md](plan/WHATS_NEXT.md).
