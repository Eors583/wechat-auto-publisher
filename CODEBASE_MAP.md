# WeChat Auto Publisher — AI development handoff and code map

Last verified against the repository on **2026-08-25**. This document describes
the current source tree; code is the final authority. When a symbol moves, update
this file in the same change.

## 1. Read this first

This is a Python 3.11+ application with four runtime surfaces sharing one
PostgreSQL database and the same service layer:

| Surface | Entry point | Default/local port | Purpose |
|---|---|---:|---|
| Operations web UI | `python -m app.ui.server` | 18765 (`compose.yaml`: host 18775) | NiceGUI operations workbench used by customers |
| HTTP API | `python -m app.api` / `wechat-auto-api` | 18766 (`compose.yaml`: host 18776) | Authenticated `/api/v1` endpoints and health check |
| Merchant admin UI | `python -m app.admin.server` | 18767 (`compose.yaml`: host 18777) | Users and platform-managed model administration |
| Desktop launcher | `python -m app.launcher` | starts/opens the UI and API | Native/remote desktop lifecycle and owned child process control |
| Windows local-model Companion | `python -m app.launcher --local-agent` | loopback 11798 + outbound HTTPS | DPAPI-backed Cockpit setup, device pairing and leased background model jobs |
| CLI | `python -m app ...` / `wechat-auto` | n/a | Direct run, retry, review, publish, list and desktop commands |

The primary business flow is:

```text
UI / API / Feishu
    -> app.services.batches.BatchService
    -> app.pipeline.Pipeline (small facade)
    -> app.workflows.generation.GenerationSteps
    -> app.workflows.rendering.RenderingStep
    -> app.workflows.delivery.DeliverySteps
    -> app.wechat + app.services.wechat_delivery
    -> PostgreSQL through app.db.Database
```

Do not bypass this flow by copying business decisions into a UI callback or an
API endpoint. The UI, API and Feishu surfaces must converge on shared services.

## 2. Repository tree at a glance

```text
wechat-auto-publisher/
├─ AGENTS.md                    # Agent rules; points to this map
├─ PROJECT_MEMORY.md            # Durable user/product collaboration preferences
├─ CODEBASE_MAP.md              # This maintained handoff document
├─ README.md                    # Operator/developer overview and normal usage
├─ pyproject.toml               # Package metadata, dependencies, console scripts
├─ config.example.yaml          # Non-secret functional configuration template
├─ .env.example                # Runtime secrets/environment template
├─ compose.yaml                 # Local PostgreSQL + API + web + admin stack
├─ compose.production.yaml      # Production stack and persistent NiceGUI storage
├─ Dockerfile                   # Shared image for API, web and admin
├─ app/                         # Product source
│  ├─ ui/                       # Operations workbench and browser interaction
│  ├─ services/                 # Shared application/domain services
│  ├─ workflows/                # Generation/rendering/delivery pipeline stages
│  ├─ api/                      # FastAPI surface
│  ├─ admin/                    # Merchant administration UI
│  ├─ ai/                       # Text/image provider clients and model registry
│  ├─ wechat/                   # Low-level WeChat API, draft and material adapters
│  ├─ providers/                # Article/topic/followed-account acquisition
│  ├─ feishu/                   # Feishu gateway, agent, tools and progress
│  ├─ render/                   # Final HTML rendering and preview sanitation
│  ├─ cover/                    # Cover generation and material fallback
│  ├─ layout/                   # Multi-article composition
│  ├─ db.py                     # Schema, migrations and all persistence methods
│  ├─ pipeline.py               # Small facade over app/workflows
│  └─ ...                       # Shared helpers described below
├─ tests/                       # Unit, contract, integration and UI source contracts
├─ deploy/production/           # Git release deployment and safe cleanup automation
├─ deploy/wechat-relay/         # Optional fixed-egress WeChat relay support
├─ scripts/                     # Local run, migration and packaging scripts
├─ packaging/                   # Windows installer/PyInstaller assets
└─ docs/                        # Style spec, feature mapping and visual evidence
```

`app/frontend/` currently contains only ignored stale `__pycache__` artifacts;
there is no maintained JS/Vue source tree there. The actual frontend is generated
by NiceGUI from `app/ui/`. Do not edit bytecode or build a second frontend under
`app/frontend/` unless the product explicitly approves an architecture change.

## 3. Non-negotiable architectural boundaries

### 3.1 User ownership and data isolation

- `app.db.Database` is the only persistence gateway.
- A customer-scoped handle is created with `Database.for_user(user_id)` or
  `Database.set_owner_user(user_id)`. API requests use `customer_data_scope`.
- `AppState.set_current_user` scopes its database handle to the logged-in user.
- Legacy `config.yaml` accounts are imported only through the explicit default
  administrator owner scope. Secondary administrators may manage platform
  settings, but cannot see, overwrite or re-claim that administrator's account.
- Customer data includes accounts, batches/jobs, prompts, plans, review data,
  followed content, topic data, per-user Feishu integrations and UI preferences.
- `ai_models.owner_user_id == ''` means a platform/official model; a non-empty
  owner is a user's custom model. Official models are visible to users but may
  only be managed from the merchant admin path.
- Never expose `api_key_encrypted`, `app_secret_encrypted`, cookies, tokens or
  passwords. `ConfigurationService._public` recursively removes sensitive keys.
- Credentials are encrypted through `app.ai.model_registry.encrypt_api_key` /
  `decrypt_api_key`; the stable `CREDENTIAL_ENCRYPTION_KEY` is deployment data,
  never source-controlled data.

Regression authority: `tests/test_customer_data_isolation.py`,
`tests/test_admin_owner_scope.py`,
`tests/test_auth_and_managed_models.py`, `tests/test_postgres_only_runtime.py`.

### 3.2 Business logic boundaries

- `BatchService` is the public application service for batch/job operations.
- `Pipeline` must remain a small facade; stages under `app/workflows/` must not
  import `Pipeline` back. Enforced by `tests/test_architecture.py`.
- API handlers validate/authenticate and delegate. They should not reimplement
  batch, review, delivery or configuration rules.
- UI panels render state, gather input, show loading/progress and delegate to
  services. Long work must use `run.io_bound`, background execution, or existing
  progress monitors; never block the NiceGUI event loop.
- Feishu operations must delegate to the same services. Any new public
  `BatchService` operation requires a capability decision in
  `app/feishu/capabilities.py` and, when supported, a catalog/tool handler.

### 3.3 Frontend and visual boundaries

- `app/ui/style_tokens.py` is the design-token source of truth.
- `app/ui/styles.py` is the shared CSS bundle. Business panels should use shared
  classes instead of inline colors, sizes, margins or one-off media queries.
- `app/ui/desktop.py` owns the full-screen shell, lazy primary panels, creation
  workbench and account configuration workbench.
- Feature-specific panels live in `app/ui/panels/`.
- Every frontend change must verify at least 1440×900, 1280×720 and 1024×768,
  including long Chinese text, long URLs/model names, empty/populated/loading/
  failed states, root scroll metrics and the intended internal scroll owner.
- Do not hide required controls to make a viewport fit. Use `min-width: 0`,
  `min-height: 0`, bounded flex/grid tracks and explicit scroll ownership.

Visual authority: `docs/ui-style-spec.md`, `docs/codex/pixel-audit/`,
`docs/codex/workbench-visual-acceptance/`, and the UI contract tests.

## 4. Application source — exact file responsibilities

### 4.1 Root application modules (`app/*.py`)

