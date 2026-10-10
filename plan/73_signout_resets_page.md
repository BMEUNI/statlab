---
status: completed
dependencies: [72]
completion_date: 2026-10-10
implementation_notes: "sign-out goes to the chat page and clears the saved setup chat"
---

# Chunk 73 — sign-out goes back to the chat page

User report 2026-10-10: a newly registered (non-admin) user landed on `#/admin` ("Admins only")
instead of the chat page.

Cause (`frontend/src/App.tsx` `afterSignOut`): sign-out shows the sign-in page but keeps the URL
hash. The admin signed out on `#/admin`; the next account signed in on the same hash. The saved
setup chat (`localStorage` key `CURRENT_KEY`) also survives sign-out, so the next person on the
browser sees the previous person's setup chat.

Decisions: explicit sign-out clears the saved setup chat and navigates to the chat page. A
session that expires (401 via `onSignedOut`) keeps the page so the same person returns to it.
Non-goals: per-run chat keys (the runs are access-guarded), the "Admins only" page for typed links.

## Stage 73a (detailed spec)

Owned: `frontend/src/App.tsx`. Not alone: preserve others' edits.

1. `afterSignOut()`: before the existing two statements, add
   `clearSession(browserStore(), CURRENT_KEY);`, `setChatKey((key) => key + 1);` and
   `navigate({ name: 'home' });` (same three calls as the New chat button). Nothing else changes.

Checks (from `frontend/`): `npm test`, `npm run typecheck`, `npm run build`. Stop and report on
any failure.

## Acceptance receipt (2026-10-10)

Implementer: `npm test` 113/113, `npm run typecheck` exit 0, `npm run build` exit 0. Main
reviewed the diff: the three calls match the New chat button, placed before `setSignedOut(true)`.
`navigate` sets the hash while the sign-in page shows, so the next sign-in renders the chat page.
No browser rerun: same calls as the already-working New chat path.
