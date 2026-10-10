---
status: completed
dependencies: [73]
completion_date: 2026-10-10
implementation_notes: "make share serves a built UI with vite preview on 4173 and Secure cookies; Tailscale Funnel exposes it"
---

# Chunk 74 — share the app through a Tailscale Funnel

User request 2026-10-10: make the app reachable for a consultant and friends on a free
platform. Decision (user): a tunnel from the user's Mac; Tailscale Funnel (stable
`https://<mac>.<tailnet>.ts.net`, no request cap). Cloudflare quick tunnels were rejected: no
server-sent events (the run stream needs them). ngrok free was rejected: 20,000 requests/month.

Decisions:
1. `make share` builds the frontend, then runs the existing three-process launcher with the UI
   process as `vite preview` (port 4173, inherits `server.host`, `server.strictPort` and
   `server.proxy`) instead of the dev server. The dev server is not exposed.
2. `make share` sets `STATLAB_COOKIE_SECURE=true` (the tunnel is HTTPS). `dev.py share` refuses
   to start without it.
3. `preview.allowedHosts` is `['.ts.net']`; the dev server keeps Vite's default host check.
4. The API and MCP stay on loopback; Funnel connects to 127.0.0.1:4173. No backend change.
Non-goals: closing registration (budgets cover strangers; the admin sets a low default monthly
budget before sharing), a hosted deployment, the Tailscale install itself (user action).

## Stage 74a (detailed spec)

Owned: `scripts/dev.py`, `tests/test_dev.py`, `frontend/vite.config.ts`, `Makefile`. Not alone:
preserve others' edits.

1. `scripts/dev.py`:
   - `run_dev(settings: Settings, tools: ToolSettings, ui_args: tuple[str, ...] = ()) -> int`.
     The UI `Popen` command becomes `[str(node_path), str(vite), *ui_args]`. Nothing else in
     `run_dev` changes.
   - `main`: accepted argument lists become `(["check"], ["dev"], ["share"])`; usage text
     `"Usage: dev.py check|dev|share"`. After the `"check"` branch, add: if `arguments[0] ==
     "share"`: if `not settings.cookie_secure`: print `"make share needs
     STATLAB_COOKIE_SECURE=true."` to stderr and return 2; otherwise `return run_dev(settings,
     tools, ("preview",))`. The existing `return run_dev(settings, tools)` stays for `"dev"`.
2. `frontend/vite.config.ts`: in the `defineConfig` object, after `server`, add
   `preview: { allowedHosts: ['.ts.net'] },`. Nothing else changes.
3. `Makefile`: add `share` to `.PHONY` (after `dev`). After the `dev` target add:
   - `share: ## Serve a built UI on 4173 for a Tailscale Funnel (Secure cookies)`
   - recipe line 1: `cd frontend && node node_modules/vite/bin/vite.js build`
   - recipe line 2: `STATLAB_COOKIE_SECURE=true $(PY) scripts/dev.py share`
4. `tests/test_dev.py`:
   - `test_check_configuration_order_errors_usage_and_interrupt`: expected usage stderr becomes
     `"Usage: dev.py check|dev|share\n"`.
   - New `test_share_runs_vite_preview_with_secure_cookies(monkeypatch, tmp_path, capsys)`:
     a. `settings, tools = configuration(tmp_path)`; `mcp, api, ui = FakeProcess("mcp"),
        FakeProcess("api"), FakeProcess("ui")`; `commands, _, _ = install_runtime(monkeypatch,
        [mcp, api, ui])`; patch `time.sleep` with a function that sets `api.code = 3`; assert
        `dev.run_dev(settings, tools, ("preview",)) == 3`; assert `list(commands[2][0]) ==
        ["/usr/bin/node", str(dev._ROOT / "frontend/node_modules/vite/bin/vite.js"),
        "preview"]`.
     b. Record calls: `calls: list[tuple[str, ...]] = []`; patch `dev.run_dev` with a function
        `(settings, tools, ui_args=()) -> int` that appends `ui_args` and returns 0. Patch
        `dev.load_tool_settings` to return `tools` and `dev.check_startup_environment` to a
        no-op. Patch `dev.load_settings` to return `settings` (cookie_secure is False): assert
        `dev.main(["share"]) == 2`, `capsys.readouterr().err == "make share needs
        STATLAB_COOKIE_SECURE=true.\n"`, `calls == []`.
     c. Patch `dev.load_settings` to return `settings.model_copy(update={"cookie_secure":
        True})`: assert `dev.main(["share"]) == 0` and `calls == [("preview",)]`; then
        `dev.main(["dev"]) == 0` and `calls == [("preview",), ()]`.
     Type annotations as in the surrounding tests.

Checks: `make test-fast TESTS="tests/test_dev.py"`; `.venv/bin/python -m ruff check
scripts/dev.py tests/test_dev.py`; `.venv/bin/python -m ruff format --check scripts/dev.py
tests/test_dev.py`; `.venv/bin/python -m mypy tests/test_dev.py`; from `frontend/`:
`npm run typecheck`, `npm run build`. Stop and report on any failure.

Main then: review; browser check through `vite preview` on scratch ports (sign-in, a run page's
live stream); `make check`; README section; commit.

## Acceptance receipt (2026-10-10)

Implementer: `make test-fast TESTS="tests/test_dev.py"` 25 passed; Ruff check/format (after a
mechanical `ruff format` of the new test) and mypy on the owned Python files exit 0;
`npm run typecheck` and `npm run build` exit 0. Main reviewed the diff (matches the recipe).

Runtime (main, scratch copy of `integration/out/20261010T080829Z-offline`, MCP 8767, API 8766
with `STATLAB_COOKIE_SECURE=true`, `vite preview --port 4175`): `Host: mymac.tail1234.ts.net`
gives 200 for `/` and `/api/auth/config`; `Host: evil.example.com` gives 403; login through the
proxy sets `statlab_session` with `HttpOnly; SameSite=strict; Secure`; `/api/runs/{id}/events`
through the proxy answers `text/event-stream` without content-encoding and streamed 65 events
while held open; Chromium signed in on `http://127.0.0.1:4175` and rendered the run page
(`.playwright-mcp/74-preview-run.png`). Scratch servers stopped.

`make check` (`check74.log`, temporary) exit 0: coverage 100 % (TOTAL 13367 stmts, 0 missed),
mypy 212 files, Ruff clean. README gains "Share with other people (Tailscale Funnel)".
The Funnel itself (user's Tailscale account) is not exercised here.