| File | Responsibility and important symbols |
|---|---|
| `app/__main__.py` | Package execution bridge to the Typer CLI. |
| `app/cli.py` | CLI commands `run`, `retry`, `list_jobs`, `review`, `publish`, `show`, `desktop`. Keep it a caller of shared services/pipeline. |
| `app/launcher.py` | Desktop startup, remote URL mode, owned API child process and native/browser fallback. Use this for application lifecycle changes, not UI navigation. |
| `app/local_agent.py` | Windows Companion lifecycle: single-instance lock, pairing, HTTPS long polling, lease renewal, result replay and fixed Cockpit 11797 calls. |
| `app/local_credentials.py` | Windows CurrentUser DPAPI-only storage for the Cockpit key, Agent token and pending result state. Never replace with the production credential encryption key. |
| `app/local_model_cors_bridge.py` | Loopback-only 11798 compatibility bridge and `/setup`; exact Origin/Host/routes, local key injection, sanitized model responses and browser-triggered local WeChat extraction. |
| `app/local_wechat_extractor.py` | Standard-library WeChat article URL validation, user-network HTML fetching, `js_content` extraction and verification/error-page rejection for the standalone bridge. |
| `app/config.py` | Loads YAML + environment substitutions; computes `_root`, `_data_dir`, `_db_target`; `database_target` enforces PostgreSQL runtime. |
| `app/db.py` | Versioned schema initialization/migrations, user scoping and every CRUD method. Add persistence here before writing service logic. Large hotspot: do not issue raw customer-table SQL elsewhere. |
| `app/db_backend.py` | PostgreSQL compatibility layer, schema SQL conversion, cursor/connection wrappers and integrity-error mapping. |
| `app/schema_migrations.py` | Ordered migration manifest, stable checksums and migration-history validation. |
| `app/db_audit.py` | Read-only aggregate integrity audit for orphan/owner/status/index/schema findings; never returns stored values or credentials. |
| `app/accounts.py` | Account persistence, legacy config import, public account projection, account→model resolution, account layout/prompt bindings and selection application. |
| `app/prompt_templates.py` | Article/image prompt template validation, usage protection, account-specific prompt resolution. There is no separate “disabled-word” domain here. |
| `app/editorial_review.py` | Normalizes AI review configuration and exposes selectable review options. Domain execution is in `services/editorial_reviews.py`. |
| `app/pipeline.py` | `Pipeline.create_and_run`, `run_job`, `review_and_inject`, `publish_job`; delegates to independent workflow stages. Keep under the architecture-test size limit. |
| `app/batch.py` | Low-level concurrent execution helpers `run_pipelines_concurrently` and `inject_pipelines_concurrently`. |
| `app/inline_images.py` | Plans, resolves, inserts, regenerates and removes inline article images; preserves metadata and revision prompts. |
| `app/layout_profiles.py` | Normalizes/validates account typography/layout structures and converts them to renderer/template config. |
| `app/benchmark.py` | Reads benchmark/publication records and aligns secondary article titles. |
| `app/notify.py` | Optional webhook notifier abstraction used by pipeline stages. |
| `app/runtime_control.py` | Safely tracks and restarts only the API process owned by the launcher; never kill arbitrary processes by port. |
| `app/time_utils.py` | Asia/Shanghai/business-time conversion, formatting and UTC query bounds. Use this instead of ad-hoc `datetime.now()` in user-visible timestamps. |
| `app/packaging_smoke.py` | Frozen/installer runtime self-test. |

### 4.2 Operations UI (`app/ui/`)

| File | Responsibility and important symbols |
|---|---|
| `app/ui/server.py` | Headless/web NiceGUI entry. Sets storage secret, persistent auth-cookie middleware and port 18765. |
| `app/ui/desktop.py` | Main shell and largest UI composition file. `create_desktop_app` builds auth gate, sidebar/topbar, lazy tabs, creation workbench and review route; `_build_accounts_panel` / `_render_account_config_workspace` own account configuration and the default-model selector; `_build_wizard` owns generation form/background submission. |
| `app/ui/state.py` | Per-page `AppState`: config, scoped DB/services, authenticated user, model selectors, UI preferences and refresh coordination. `set_button_loading`, `clean_titles`, `clean_subtitles` are shared UI helpers. |
| `app/ui/style_tokens.py` | Typed design tokens and CSS variables: color, type, spacing, radius, shadow, control heights, layout widths and breakpoints. Update tokens here before CSS literals. |
| `app/ui/styles.py` | Shared operations-workbench CSS and component-library overrides. Resolve old/new selector conflicts here; do not append endless business-specific overrides. |
| `app/ui/background_activity.py` | Global/right-side activity dock showing background batch/review/rewrite progress and details. |
| `app/ui/loading.py` | `RequestLoading` and `get_request_loading`: request feedback and optional “move to background” behavior. |
| `app/ui/interaction_feedback.py` | Installs lightweight immediate feedback on interactive controls so network work is not perceived as a frozen click. |
| `app/ui/lifecycle.py` | Client-owned timers that stop safely when the NiceGUI client is deleted. |
| `app/ui/navigation.py` | Builds reverse-proxy-aware full UI URLs and NiceGUI client-navigation targets. |
| `app/ui/preflight_repair.py` | Normalizes failed preflight reasons and renders durable repair dialogs with account-scoped configuration links. |
| `app/ui/auth_persistence.py` | Aligns signed NiceGUI cookie lifetime/security with 30-day database sessions. |
| `app/ui/local_model_bridge.py` | Transitional browser fallback for user-local models: Chromium loopback permission probe, one active tab per user and DB request relay. Companion-bound models do not depend on an open tab. |
| `app/ui/image_proxy.py` | Validated proxy route for WeChat image previews; prevents arbitrary URL proxying. |
| `app/ui/ip_whitelist_guide.py` | Detects WeChat IP-whitelist errors and opens the guided repair dialog. |
| `app/ui/workflow.py` | UI workflow-step normalization and next-review-job navigation helpers. |
| `app/ui/__main__.py` | `python -m app.ui` bridge. |

### 4.3 UI feature panels (`app/ui/panels/`)

| File | User-visible module |
|---|---|
| `auth.py` | Login/register/logout UI; stores only the opaque token in NiceGUI user storage and delegates to `AuthService`. |
| `topics.py` | “选题雷达”: topic list/table, pagination/filtering, followed accounts, source management and refresh actions. |
| `followed_articles.py` | Recent-article dialog for one followed account, pagination/load limits, cover proxy and actionable fetch errors. |
| `tasks.py` | “任务队列” plus full-page article review. Owns inbox rows, filters, retry/progress, title/body/image/history views, confirmation, AI review UI, background rewrite and draft-write confirmation. Business calls still go to `BatchService`. |
| `review_jury.py` | AI review progress calculation, risk/result panel, review-profile configuration and profile options. |
| `models.py` | Reusable custom model create/edit form, provider presets, local/API model choice and connection testing. It is embedded by the account model selector and settings surfaces. |
| `prompts.py` | Structured article/image prompt-template administration. |
| `settings_hub.py` | Merchant/user model-management composition and creation-plan panel composition. |
| `onboarding_wizard.py` | First-run configuration wizard, readiness checks and persistent health banner. |
| `overview.py` | Compact overview/metric cards used by older or secondary UI composition paths. |
| `feishu.py` | “我的飞书机器人”: per-user encrypted app credentials, dedicated Webhook, model/account scope, callback status, one-time p2p pairing, unbind and disable controls. |
| `wechat_relay.py` | Optional WeChat fixed-egress relay configuration and validation UI. |

Primary tab ownership inside `desktop.py`:

| Navigation | Builder |
|---|---|
| 创作台 | inline creation workbench in `create_desktop_app` / `_build_wizard` |
| 选题雷达 | `panels.topics.build_topic_center` |
| 任务队列 | `panels.tasks.build_tasks_panel` |
| 公众号 | `_build_accounts_panel` → `_render_account_config_workspace` |
| 模型配置 | `panels.models.build_models_panel` |
| 飞书机器人 | `panels.feishu.build_feishu_panel` |
| 文章审核 | hidden route/tab entered from a task row → `panels.tasks.build_review_page` |

### 4.4 Shared services (`app/services/`)

| File | Responsibility and public operations |
|---|---|
| `batches.py` | Central `BatchService`: create/list/get batches, review inbox, title/content selection, confirm/needs-changes, versions, paragraph/images/cover edits, AI review delegation, retry/cancel/archive, idempotent draft injection. Most product operations start here. |
| `batch_contracts.py` | Stable public projection and status semantics: `public_job`, `batch_progress`, `effective_review_status`, `effective_batch_status`. Change this when counts/status disagree across UI/API. |
| `batch_progress.py` | Polling monitor and change signatures for background progress without duplicate updates. |
| `job_attempts.py` | Stage leases, heartbeat, completion and retry backoff. Diagnose stuck/running jobs here with DB attempt records. |
| `editorial_reviews.py` | AI review/rewrite state machine, guards, snapshots, prompt/schema building, incomplete-output recovery, issue resolution, numeric/fact safeguards and source-vs-candidate application. |
| `article_revisions.py` | Paragraph-level AI revision with structural cleanup, inline-image preservation and version events. |
| `configuration.py` | Safe public CRUD facade for accounts, models, prompts, layouts and per-account benchmark-ad settings; recursively strips credentials. Prefer this over direct DB use from new management APIs. |
| `feishu_integrations.py` | Per-user Feishu integration security boundary: encrypted credentials, globally unique App ID, dedicated callback key, public-HTTPS callback resolution, account/model ownership checks, p2p identity binding, hashed expiring pairing codes and safe legacy migration. |
| `local_agents.py` | One-time device pairing, Agent Token authentication, device management, fixed `chat.completions` claims, 60-second leases and idempotent result submission. |
| `creation_plans.py` | Reusable plans combining prompts, layout, image/cover and review settings; account defaults and account-specific template bindings. |
| `followed_content.py` | Followed-account CRUD and article discovery. Chooses available acquisition paths (Jizhile, WeChat backend state, RSS/manual) and persists articles per user. |
| `topic_sources.py` | Source CRUD, default sources, refresh/search/pagination and manual topics; adapters for RSS, Bing/Google, 36Kr and hot APIs. |
| `jizhile_settings.py` | Per-user encrypted Jizhile configuration, public masked view, effective values and clear. |
| `wechat_backend_settings.py` | Per-user WeChat backend Token/Cookie state, masked public view and clear. |
| `wechat_layout_import.py` | Fetches/parses a public WeChat article's typography: font size/weight/color, spacing, indentation, headings and safe preview HTML. |
| `wechat_delivery.py` | Idempotent draft writing and reconciliation after uncertain network results; cached connection health; prevents duplicate drafts. |
| `wechat_relay_settings.py` | Fixed-egress relay credentials/access-code encoding, effective settings and connectivity validation. |
| `preflight.py` | Checks account/model/template/cover/WeChat connectivity before write or generation; provides repairable results. |
| `model_readiness.py` | Tracks model credential fingerprints and active authentication failures so bad models are not retried blindly. |
| `failures.py` | Sanitizes errors, classifies retry stage/action and produces safe public failure payloads. Never return raw provider exceptions directly. |
| `auth.py` | PBKDF2 password hashing, opaque 30-day sessions, default admin seeding, registration/login/logout/user status. |
| `onboarding.py` | Persisted onboarding state, model/account/Feishu steps, readiness and restart. |
| `onboarding_errors.py` | Converts provider/WeChat errors into guided onboarding actions. |
| `analytics.py` | Operational metrics and time-bounded summaries. |
| `billing.py` | Owner-scoped shadow metering, strict article-usage completeness, provider cost snapshots and Credit-safe summaries. |
| `url_validation.py` | SSRF/network-boundary validation for external URLs. Use before every new server-side fetch feature. |

