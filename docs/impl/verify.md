# VERIFY workstream — what was executed

All results below were produced by running the commands; nothing is inferred.

## 1. Playwright (real Chromium)
- `playwright` is a server dependency; `uv run playwright install chromium`. Browser tools, `checkui_run` and version
  covers run **in the api/worker process**, not in sandboxes (Chromium would add ~400 MB to `openatoms/sandbox`);
  `deploy/docker/server.Dockerfile` runs `playwright install --with-deps chromium`.
- `checkui_run` now records page errors, console errors, failed requests and same-origin HTTP ≥400, and ignores Vite
  dev plumbing (HMR websocket, `text/x-vite-ping`, duplicate "Failed to load resource" console lines).
  **Bug found by the real browser:** previously every preview check reported HMR socket failures as app errors.
- Version covers: built `dist/` is served on an ephemeral local HTTP server, screenshotted (1280×800) and stored at
  `covers/{chat}/{v}.png`; `Version.cover` = `/api/v1/covers/{chat}/{v}.png?sig=<hmac>` (works in `<img>`; bad sig → 404).
- `tests/test_verify_playwright.py` (real LocalProvider + pnpm + Vite + Chromium):
  - prompt "… (runtime error drill)": Alex ships a component that throws in `useEffect`; first `checkui_run` fails with
    the uncaught error and a real PNG screenshot; Alex fixes it; re-check is clean; v1 gets a rendered cover. ✅
  - team round: David's `browser_goto` renders Wikipedia in Chromium; Browser block stores a real PNG. ✅

## 2. Real LLM client path
- `oa/llm/mock_server.py` — OpenAI-compatible provider driven by the oa-mock policy: SSE streaming, tool calls split
  into 7-char argument fragments, `stream_options.include_usage`, `/v1/models`, `/v1/images/generations`, API-key auth,
  `--fail-every N` injected 503s, and strict request validation (roles/order, unanswered tool calls, function-name
  pattern, JSON-schema `parameters` keywords, `stream_options` only with `stream`). `GET /stats` for assertions.
- `tests/test_verify_llm_client.py` (5 tests): reassembly, retries, auth/validation errors, **every registered tool
  schema is provider-valid**, and a full team round through `OpenAICompatLLM` where billed tokens == provider usage.
- Over real HTTP (`OA_LLM_BASE_URL=http://127.0.0.1:4010/v1`, model `claude-sonnet-4-5`, `--fail-every 7`):
  - `scripts/e2e.sh lite` → **E2E passed** (18 completions, 2 injected 503s retried, 0 rejected)
  - `scripts/e2e.sh full` → **E2E passed** (57 completions over all 5 roles, 9 injected 503s retried, 0 rejected)
- **LiteLLM** (`ghcr.io/berriai/litellm:main-stable`, model `claude-sonnet-4-5` → `openai/…` at the mock provider):
  `scripts/e2e.sh full` through `http://127.0.0.1:4400/v1` → **E2E passed**, 0 rejected.
- Provider compatibility notes: the client sends only standard OpenAI chat fields (`model, messages, stream,
  stream_options.include_usage, tools, tool_choice:"auto", temperature?`). Tool schemas contain only standard JSON-schema
  keywords (`optional` is stripped). Assistant tool-call turns send `content: null` + `tool_calls`. For Anthropic use
  LiteLLM (`anthropic/claude-…`), which maps tools/streaming/usage; keep `drop_params: true` for providers that reject
  `stream_options`.

## 3. Integrations against fake providers (`tests/test_verify_integrations.py`)
Provider endpoints are settings now: `OA_GITHUB_API_URL`, `OA_GITHUB_OAUTH_URL`, `OA_GITHUB_GIT_URL`,
`OA_SUPABASE_API_URL`, `OA_STRIPE_API_URL`, `OA_GOOGLE_AUTH_URL`, `OA_GOOGLE_TOKEN_URL`, `OA_GOOGLE_USERINFO_URL`
(defaults = real services; also enables GitHub Enterprise). A uvicorn server impersonates the provider APIs and a
`git http-backend` smart-HTTP server (Basic auth like github.com) hosts repos.
- GitHub: OAuth connect → callback (single-use state) → encrypted token → repo create → push (verified in the bare
  repo) → remote edit → pull into the workspace; manual PAT; bad code. ✅
- Supabase: OAuth → organizations → link project → `.env` gets URL + anon key (never service_role). ✅
- Stripe: checkout session via API → bad signature / stale timestamp rejected → signed `checkout.session.completed`
  delivered twice → plan upgraded, exactly one ledger entry. ✅
- Google: new user (verified) created + refresh cookie; existing password user linked by verified email. ✅

**Bugs fixed**
- GitHub token leaked into the workspace git reflog (`.git/logs`) because the token was embedded in the remote URL —
  readable by agents, zip export and pushes. Now auth goes via `GIT_CONFIG_*` env `http.extraHeader` with a clean URL
  (also not visible in `ps`). Test asserts no file under `.git` contains the token.
- Google login linked an existing account by email even when Google reported `email_verified=false` (account takeover).
  Now rejected; new Google users get `email_verified` from Google's claim.

## 4. Email (mailpit)
`docker run -p 18025:8025 -p 11025:1025 axllent/mailpit`; with SMTP pointed at it, verification, password-reset and
workspace-invite emails are delivered (checked via the mailpit API) and the reset link from the delivered mail works.

## 5. Verified with real accounts (2026-10-30)
Evidence is in `data/oa.db` (`usage_events`, `users`, `integrations`) and `evals/results/`.
- **DeepSeek** (`deepseek-flash` via `OA_LLM_BASE_URL=https://api.deepseek.com/v1`): `evals/run.py` cases pomodoro,
  counter, landing (lite) and team-todo, guestbook-cloud (full, all 5 roles) all passed (build + checkui + content
  expectations) — 225 completions, ~4.3M input / ~0.18M output tokens, 26–479 s per case. Three earlier attempts
  failed: counter missed its content expectation, and landing and guestbook-cloud each failed within ~4 s because of the
  environment, not the model; reruns passed. Note: `deepseek-flash` is not in `billing.PRICES`, so its recorded
  `cost_usd` uses `DEFAULT_PRICE`, not DeepSeek's real pricing.
- **Google login** with a real OAuth app: new user created with `email_verified` taken from Google.
- **GitHub OAuth connect** with a real OAuth app: account linked and the token stored encrypted.
- **GitHub repo create / push** against github.com and **Supabase OAuth + project link**: verified manually by the
  project owner. These records are not in the current `data/oa.db`.

## Still requires the user's real accounts
Stripe test-mode keys + a real webhook endpoint (e.g. `stripe listen`); a real SMTP/SES sender domain; model quality
at scale (all 20 eval cases, repeated runs, other providers such as Anthropic/OpenAI/Moonshot).
