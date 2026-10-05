# Backend local development

The API orchestrates runs and persists their state. Compute tools run in the
separate MCP server. Both Python packages are installed by the root project;
run every command below from the **repository root**, not `backend/`.

## Setup

Requires Python 3.14 or newer and `uv`:

```sh
make install
```

Follow the root [configuration setup](../README.md#local-development-setup) to
create `.env`, set the shared MCP token, and supply the required provider
credentials. Verify the configuration with:

```sh
make check-env
```

This is an offline configuration check. It does not contact the MCP server or
validate credentials with a remote service.

## Start MCP and the API

Start MCP in one terminal:

```sh
make mcp
```

After MCP has started, start the API in a second terminal:

```sh
make api
```

Open http://127.0.0.1:8000/docs for the API documentation. MCP listens at
http://127.0.0.1:8765/mcp and authenticates transport requests with the shared
bearer token. Its health endpoint is http://127.0.0.1:8765/health.

`make api` uses `.venv/bin/python -m statlab.main`. This is the supported API
launcher: its signal handling closes event streams before draining owned work.
Keep MCP running while the API shuts down. Stop the API first, then MCP;
shutdown may wait for in-flight work. Restart after Python source or
configuration changes because autoreload is not enabled.

To include the frontend, use `make dev` after completing the
[common setup](../README.md). It manages all three processes and their shutdown.

## Configuration and storage

Both services read the root `.env`; shell environment variables take priority.
The main development settings are:

| Variable | Default / requirement |
| --- | --- |
| `STATLAB_MCP_TOKEN` | Required, nonblank shared secret |
| `STATLAB_DATA_DIR` | `projects` |
| `STATLAB_DB_PATH` | `statlab.db` |
| `STATLAB_API_HOST` / `STATLAB_API_PORT` | `127.0.0.1` / `8000` |
| `STATLAB_MCP_HOST` / `STATLAB_MCP_PORT` | `127.0.0.1` / `8765` |
| `STATLAB_MCP_URL` | `http://127.0.0.1:8765/mcp` |

Relative storage paths resolve from the working directory, which is why the
commands must run at the repository root. The API creates storage directories
and applies SQLite migrations at startup. Local state survives restarts.
If you change the MCP host or port, update `STATLAB_MCP_URL` to match.
Keep local development hosts on loopback.

See [.env.example](../.env.example) for the remaining settings and
[statlab/llm/routing.py](statlab/llm/routing.py) for model routing and credentials.

## Static checks

```sh
make typecheck
make lint
```

Runtime tests remain stopped under the current workflow. Static acceptance
does not establish runtime or coverage acceptance; see
[plan/WHATS_NEXT.md](../plan/WHATS_NEXT.md) for the outstanding validation.