### 4.5 Workflow stages (`app/workflows/`) and facade

| File | Responsibility |
|---|---|
| `context.py` | `WorkflowContext`: shared config, scoped DB, notifier, AI client, WeChat client factory and cancellation checks. |
| `generation.py` | Ingest source text/URLs, rewrite content and optimize structured title/subtitle candidates. |
| `rendering.py` | Account layout, prompt styles, template snapshot, inline images, cover selection/generation, final HTML and layout-quality metadata. |
| `delivery.py` | Applies review choices, requires safe article length/layout, composes secondary articles, writes an idempotent draft and optionally publishes. |
| `errors.py` | `JobCancelled` workflow control exception. |
| `app/pipeline.py` | Constructs the above stages and exposes the compatibility facade. |

Generation status changes should be persisted on `jobs`/`job_attempts`, projected
through `batch_contracts`, and observed by the UI/Feishu progress monitors. Do
not invent a UI-only status.

### 4.6 AI providers and model registry (`app/ai/`)

| File | Responsibility |
|---|---|
| `__init__.py` | Shared result types, structured output parsing, candidate cleanup, emphasis rules and quality checks. |
| `model_registry.py` | Official/config/custom model projection, API-key encryption, save/test/delete, selected-model application, text-client construction and image test generation. |
| `openai_compat.py` | OpenAI Chat Completions-compatible text client, overload retry behavior, structured rewrite/title parsing and junk-title filtering. |
| `local_browser.py` | Client shape for local models and normalized chat-completions URL; pairs with `ui/local_model_bridge.py`. |
| `manus.py` | Manus async task transport, polling/events, timeout/retry classification and structured rewrite schema. |
| `gemini.py` | Google Gemini text client adapter. |
| `askmany.py` | AskMany client adapter. |
| `failover.py` | Primary/fallback text model execution and title scoring. |
| `usage.py` | Provider-neutral usage events, strict Token status, provider-actual normalization, estimates and fixed/Credit units. |
| `image_providers.py` | Supported image-provider presets, type inference, labels and endpoint resolution. |
| `image_generator.py` | Provider-specific image generation, rate-limit retry, response normalization, image validation and endpoint test. |

For custom model UI changes, the usual path is:

```text
desktop.py account selector / panels.models form
  -> AppState refresh registry
  -> model_registry.save_model/test_model_connection/build_text_client
  -> Database.ai_models (official owner='' or current user owner=id)
```

### 4.7 Content acquisition (`app/providers/`)

| File | Responsibility |
|---|---|
| `ingest.py` | Converts pasted text, one URL or multiple URLs into normalized `IngestedContent`; uses Trafilatura then Readability fallback. |
| `public_wechat.py` | Normalizes public WeChat URLs and extracts public metadata from article HTML. |
| `wechat_backend_search.py` | Uses configured WeChat backend Token/Cookie to search an account's articles and test login state. |
| `jizhile_api.py` | Jizhile account/article requests, identity payloads, error normalization and connection test. |
| `article_search.py` | Generic Weixin/Sogou/Baidu search and result-link resolution; compatibility/fallback path, not the owner of followed-account settings. |
| `topics_catalog.py` | Legacy/config-driven keyword, peer and hot-topic catalog fetch/cache helpers. New persisted topic-center work belongs to `services/topic_sources.py`. |
| `topic.py` | Small `Topic` value type and manual/keyword constructors. |

### 4.8 WeChat integration (`app/wechat/`)

| File | Responsibility |
|---|---|
| `client.py` | Authenticated low-level HTTP client and `WeChatAPIError`. |
| `auth.py` | Access-token acquisition/cache and expiration. |
| `factory.py` | The only approved construction path for `WeChatAuth`/`WeChatClient`, including relay options. All call sites should use it. |
| `draft.py` | Add/update/list/get drafts and build an article payload. |
| `material.py` | Upload article/thumbnail/permanent images and list materials. |
| `publish.py` | Submit/get publish status, scheduled publication helpers and job→article conversion. Normal operations stop at drafts. |
| `template_snapshot.py` | Capture, validate, sanitize and merge a WeChat editor template snapshot for one account. |
| `errors.py` | Friendly classification of WeChat HTTP/API errors. |

Never call draft creation directly from a new feature. Route through
`BatchService.inject_batch` → workflow delivery → `deliver_draft_once` to retain
idempotency and reconciliation.

### 4.9 Rendering, cover and composition

| File | Responsibility |
|---|---|
| `app/render/renderer.py` | Jinja `TemplateRenderer` and digest generation. |
| `app/render/finalize.py` | Normalizes WeChat-safe HTML and returns quality metrics/errors/warnings. |
| `app/render/preview.py` | Produces safe standalone preview documents/HTML. |
| `app/render/templates/article.html.j2` | Base article template consumed by the renderer. |
| `app/cover/generator.py` | Builds article cover prompts, generates/uploads covers and invalidates stale generated cover metadata. |
| `app/cover/resolver.py` | Chooses explicit/generated/material-library cover with fallback. |
| `app/layout/composer.py` | Selects secondary draft articles, identifies ad titles and composes multi-article payloads. |
| `app/ads/scheduler.py` | Synchronizes configured ads, selects one and renders ad HTML. |

### 4.10 HTTP API (`app/api/`)

- `server.py` creates FastAPI, auth dependencies, request-scoped user context,
  structured error envelopes, health checks and all non-review routes.
- `editorial_reviews.py` is a separate router for profile/default/review/rewrite/
  application/issue endpoints.
- `__main__.py` starts the API command.

Endpoint map (handler names are searchable symbols):

