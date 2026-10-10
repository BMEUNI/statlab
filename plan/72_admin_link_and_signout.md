---
status: completed
dependencies: [71]
completion_date: 2026-10-10
implementation_notes: "account menu buttons visible; Admin link in the top bar"
---

# Chunk 72 — reach the admin panel; sign out works

User report 2026-10-10: "there is no easy way to reach the admin panel for admins, also I cant
log out".

Cause (browser, scratch servers): the account menu opens, but its `Admin panel` link and `Sign
out` button inherit `.topnav a, .topnav button` (white text, transparent background, white-ish
border; `frontend/src/style.css` ~46-47), so they are invisible on the white popup. The admin
panel is only reachable through that hidden link.

Decisions: (1) buttons inside the account popup use the normal surface look; (2) admins get an
`Admin` link in the top bar after `Projects & runs`, marked current on admin routes.

## Stage 72a (detailed spec)

Owned: `frontend/src/style.css`, `frontend/src/App.tsx`. Not alone: preserve others' edits.

1. `style.css`: replace the rule `.user-pop .button, .user-pop button { width: 100%;
   margin-top: 8px; }` with the selector `.topnav .user-pop .button, .topnav .user-pop button`
   and declarations width 100%, margin-top 8px, color `var(--ink)`, background
   `var(--surface)`, border-color `var(--rule)`. Add `.topnav .user-pop .button:hover, .topnav
   .user-pop button:hover` with background `var(--paper)`.
2. `App.tsx`: import `ShieldCheck` from `lucide-react` (existing import line). In the
   `nav.topnav`, directly after the `Projects & runs` link, when `user.is_admin`: an `a` with
   class `button`, href `#/admin`, `aria-current={route.name === 'admin' ? 'page' :
   undefined}`, containing `<ShieldCheck className="icon" aria-hidden="true" />` and the text
   `Admin`.

Checks (from `frontend/`): `npm test`, `npm run typecheck`, `npm run build`. Stop and report on
any failure.

## Acceptance receipt (2026-10-10)

Implementer: `npm test` 113/113, `npm run typecheck` exit 0, `npm run build` exit 0. Main
reviewed the diff (matches the recipe). Browser (scratch servers 8766/8767/5174, harness admin):
top bar shows `Admin` after `Projects & runs`; the account menu shows visible `Admin panel` and
`Sign out` buttons (`.playwright-mcp/72-usermenu.png`); `Sign out` returns to the sign-in page.
Frontend-only change: no backend gate rerun.
