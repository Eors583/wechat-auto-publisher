# Repository instructions for coding agents

Before changing code, read these project-level sources in order:

1. [`PROJECT_MEMORY.md`](PROJECT_MEMORY.md) — how the user expects product work,
   communication, verification and release decisions to be handled.
2. [`CODEBASE_MAP.md`](CODEBASE_MAP.md) — entry points, module ownership, feature
   call chains, database scope, tests and deployment boundaries.

The latest explicit user request always overrides project memory. Memory gives
context and defaults; it is never independent authorization for deployment,
data mutation or a materially different scope.

Mandatory working rules:

0. Use `main` as the only development, integration, push, and production-deploy
   branch. Do not create or continue work on `codex/*`, feature, backup, or
   deployment branches. Before editing, switch to `main` and fast-forward it
   from `origin/main`; commit and push completed work directly to `main`.

1. Inspect `git status --short --branch` before editing. Existing changes belong
   to the user or another task; do not overwrite or include them accidentally.
2. Trace the affected UI/API entry through `app/services/` before changing
   business behavior. UI panels should orchestrate and render, not duplicate
   domain rules.
3. All customer-owned reads and writes must use the authenticated user scope
   (`Database.for_user(...)`, `Database.set_owner_user(...)`, or
   `customer_data_scope(...)`). Never query customer tables through an
   unscoped database handle.
4. Frontend visual values belong in `app/ui/style_tokens.py` and
   `app/ui/styles.py`. Verify responsive behavior and overflow in a real browser.
5. Do not change production API contracts casually. Update the relevant API,
   service, UI, Feishu capability/tool mapping, and tests together when a shared
   operation changes.
6. Run focused tests first, then `python -m pytest -q`. Commit only explicit
   task files. After every completed change request, commit and push the intended
   files to `main`, deploy that exact `main` commit to production, and independently
   verify the release, container health, public HTTP endpoints, and recent error-log
   counts. Skip deployment only when the latest user request explicitly says not to
   deploy, asks for local/design acceptance first, or otherwise sets a pre-production
   stopping boundary.