| Domain | Routes |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/login`, `GET /auth/me`, `POST /auth/logout` |
| Merchant admin | `GET /admin/users`, `GET/POST /admin/models`, test/delete model |
| Customer config | `GET /accounts`, `GET/POST /models`, model test/delete, onboarding status, account preflight, WeChat health |
| Topics | source CRUD/refresh, topic list/search/manual under `/api/v1/topic-*` and `/api/v1/topics*` |
| Followed content | account CRUD/refresh and article list/add/state update under `/api/v1/followed-*` |
| Batches/jobs | create/list/get, review inbox, attempts/retry, selection/view/confirm/needs-changes/content/rerender, paragraph/images/cover/version operations, draft/cancel/retry/copy/archive |
| AI review | profiles/defaults, run/list/get review, generate candidate, list/get/apply/keep-source application, resolve issue |
| My Feishu bot | authenticated owner-scoped `GET/PUT /api/v1/me/feishu-integration`, credential test, pairing-code, unbind, enable and disable |
| Feishu events | unauthenticated callback-key lookup at `POST /api/feishu/events/{callback_key}`; the handler verifies/decrypts the event, binds the integration owner, then delegates to shared services |

Run the AST endpoint inventory when routes change:

```powershell
rg -n "@(app|router)\.(get|post|put|patch|delete)" app/api
```

### 4.11 Feishu (`app/feishu/`)

| File | Responsibility |
|---|---|
| `gateway.py` | Legacy/single-tenant long-connection transport plus SDK message sending; it is not the multi-user production ingress. |
| `webhook.py` | Production multi-user event ingress: resolves a random callback key, verifies signature/token/App ID, decrypts SDK events and constructs an owner-bound bot/service. |
| `events.py` | Parses raw Feishu events into `IncomingFeishuMessage`, preserving App ID and `chat_type`. |
| `bot.py` | Orchestrates strict p2p pairing/session/agent/tool execution/progress for one pre-bound integration owner; redacts known sensitive values. |
| `agent.py` | Uses the configured planning model to create `AgentPlan`; enforces explicit confirmations and secret redaction. |
| `session.py` | Integration/chat conversation and current batch/job context; legacy user/chat fallback remains for compatibility only. |
| `tool_catalog.py` | Tool schemas, descriptions and confirmation requirements shown to the agent. |
| `tool_executor.py` | Central validated dispatch; composes domain mixins. |
| `tool_modules/discovery.py` | Account/topic/article discovery tools. |
| `tool_modules/review.py` | Batch, article, title, body, image, cover and draft-review tools. |
| `tool_modules/editorial_review.py` | AI review profiles, review execution, rewrite/application and issue tools. |
| `tool_modules/admin.py` | Administrative configuration tools. |
| `tool_modules/system.py` | Runtime/system inspection tools. |
| `tool_modules/common.py` | Argument coercion, confirmation and batch/job helpers. |
| `capabilities.py` | Required support matrix for public `BatchService` operations. |
| `progress.py` | Deduplicated proactive job progress reporting. |
| `presenter.py` | Human-readable account/topic/status/article/review/draft messages. |
| `settings.py` | Compatibility projection over the per-user integration service. |
| `pairing.py` | Integration-scoped one-time pairing, with legacy compatibility helpers. |
| `runtime.py` | Integration-scoped persisted runtime state, with legacy compatibility helpers. |
| `media.py` | Safe WeChat-image download for Feishu delivery. |
| `legacy.py` | Backward-compatible fixed command handler. |

### 4.12 Merchant admin (`app/admin/`)

- `server.py`: independent NiceGUI login, overview, user enable/disable and
  official/platform model management. Platform model CRUD belongs here/API admin
  endpoints, not in the customer account selector.

## 5. Database map

`app/db.py` initializes/migrates the schema. Runtime is PostgreSQL-only; SQLite
is permitted solely when tests explicitly opt in. The database currently has 45
tables: 37 core business/runtime tables, 7 additive shadow-billing tables, and
`schema_migrations`. Applied versions
are checksum-validated at startup under the existing PostgreSQL advisory lock;
non-transactional index work uses an autocommit connection and the same lock.

| Table | Owner/scope | Purpose |
|---|---|---|
| `users`, `user_sessions` | platform | Login identities and hashed opaque sessions |
| `user_settings` | user | Cross-device UI/config preferences and private service settings |
| `app_settings` | platform/legacy | Platform settings and one-cycle legacy compatibility; customer writes go only to `user_settings`, and legacy customer reads require the explicit historical-owner marker |
| `ai_models` | platform when owner empty; otherwise user | Official and custom text/image/local model definitions |
| `local_model_requests` | user/device | Browser-fallback or Companion model jobs, attempt/nonce, lease, deadline and idempotent terminal result |
| `local_model_agents` | user | Paired Companion identity, Agent Token hash, heartbeat/Cockpit status and revocation |
| `local_agent_pairings` | temporary→user | Hashed one-time device/user codes, expiry, lockout, approval and single consumption |
| `official_accounts` | user | WeChat AppID/encrypted AppSecret, model, layout and canonical default creation/review bindings |
| `prompt_templates` | user | Article/image prompt templates |
| `creation_plans` | user | Combined writing/layout/image/review presets |
| `account_creation_plan_defaults` | user | One-release compatibility mirror of the canonical account default plan; dual-written, not yet dropped |
| `creation_plan_account_templates` | user | Account-specific template snapshots within plans |
| `jobs` | user | Article content, pipeline status, canonical batch/account snapshot/review state, titles, render/draft metadata and errors |
| `batches` | user | Multi-account batch/request/notification envelope |
| `batch_jobs` | user | One-release compatibility mirror of `jobs.batch_id/account_id/review_*`; dual-written, not yet dropped |
| `job_versions` | user | Restorable article snapshots |
| `job_attempts` | user | Stage attempt/heartbeat/retry/lease records |
| `draft_deliveries` | user | Idempotency and reconciliation records for draft writes |
| `wechat_connection_health` | user/account | Cached read/write capability and last health result |
| `editorial_review_profiles` | user | Structured AI review schemes |
| `account_editorial_review_defaults` | user | One-release compatibility mirror of canonical account review profile/config; dual-written, not yet dropped |
| `editorial_reviews` | user | Review state, result, progress and errors |
| `editorial_review_applications` | user | Rewrite candidates and source/candidate choice state |
| `topic_sources`, `topic_items` | user | Persisted source definitions and fetched topics |
| `followed_accounts`, `followed_articles` | user | Followed-public-account catalog and article states |
| `feishu_integrations` | user | One encrypted Feishu app per owner in v1; globally unique App ID and random callback key, exact p2p identity binding, pairing state and runtime |
| `feishu_integration_accounts` | user/integration/account | Allowed owned accounts and the integration's single default account |
| `feishu_sessions` | user/integration/chat | Conversation, batch/job and context isolated by `(integration_id, chat_id)` |
| `feishu_processed_events` | user/integration/event | Event deduplication isolated by `(integration_id, event_id)` |
| `bot_sessions`, `bot_contexts` | legacy Feishu | Compatibility-only Feishu session/context; new multi-user Webhook traffic must not use these tables |
| `processed_events` | shared compatibility | Generic cross-entry event idempotency still used by the WeChat command-agent webhook; it is not part of new Feishu Webhook storage and must not be retired until WeChat idempotency moves to an owner/account-scoped table |
| `schema_migrations` | platform/schema | Ordered version/name/checksum/application-time history; never stores customer data |
| `billing_plans`, `model_price_cards` | platform/billing | Shadow-mode plan and model-price snapshots; no online payment capture |
| `user_subscriptions`, `credit_buckets`, `credit_ledger` | user/billing | Subscription periods and append-only points grant/ledger data |
| `usage_operations`, `ai_usage_events` | user/billing | Idempotent operation envelope and one sanitized event per physical provider call; migration `20260825_0005` adds explicit `RECORDED/PENDING/UNAVAILABLE` Token status, provider request/response trace-id uniqueness guards, separate estimates, and provider Credits without treating missing usage as zero. |
| `ads` | user/config-compatible | Advertisement pool and use tracking |
| `token_cache` | ephemeral/account-compatible | WeChat access-token cache; excluded from legacy business migration |

When adding a column/table:

1. Add a new immutable migration entry in `app/schema_migrations.py` and its
   implementation in the versioned migration runner; never edit an already
   applied migration signature.
2. Add `owner_user_id` and owner index for customer data.
3. Apply `_owner_clause` / `_assert_write_owner` in every read/write method.
4. Use transaction-safe DDL by default. PostgreSQL `CONCURRENTLY` operations must
   run through the locked autocommit migration path.
5. Update PostgreSQL SQL compatibility in `db_backend.py` only if needed.
6. Add isolation and real-PostgreSQL migration tests; update `scripts/migrate_sqlite_to_postgres.py`
   if the table is business data.

## 6. Feature-to-code reverse index

Use this table before searching the whole repository.

| Requested change | Start here | Then inspect | Regression tests |
|---|---|---|---|
| Main shell/sidebar/topbar/navigation | `ui/desktop.py:create_desktop_app` | `ui/styles.py`, `ui/style_tokens.py`, `ui/state.py` | `test_ui_desktop_navigation.py`, `test_ui_lazy_panels.py`, `test_ui_performance_contract.py`, `test_ui_style_tokens.py` |
| Creation source form/background generation | `ui/desktop.py:_build_wizard` | `services/batches.py:create_batch`, `pipeline.py`, `background_activity.py`, `loading.py` | `test_workbench_content_source_ui.py`, `test_batch_progress.py`, `test_ui_operations_workbench_contract.py` |
| Task queue/count/status mismatch | `ui/panels/tasks.py:build_tasks_panel` | `services/batch_contracts.py`, `Database.review_inbox_counts/list_review_inbox_rows`, API review inbox | `test_ui_review_inbox.py`, `test_ui_operations_workbench_contract.py`, `test_batch_progress.py` |
| Full article review UI | `ui/panels/tasks.py:build_review_page` | `BatchService` selection/content/confirm/version/image/cover methods | `test_ui_review_inbox.py`, `test_ui_review_routing.py`, `test_ui_review_inplace.py` |
| AI review/background rewrite | `ui/panels/review_jury.py`, `ui/panels/tasks.py` | `services/editorial_reviews.py`, `services/batches.py`, `api/editorial_reviews.py`, DB review tables | `test_editorial_reviews.py`, `test_api_editorial_reviews.py`, `test_ui_review_jury_contract.py`, `test_batch_review.py` |
| Article before/after rewrite choice | `services/editorial_reviews.py` application methods | review page candidate UI, DB applications, API apply/keep-source | same AI review tests plus `test_ui_review_inbox.py` |
| Account list/switch/add/config | `ui/desktop.py:_build_accounts_panel` | `accounts.py`, `services/configuration.py`, `Database.official_accounts` | `test_accounts_panel.py`, `test_customer_data_isolation.py`, `test_optional_account_model.py` |
| Default model selector/custom edit-delete | `_render_account_config_workspace`, `ui/panels/models.py` | `ui/state.py`, `ai/model_registry.py`, `services/configuration.py`, DB `ai_models` | `test_models_panel.py`, `test_auth_and_managed_models.py`, `test_ui_model_selector_refresh.py`, UI operations contract |
| User's local model | `ui/panels/models.py` | `ui/local_model_bridge.py`, `local_model_cors_bridge.py`, `local_agent.py`, `services/local_agents.py`, `api/local_agents.py`, DB local jobs/devices | `test_local_models.py`, `test_local_model_cors_bridge.py`, `test_local_agents.py`, `test_api_local_agents.py`, `test_local_agent.py` |
| Prompt templates | `ui/panels/prompts.py` / account prompt button in `desktop.py` | `prompt_templates.py`, `services/configuration.py` | `test_prompt_templates.py`, `test_longform_prompts.py` |
| Creation plans | `ui/panels/settings_hub.py` | `services/creation_plans.py`, `accounts.py`, DB plan tables | `test_creation_plans.py`, `test_feishu_creation_plans.py` |
| Layout tokens/styles/overflow | `ui/style_tokens.py`, `ui/styles.py` | affected component DOM in browser | `test_ui_style_tokens.py`, `test_ui_review_design_system.py`, `test_ui_performance_contract.py` |
| Import layout from WeChat article | account layout UI in `desktop.py` | `services/wechat_layout_import.py`, `layout_profiles.py` | `test_wechat_layout_import.py`, `test_template_snapshot.py` |
| Topic radar/list/pagination | `ui/panels/topics.py` | `services/topic_sources.py`, DB topic tables, API topic routes | `test_topic_center.py`, `test_topics_catalog.py`, `test_topic_navigation.py` |
| Followed account recent articles | `ui/panels/topics.py`, `followed_articles.py` | `services/followed_content.py`, provider adapters, per-user settings | `test_article_search.py`, `test_wechat_backend_search.py`, `test_jizhile_api.py`, `test_topic_center.py` |
| Jizhile failures/fallback | `services/followed_content.py:discover_account` | `providers/jizhile_api.py`, `services/jizhile_settings.py`, backend-search provider/settings | `test_jizhile_api.py`, `test_wechat_backend_search.py` |
| Generate/rewrite content | `services/batches.py:create_batch` | `Pipeline`, `workflows/generation.py`, selected AI client | `test_batch.py`, `test_p0_backend.py`, provider/model tests |
| Strict Token/Credit metering | `ai/usage.py`, provider client | `services/billing.py`, `Database.ai_usage_events`, model capability probe, task/billing panels | `test_ai_usage.py`, `test_billing.py`, `test_manus.py`, `test_model_registry.py`, `test_ui_review_inbox.py` |
| Inline images | review UI and `BatchService.regenerate_inline_*` | `inline_images.py`, rendering stage, WeChat material | `test_inline_images.py`, `test_image_generator.py` |
| Cover generation/selection | review UI and `BatchService` cover methods | `cover/`, rendering stage, image model registry | `test_cover_generator.py`, `test_image_generator.py` |
| Write to draft safely | `tasks.py:confirm_batch_write` | `BatchService.inject_batch`, workflow delivery, `services/wechat_delivery.py`, `wechat/draft.py` | `test_wechat_delivery_reliability.py`, `test_wechat_publish.py`, `test_ui_ip_whitelist_guide.py` |
| Login persistence | `ui/panels/auth.py`, `ui/auth_persistence.py` | `services/auth.py`, DB sessions, production NiceGUI volume | `test_auth_persistence.py`, `test_auth_and_managed_models.py` |
| API behavior | `api/server.py` or `api/editorial_reviews.py` | relevant shared service and public contract | `test_api_*.py`, `test_failure_preflight_contract.py` |
| Feishu operation | `feishu/tool_catalog.py` | appropriate tool mixin, executor, capabilities, presenter/progress | `test_feishu_*.py`, especially capability alignment/security |
| Per-user Feishu bot/integration | `ui/panels/feishu.py`, `services/feishu_integrations.py` | `api/server.py`, `feishu/webhook.py`, `feishu/bot.py`, integration DB tables | `test_feishu_multitenant.py`, `test_ui_feishu_panel.py`, `test_api_runtime_health.py` |
| Production deployment/cleanup | `deploy/production/deploy-from-git.sh` | `compose.production.yaml`, cleanup script/systemd units | `test_production_*.py`, `test_packaging_smoke.py` |

## 7. End-to-end call chains

### 7.1 Background article generation

```text
desktop._build_wizard submit
  -> BatchService.create_batch(source_kind, source_value, account_ids, ...)
  -> Database.create_batch/create_job/attach_batch_job
  -> concurrent worker per account
  -> Pipeline.run_job
  -> GenerationSteps.ingest/rewrite/optimize_titles
  -> RenderingStep.render
  -> job status ready_for_review
  -> BatchProgressMonitor / global activity dock / task inbox
```

Required behavior: submission returns control to the UI; progress is persisted,
percentage/status is visible in both the global activity surface and task/review
detail; closing a dialog/page must not cancel the server job.

### 7.2 AI review and rewrite

```text
tasks.build_review_page / review_jury panel
  -> BatchService.run_editorial_review
  -> EditorialReviewService.run_review
  -> DB editorial_reviews progress/result/error
  -> generate_rewrite_candidate(selected issue ids)
  -> DB editorial_review_applications candidate_ready
  -> user explicitly chooses apply candidate OR keep source
  -> job returns to an unconfirmed review state
```

Fact/number safeguards are review warnings or candidate-application guards, not
generic transport errors. Preserve both source and candidate until the user
chooses; never silently overwrite the article after background rewrite.

### 7.3 Followed-account article acquisition

```text
topics followed-account action / followed_articles dialog
  -> FollowedContentService.discover_account
  -> effective per-user Jizhile settings, when valid
  -> effective per-user WeChat backend Token/Cookie search, when valid
  -> other supported/manual/RSS path
  -> normalize/deduplicate and DB.upsert_followed_article
  -> paginated list in the dialog/table
```

When acquisition fails, show a durable dialog with the actual sanitized reason
and a route to the relevant login/API settings. Do not use a disappearing toast
as the only error explanation.

### 7.4 Draft writing

```text
review/task confirm write
  -> BatchService.preflight + confirmed-state gate
  -> BatchService.inject_batch
  -> DeliverySteps.inject
  -> RenderingStep if final render is stale/missing
  -> deliver_draft_once
  -> claim draft_deliveries idempotency record
  -> WeChat draft API
  -> reconcile uncertain network result before any retry
  -> job status drafted and connection-health update
```

Never retry an uncertain draft creation by blindly calling `add_draft`; it can
create duplicates.

## 8. Test suite map

The full command is:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Run the smallest affected group first. The suite is organized as follows.

### Core, architecture and persistence

- `test_core.py`, `test_architecture.py`, `test_pipeline` behavior in related
  tests: core helpers and layer boundaries.
- `test_postgres_only_runtime.py`, `test_postgres_runtime_entrypoints.py`:
  PostgreSQL-only production behavior.
- `test_customer_data_isolation.py`: per-login ownership and request scoping.
- `test_auth_and_managed_models.py`, `test_auth_persistence.py`: authentication,
  official/custom models and persistent sessions.
- `test_admin_owner_scope.py`: default-owner legacy import, secondary-admin
  reload safety and account read/write/delete isolation.
- `test_configuration_service.py`, `test_accounts_panel.py`,
  `test_optional_account_model.py`: safe account/model/configuration behavior.
- `test_creation_plans.py`, `test_prompt_templates.py`,
  `test_longform_prompts.py`: plan and prompt persistence/validation.

### Generation, review, rendering and delivery

- `test_batch.py`, `test_batch_progress.py`, `test_batch_review.py`,
  `test_p0_backend.py`: batch lifecycle and review gates.
- `test_editorial_reviews.py`, `test_api_editorial_reviews.py` and
  `test_api_revisions.py`: AI review/rewrite, paragraph revision and version
  safety.
- `test_title_candidates.py`: title/subtitle structured-output cleanup.
- `test_inline_images.py`, `test_image_generator.py`,
  `test_cover_generator.py`, `test_image_proxy.py`: image and cover behavior.
- `test_preview_html.py`, `test_template_snapshot.py`,
  `test_wechat_layout_import.py`: final HTML, templates and imported typography.
- `test_wechat_publish.py`, `test_wechat_retry.py`,
  `test_wechat_delivery_reliability.py`, `test_wechat_errors.py`: WeChat
  transport, idempotency and friendly errors.
- `test_failure_preflight_contract.py`: safe error envelope and repair metadata.

### Topics and article acquisition

- `test_topic_center.py`, `test_topics_catalog.py`, `test_topic_navigation.py`:
  persisted sources, filtering/pagination and UI navigation.
- `test_article_search.py`, `test_wechat_backend_search.py`,
  `test_jizhile_api.py`: acquisition adapters and fallback behavior.

### UI contracts and performance

- `test_ui_operations_workbench_contract.py`: cross-page required-entry contract.
- `test_ui_desktop_navigation.py`, `test_ui_lazy_panels.py`,
  `test_ui_client_lifecycle.py`: tab switching, lazy render and client ownership.
- `test_ui_performance_contract.py`: CSS/DOM patterns known to cause click or
  navigation stalls.
- `test_ui_style_tokens.py`, `test_ui_review_design_system.py`: centralized
  tokens and design-system rules.
- `test_ui_review_inbox.py`, `test_ui_review_routing.py`,
  `test_ui_review_inplace.py`, `test_ui_review_jury_contract.py`: task→review
  path, review controls and AI review UI.
- `test_ui_model_selector_refresh.py`, `test_models_panel.py`,
  `test_local_models.py`: model selection, editing, local bridge and refresh.
- `test_ui_onboarding_wizard.py`, `test_onboarding*.py`: onboarding and repair.
- `test_ui_ip_whitelist_guide.py`, `test_ui_preflight_repair.py`: durable repair
  paths for WeChat/preflight failures.
- `test_ui_workflow.py`, `test_workbench_content_source_ui.py`,
  `test_ui_preferences.py`, `test_loading.py`, `test_settings_navigation.py`:
  workflow/form/preferences/loading/settings behavior.
- `test_ui_feishu_panel.py`, `test_ui_wechat_relay_panel.py`,
  `test_ui_wechat_backend_tutorial.py`: integration settings UI.

### API, Feishu, deployment and packaging

- `test_api_runtime_health.py`: API health/runtime data.
- `test_feishu_*.py`: gateway, events, agent, session, pairing, security,
  capabilities, creation plans, media and editorial-review tools.
- `test_config_feishu_env.py`, `test_config_wechat_relay_env.py`:
  environment parsing.
- `test_wechat_relay_settings.py`, `test_wechat_relay_transport.py`: relay
  configuration and routing.
- `test_production_nginx.py`, `test_production_cleanup.py`: production proxy and
  fail-closed artifact cleanup.
- `test_packaging_smoke.py`, `test_remote_desktop.py`, `test_runtime_control.py`:
  packaged desktop, remote mode and safe child-process control.
- `test_local_model_cors_bridge.py`, `test_local_agent.py`,
  `test_local_agents.py`, `test_api_local_agents.py`: loopback bridge security,
  DPAPI Companion lifecycle, pairing/auth domains, leases and multi-tenant jobs.
- `test_benchmark.py`, `test_analytics_service.py`, `test_second_phase.py`:
  specialized product behavior and historical regression coverage.

`tests/conftest.py` enables isolated SQLite only for tests and provides common
fixtures. Do not copy its SQLite opt-in into runtime code.

## 9. Configuration and secrets

| File/source | Use |
|---|---|
| `.env` from `.env.example` | Secrets and deployment/runtime toggles: PostgreSQL, auth storage, credential encryption, provider keys, Feishu and relay. Never commit. |
| `config.yaml` from `config.example.yaml` | Non-secret/legacy functional defaults: WeChat, AI fallback, prompts, layout, images, cover, topics, benchmark, notification. Runtime user/account settings increasingly override it. |
| PostgreSQL `user_settings` | Per-login settings such as UI preferences, Jizhile/backend state and onboarding. |
| PostgreSQL business tables | Accounts, models, prompts, plans, tasks, reviews, topics, followed content and per-user Feishu integrations. |
| production shared `.env.production` | Stable production DB/auth/encryption secrets; deployment script reuses it across immutable releases. |

Important environment behavior:

- `DATABASE_URL` is mandatory outside isolated tests.
- `AUTH_STORAGE_SECRET` signs NiceGUI storage cookies; changing it logs browsers
  out even if DB sessions remain valid.
- `AUTH_SESSION_COOKIE_SECURE=true` only after HTTPS is actually enabled.
- `CREDENTIAL_ENCRYPTION_KEY` must remain stable or encrypted credentials become
  unreadable.
- `NICEGUI_STORAGE_PATH` in production persists web/admin login storage across
  container replacement.
- A user's local-model URL must be called from the browser bridge; the production
  server's `localhost` is the server/container, not the user's computer.

## 10. Local development and verification

Start PostgreSQL and the stack:

```powershell
docker compose up -d postgres
.\scripts\run_dev_server.ps1
```

Or run the launcher, which owns its API child process:

```powershell
.\scripts\run_app.ps1
```

Useful checks:

```powershell
git status --short --branch
rg -n "symbol_or_text" app tests
.\.venv\Scripts\python.exe -m pytest tests\test_relevant_file.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

Browser acceptance for UI work:

1. Test the actual interaction, not only direct service calls.
2. Verify 1440×900, 1280×720 and 1024×768.
3. Confirm root `scrollWidth <= clientWidth` and `scrollHeight <= clientHeight`
   for the fixed workbench.
4. Confirm any internal overflow belongs to the intended list/editor region.
5. Exercise empty, many-item, long-text, loading, failed, running and completed
   states; inspect accessible names for icon-only buttons.

## 11. Production deployment and cleanup

Production definitions:

- `compose.production.yaml`: API/web/admin/PostgreSQL services and volumes.
- `deploy/production/deploy-from-git.sh`: fetch branch, create immutable release,
  build, start, health-check and update `current`.
- `deploy/production/cleanup-deploy-artifacts.sh`: threshold-triggered,
  fail-closed cleanup that preserves current/recent releases, in-use images and
  all persistent volumes.
- `wechat-publisher-cleanup.service` / `.timer`: hourly cleanup scheduling.
- `nginx.conf.example`: reverse-proxy example.

Do not deploy merely because code was changed. Deployment requires explicit
user authorization. When authorized:

1. Ensure focused and full tests pass.
2. Confirm `git diff --check` and that only intended files are committed.
3. Push the intended branch.
4. Run the installed server deployment script with `DEPLOY_BRANCH=...`.
5. Independently verify `current-release.txt`, container health, web/API/admin
   HTTP status and error-log **counts**. Do not print full logs that may contain
   request credentials.

### 11.1 Production `/publisher` routing fix completed on 2026-08-25

Production now runs `main@a3b0c96dcecf021969707594f9e970b65e7d95bc`
from `/opt/wechat-publisher/releases/git-a3b0c96dcecf`. This release fixes two
independent client-navigation loops without changing Nginx, authentication,
customer data or the public URL contract:

1. `ui_root_url(...)` still returns the complete reverse-proxy-aware URL used by
   links and persisted/background activity entries, for example
   `/publisher/?view=review`. NiceGUI's `ui.navigate.to(...)` adds its configured
   root prefix on the client, so direct navigation must first pass the complete
   URL through `ui_navigation_target(...)`. The helper removes exactly one
   configured `/publisher` prefix; this prevents
   `/publisher/publisher/?view=...` while preserving local-root behavior and
   external URLs.
2. The post-startup configuration-health refresh may automatically open the
   WeChat repair onboarding only when the current user is an administrator and
   no explicit `view` was requested. `_should_auto_open_onboarding(...)` returns
   false for `view=onboarding`, configuration/deep-link views and non-admin
   users, preventing the onboarding page from navigating back to itself.

Relevant ownership and regression points:

- `app/ui/navigation.py`: `ui_root_url`, `ui_navigation_target`.
- `app/ui/desktop.py`: `_should_auto_open_onboarding` and the asynchronous
  configuration-health redirect.
- `app/ui/preflight_repair.py`, `app/ui/panels/onboarding_wizard.py` and
  `app/ui/panels/tasks.py`: internal `ui.navigate.to(...)` callers use the
  NiceGUI navigation boundary; ordinary `ui.link(...)` callers keep full URLs.
- `tests/test_ui_desktop_navigation.py`: production-prefix composition,
  no-duplicate-prefix contracts and admin/root-only onboarding redirect cases.

Final verification recorded for the release:

- focused navigation/onboarding checks passed;
- full suite: `1168 passed, 10 skipped`;
- production API, web and onboarding endpoints returned HTTP 200;
- the onboarding endpoint returned zero HTTP redirects;
- API, web, HTTPS web and admin containers reported zero recent
  `ERROR`/`CRITICAL`/`Traceback` log matches;
- a 12-second post-release Nginx observation window recorded zero onboarding
  reload requests and zero `/publisher/publisher/` requests.

The server-to-GitHub HTTPS fetch was unstable during this release. The exact
already-pushed `origin/main` commit was transferred as a Git bundle into the
server's existing bare mirror, then the normal immutable release script ran with
`SKIP_GIT_FETCH=true`. This is a transport fallback only; the production commit
and `origin/main` commit are identical.

## 12. Safe change checklist for the next AI

Before editing:

- Read this file and `git status`.
- Locate the visible entry, shared service, DB method, API/Feishu sibling path
  and closest tests.
- State whether the request is diagnosis-only, local implementation, Git
  publication or production deployment.

During implementation:

- Preserve unrelated dirty-worktree changes.
- Reuse existing service/domain helpers; avoid a new abstraction for one caller.
- Keep customer ownership, credential masking, idempotency and cancellation.
- For async work, persist status/progress and keep the UI usable.
- For UI work, use shared tokens/classes and test real generated DOM overflow.

Before handoff:

- Run focused tests and full pytest.
- Report exact files and symbols changed.
- Report browser viewports/states for frontend work.
- Report Git commit/push/deployment separately; never imply one from another.
- Update this map if a module's responsibility, entry point, route, table or
  deployment path changed.

## 13. Hotspots and search shortcuts

These files are intentionally large and should be entered through symbols, not
read blindly from top to bottom:

- `app/ui/desktop.py`: search `create_desktop_app`, `_build_wizard`,
  `_build_accounts_panel`, `_render_account_config_workspace`.
- `app/ui/panels/tasks.py`: search `build_tasks_panel`, `build_review_page`,
  `_render_inbox_article_card`, `confirm_batch_write`.
- `app/services/batches.py`: search the public `BatchService` method matching the
  operation.
- `app/services/editorial_reviews.py`: search the `EditorialReviewService`
  method or prompt/schema/normalizer named in the failure.
- `app/db.py`: search the table name or public CRUD method; keep owner checks.
- `app/api/server.py`: search route text or handler name.

Fast examples:

```powershell
rg -n "def _render_account_config_workspace|默认模型" app/ui/desktop.py
rg -n "def build_review_page|def confirm_batch_write" app/ui/panels/tasks.py
rg -n "class BatchService|def run_editorial_review|def inject_batch" app/services/batches.py
rg -n "CREATE TABLE IF NOT EXISTS editorial_reviews|def update_editorial_review" app/db.py
rg -n "editorial-reviews|review-inbox" app/api tests
```

## 14. Exhaustive source-path index

This section is deliberately redundant with the responsibility tables above.
It gives coding agents exact repository paths so similarly named files are not
confused. Package `__init__.py` files only define exports/package boundaries
unless noted.

### Package/root

| Exact path | Role |
|---|---|
| `app/__init__.py` | Package metadata/boundary. |
| `app/__main__.py` | `python -m app` CLI bridge. |
| `app/accounts.py` | Customer WeChat accounts and account-level bindings. |
| `app/batch.py` | Concurrent batch execution helpers. |
| `app/benchmark.py` | Benchmark publication/secondary-title synchronization. |
| `app/cli.py` | Typer CLI commands. |
| `app/config.py` | YAML/environment loading and runtime paths. |
| `app/db.py` | Schema, migrations, scoped persistence. |
| `app/db_backend.py` | PostgreSQL adapter/SQL compatibility. |
| `app/schema_migrations.py` | Versioned schema manifest/checksums. |
| `app/db_audit.py` | Credential-safe aggregate schema/data audit. |
| `app/editorial_review.py` | Review configuration normalization/options. |
| `app/inline_images.py` | Inline-image lifecycle. |
| `app/launcher.py` | Desktop/remote launcher and owned API process. |
| `app/layout_profiles.py` | Structured layout normalization/validation. |
| `app/notify.py` | Optional notifications. |
| `app/packaging_smoke.py` | Packaged runtime self-test. |
| `app/pipeline.py` | Workflow facade. |
| `app/prompt_templates.py` | Article/image prompt templates. |
| `app/runtime_control.py` | Safe launcher-owned process restart. |
| `app/time_utils.py` | Business timezone helpers. |

### Admin, ads, layout, cover and render

| Exact path | Role |
|---|---|
| `app/admin/__init__.py` | Admin package boundary. |
| `app/admin/server.py` | Merchant admin NiceGUI app. |
| `app/ads/__init__.py` | Ads package exports. |
| `app/ads/scheduler.py` | Ad synchronization, selection and HTML. |
| `app/layout/__init__.py` | Layout composition exports. |
| `app/layout/composer.py` | Primary/secondary article composition. |
| `app/cover/__init__.py` | Cover package exports. |
| `app/cover/generator.py` | AI cover generation/upload/cache metadata. |
| `app/cover/resolver.py` | Explicit/generated/material cover resolution. |
| `app/render/__init__.py` | Render package exports. |
| `app/render/renderer.py` | Jinja article renderer and digest. |
| `app/render/finalize.py` | WeChat HTML normalization and quality report. |
| `app/render/preview.py` | Safe preview HTML/document. |
| `app/render/templates/article.html.j2` | Base article HTML template (non-Python asset). |

### AI

| Exact path | Role |
|---|---|
| `app/ai/__init__.py` | Shared AI result types, output parsing and quality rules. |
| `app/ai/askmany.py` | AskMany client. |
| `app/ai/failover.py` | Text-model fallback and title scoring. |
| `app/ai/gemini.py` | Gemini client. |
| `app/ai/image_generator.py` | Multi-provider image generation/test. |
| `app/ai/image_providers.py` | Image-provider presets and endpoint inference. |
| `app/ai/local_browser.py` | User-local model client shape/URL. |
| `app/ai/manus.py` | Manus async task client/polling. |
| `app/ai/model_registry.py` | Model storage projection, encryption, selection and client construction. |
| `app/ai/openai_compat.py` | OpenAI-compatible text client. |
| `app/ai/usage.py` | Strict provider usage normalization and recorder context. |

### API

| Exact path | Role |
|---|---|
| `app/api/__init__.py` | API package boundary. |
| `app/api/__main__.py` | `python -m app.api` bridge. |
| `app/api/server.py` | FastAPI factory, auth, core routes and health. |
| `app/api/editorial_reviews.py` | AI review/profile/application router. |

### Providers

| Exact path | Role |
|---|---|
| `app/providers/__init__.py` | Provider package exports. |
| `app/providers/article_search.py` | Generic Weixin/Sogou/Baidu compatibility search. |
| `app/providers/ingest.py` | Text/URL/multi-URL ingestion. |
| `app/providers/jizhile_api.py` | Jizhile article API. |
| `app/providers/public_wechat.py` | Public WeChat article metadata. |
| `app/providers/topic.py` | Topic value object. |
| `app/providers/topics_catalog.py` | Legacy/config topic catalogs. |
| `app/providers/wechat_backend_search.py` | WeChat backend Token/Cookie search. |

### Services

| Exact path | Role |
|---|---|
| `app/services/__init__.py` | Lazy shared-service exports. |
| `app/services/analytics.py` | Operational metrics. |
| `app/services/article_revisions.py` | Paragraph revision and version events. |
| `app/services/auth.py` | Users, passwords and sessions. |
| `app/services/batch_contracts.py` | Public batch/job projection and statuses. |
| `app/services/batch_progress.py` | Progress monitor/signature. |
| `app/services/batches.py` | Central batch/job application service. |
| `app/services/configuration.py` | Credential-safe account/model/prompt CRUD facade. |
| `app/services/creation_plans.py` | Reusable creation plans. |
| `app/services/editorial_reviews.py` | AI review/rewrite domain/state machine. |
| `app/services/feishu_integrations.py` | Per-user Feishu app, ownership, credentials, pairing and callback boundary. |
| `app/services/failures.py` | Sanitized/classified failures. |
| `app/services/followed_content.py` | Followed accounts and recent articles. |
| `app/services/image_prompts.py` | Article-level visual brief and argument-level image prompt agent orchestration. |
| `app/services/jizhile_settings.py` | Per-user Jizhile settings. |
| `app/services/job_attempts.py` | Attempt heartbeat/lease/backoff. |
| `app/services/model_readiness.py` | Model auth-failure readiness state. |
| `app/services/onboarding.py` | Onboarding workflow/readiness. |
| `app/services/onboarding_errors.py` | Guided onboarding error mapping. |
| `app/services/preflight.py` | Account/model/template/WeChat checks. |
| `app/services/topic_sources.py` | Persisted topic source/search/pagination. |
| `app/services/url_validation.py` | External URL/SSRF guard. |
| `app/services/wechat_backend_settings.py` | Per-user backend Token/Cookie settings. |
| `app/services/billing.py` | Strict usage completeness and shadow pricing. |
| `app/services/wechat_delivery.py` | Idempotent/reconciled draft delivery. |
| `app/services/wechat_layout_import.py` | WeChat article typography parser. |
| `app/services/wechat_relay_settings.py` | Fixed-egress relay settings/access code. |

### Operations UI

| Exact path | Role |
|---|---|
| `app/ui/__init__.py` | UI package boundary. |
| `app/ui/__main__.py` | `python -m app.ui` bridge. |
| `app/ui/server.py` | Web NiceGUI runtime. |
| `app/ui/desktop.py` | Main shell, creation and account configuration. |
| `app/ui/state.py` | Per-client/user application state. |
| `app/ui/style_tokens.py` | Design tokens. |
| `app/ui/styles.py` | Shared CSS. |
| `app/ui/auth_persistence.py` | Cookie/session lifetime settings. |
| `app/ui/background_activity.py` | Background task/review activity dock. |
| `app/ui/image_proxy.py` | Safe WeChat image proxy. |
| `app/ui/interaction_feedback.py` | Immediate click/request feedback. |
| `app/ui/ip_whitelist_guide.py` | WeChat whitelist repair dialog. |
| `app/ui/lifecycle.py` | Client-safe timers. |
| `app/ui/loading.py` | Request loading/background dialog. |
| `app/ui/navigation.py` | Reverse-proxy-aware full UI URLs and NiceGUI client-navigation targets. |
| `app/ui/preflight_repair.py` | Preflight failure reasons, repair labels/routes and durable actionable dialogs. |
| `app/ui/local_model_bridge.py` | Browser-to-user-local-model bridge. |
| `app/ui/workflow.py` | UI stage/navigation helpers. |
| `app/ui/panels/__init__.py` | Panel package boundary. |
| `app/ui/panels/auth.py` | Login/register UI. |
| `app/ui/panels/billing.py` | Customer usage and merchant AI cost/Credit panels. |
| `app/ui/panels/feishu.py` | Feishu settings UI. |
| `app/ui/panels/followed_articles.py` | Followed-account article dialog. |
| `app/ui/panels/models.py` | Custom/local/API model editor. |
| `app/ui/panels/onboarding_wizard.py` | First-run wizard/readiness banner. |
| `app/ui/panels/overview.py` | Compact overview cards. |
| `app/ui/panels/prompts.py` | Prompt-template UI. |
| `app/ui/panels/review_jury.py` | AI review result/progress/profile UI. |
| `app/ui/panels/settings_hub.py` | Model/creation-plan settings composition. |
| `app/ui/panels/tasks.py` | Task queue and full article review. |
| `app/ui/panels/topics.py` | Topic radar/followed accounts/sources. |
| `app/ui/panels/wechat_relay.py` | Relay settings UI. |

### WeChat and workflows

| Exact path | Role |
|---|---|
| `app/wechat/__init__.py` | WeChat adapter exports. |
| `app/wechat/auth.py` | Access-token lifecycle. |
| `app/wechat/client.py` | Low-level authenticated HTTP client. |
| `app/wechat/draft.py` | Draft payload/read/write functions. |
| `app/wechat/errors.py` | Friendly WeChat errors. |
| `app/wechat/factory.py` | Approved auth/client factory and relay wiring. |
| `app/wechat/material.py` | Image/material upload and listing. |
| `app/wechat/publish.py` | Publish API and job→article conversion. |
| `app/wechat/template_snapshot.py` | Editor-template capture/merge/sanitize. |
| `app/workflows/__init__.py` | Workflow stage exports. |
| `app/workflows/context.py` | Shared scoped dependencies/cancellation. |
| `app/workflows/generation.py` | Ingest/rewrite/title stage. |
| `app/workflows/rendering.py` | Layout/image/cover/final-HTML stage. |
| `app/workflows/delivery.py` | Idempotent draft/publish stage. |
| `app/workflows/errors.py` | Workflow cancellation exception. |

### Feishu

| Exact path | Role |
|---|---|
| `app/feishu/__init__.py` | Feishu package boundary. |
| `app/feishu/constants.py` | Shared Feishu constants. |
| `app/feishu/gateway.py` | SDK/long-connection transport. |
| `app/feishu/webhook.py` | Multi-user verified Webhook ingress. |
| `app/feishu/events.py` | Incoming event parser. |
| `app/feishu/bot.py` | Integration orchestrator/redaction. |
| `app/feishu/agent.py` | Tool-planning model and confirmation guard. |
| `app/feishu/session.py` | Conversation/batch/job state. |
| `app/feishu/settings.py` | Persisted/effective settings. |
| `app/feishu/pairing.py` | One-time pairing. |
| `app/feishu/runtime.py` | Persisted runtime state. |
| `app/feishu/progress.py` | Proactive deduplicated progress. |
| `app/feishu/presenter.py` | User-facing result formatting. |
| `app/feishu/media.py` | WeChat image download. |
| `app/feishu/legacy.py` | Fixed-command compatibility. |
| `app/feishu/capabilities.py` | BatchService support matrix. |
| `app/feishu/tool_catalog.py` | Tool schemas/confirmation metadata. |
| `app/feishu/tool_executor.py` | Validated tool dispatch. |
| `app/feishu/tool_modules/__init__.py` | Tool-mixin package boundary. |
| `app/feishu/tool_modules/common.py` | Argument/confirmation helpers. |
| `app/feishu/tool_modules/discovery.py` | Discovery tools. |
| `app/feishu/tool_modules/review.py` | Batch/article review/delivery tools. |
| `app/feishu/tool_modules/editorial_review.py` | AI review/rewrite tools. |
| `app/feishu/tool_modules/admin.py` | Administrative configuration tools. |
| `app/feishu/tool_modules/system.py` | Runtime/system tools. |

## 15. Exhaustive test-path index

These are all current `tests/test_*.py` files, grouped by the code they protect.
Use filename search plus the feature reverse index rather than running the full
suite after every small edit.

- Accounts/config/auth/models: `tests/test_accounts_panel.py`,
  `tests/test_auth_and_managed_models.py`, `tests/test_auth_persistence.py`,
  `tests/test_configuration_service.py`, `tests/test_creation_plans.py`,
  `tests/test_customer_data_isolation.py`, `tests/test_local_models.py`,
  `tests/test_model_registry.py`, `tests/test_models_panel.py`,
  `tests/test_optional_account_model.py`, `tests/test_prompt_templates.py`,
  `tests/test_longform_prompts.py`.
- Core/persistence/runtime: `tests/test_architecture.py`, `tests/test_core.py`,
  `tests/test_postgres_only_runtime.py`,
  `tests/test_postgres_runtime_entrypoints.py`,
  `tests/test_runtime_control.py`, `tests/test_remote_desktop.py`,
  `tests/test_packaging_smoke.py`.
- Batch/review/revision/progress: `tests/test_batch.py`,
  `tests/test_batch_progress.py`, `tests/test_batch_review.py`,
  `tests/test_editorial_reviews.py`, `tests/test_api_editorial_reviews.py`,
  `tests/test_api_revisions.py`, `tests/test_failure_preflight_contract.py`,
  `tests/test_p0_backend.py`, `tests/test_review_links.py`,
  `tests/test_title_candidates.py`.
- Rendering/images/WeChat: `tests/test_cover_generator.py`,
  `tests/test_image_generator.py`, `tests/test_image_proxy.py`,
  `tests/test_inline_images.py`, `tests/test_preview_html.py`,
  `tests/test_template_snapshot.py`, `tests/test_wechat_delivery_reliability.py`,
  `tests/test_wechat_errors.py`, `tests/test_wechat_layout_import.py`,
  `tests/test_wechat_publish.py`, `tests/test_wechat_retry.py`.
- Topics/search/followed content: `tests/test_article_search.py`,
  `tests/test_jizhile_api.py`, `tests/test_topic_center.py`,
  `tests/test_topic_navigation.py`, `tests/test_topics_catalog.py`,
  `tests/test_wechat_backend_search.py`.
- Onboarding: `tests/test_onboarding.py`, `tests/test_onboarding_errors.py`,
  `tests/test_onboarding_wizard_service.py`,
  `tests/test_ui_onboarding_wizard.py`, `tests/test_ui_preflight_repair.py`.
- Operations UI: `tests/test_loading.py`, `tests/test_settings_navigation.py`,
  `tests/test_ui_client_lifecycle.py`, `tests/test_ui_desktop_navigation.py`,
  `tests/test_ui_ip_whitelist_guide.py`, `tests/test_ui_lazy_panels.py`,
  `tests/test_ui_model_selector_refresh.py`,
  `tests/test_ui_operations_workbench_contract.py`,
  `tests/test_ui_performance_contract.py`, `tests/test_ui_preferences.py`,
  `tests/test_ui_review_design_system.py`, `tests/test_ui_review_inbox.py`,
  `tests/test_ui_review_inplace.py`, `tests/test_ui_review_jury_contract.py`,
  `tests/test_ui_review_routing.py`, `tests/test_ui_style_tokens.py`,
  `tests/test_ui_wechat_backend_tutorial.py`,
  `tests/test_ui_wechat_relay_panel.py`, `tests/test_ui_workflow.py`,
  `tests/test_workbench_content_source_ui.py`.
- Feishu: `tests/test_feishu_agent.py`, `tests/test_feishu_bot.py`,
  `tests/test_feishu_capability_alignment.py`,
  `tests/test_feishu_creation_plans.py`,
  `tests/test_feishu_editorial_review_tools.py`,
  `tests/test_feishu_extended_tools.py`,
  `tests/test_feishu_media_gateway.py`, `tests/test_feishu_pairing.py`,
  `tests/test_feishu_security.py`, `tests/test_feishu_settings.py`,
  `tests/test_feishu_multitenant.py`, `tests/test_ui_feishu_panel.py`.
- API/integration/config: `tests/test_api_runtime_health.py`,
  `tests/test_config_feishu_env.py`, `tests/test_config_wechat_relay_env.py`,
  `tests/test_wechat_relay_settings.py`,
  `tests/test_wechat_relay_transport.py`.
- Analytics/benchmark/product phases: `tests/test_analytics_service.py`,
  `tests/test_benchmark.py`, `tests/test_second_phase.py`.
- Production: `tests/test_production_cleanup.py`,
  `tests/test_production_nginx.py`.
- Provider-specific: `tests/test_manus.py`.

Shared test infrastructure is `tests/conftest.py`; `tests/__init__.py` is only a
package marker.
