# P9 features — implementation notes

Scope: PLAN.md P9+ items 1, 3, 4, 5, 6 (BoN/Boost, Iris/Sarah/Adrian, automations, multimodal tools, Expo mobile
template). Visual editing (item 2) is the VISUAL-EDIT workstream. Protocol names follow re/REPORT.md.

## 1. Boost / Best-of-N (竞赛模式)

Mirrors Atoms' `bon.parent_chat_id` design: every candidate is a hidden **child chat** forked from the parent's
current workspace and runs an ordinary round (own model, sandbox, preview, versions). Nothing in the runner,
tools or previews is special-cased.

- Start: `POST /chats/:id/messages` with `use_boost: true, boost_models: [m1..mK]` (2–4, repeats allowed). The
  response includes `boost`. A `type: "boost"` System card is posted in the parent chat and kept in sync
  (`msg:update`). The parent is marked running for the whole boost, so new prompts queue.
- Each child gets the prompt plus `metadata.boost_variant {index, total}`. The runner appends
  "candidate k of K — pick a distinctive visual direction", so candidates differ (oa-mock: blue / emerald /
  violet / amber accents).
- **Plan capacity:** each candidate needs a sandbox. `boost.start` checks `quotas.max_sandboxes` up front and
  stops the parent's idle sandbox if that makes room. Otherwise it returns 400 ("…at most N candidates") and
  releases the parent.
- A watcher (api process; resumed by `boost.recover()` at startup) polls the children and emits `boost:update`
  `{boost, items, boost_id}`. Each item carries `status`, `summary`, `build_ok`, `checkui_ok`, `steps`,
  `version`, `version_link` and `preview_url`.
- When all candidates finish, a judge recommends one and the watcher emits `boost:all_end`:
  - real basic model: LLM picks from the summaries;
  - `oa-mock`: deterministic score.
  - Only candidates whose run succeeded, did real work and did not fail the build are eligible.
- `POST /chats/:id/boost/select {index}`:
  - syncs the child workspace into the parent (ignores `.git`, `node_modules`, `dist`, `.atoms`);
  - runs `pnpm install` if dependencies changed and a sandbox is running;
  - posts a System message, then `create_version`;
  - emits `boost:merge_end`;
  - stops the child sandboxes.
- Other endpoints: `GET /chats/:id/boost` (latest), `POST /chats/:id/boost_abort`, `DELETE /chats/:id/boost`
  (abort plus soft-delete the children). `POST /chats/:id/stop` also aborts a *running* boost.
- Child chats are excluded from `/chats` and `/discover` via the `parent_chat_id IS NULL` filter, and the
  serializer exposes `parent_chat_id`.
- Web:
  - **Composer:** `BoostPicker`, a 竞赛 toggle with model slots; hidden inside child chats.
  - **Dashboard prompt box:** the same picker. Boost projects are created first, then the prompt is sent.
  - **Chat:** `BoostCard`.
  - **Boost panel:** side-by-side live previews, each through that child's own preview ticket. Also shows
    build/preview badges, the recommendation, Pick & merge, Stop and Discard.

## 2. Iris / Sarah / Adrian

- **Roles** (`oa/agents/roles.py` + prompts): Iris (Deep Researcher), Sarah (SEO Specialist), Adrian
  (Ads Specialist). Mike's prompt and the plan tools know about them.
- **Tools** (`oa/tools/research.py`):
  - `searchnavigator_next_query` and `researchexecutor_finalize_article` write `Research` blocks. The report
    goes to `docs/research/report.md` with numbered `## Sources`.
  - `seo_audit` (`Growth` block) runs 9 Lighthouse-style weighted checks over the served preview HTML plus the
    JSX sources (SPAs render headings and images client-side). It records score history.
  - `ads_generate_campaign` (`Growth` block) clips headlines to ≤30 and descriptions to ≤90 chars and writes
    `docs/ads/campaign.md`.
- **Modes:**
  - **Deep research:** `metadata.enable_dr: true` (composer "研究" toggle / dashboard "深度研究") gives
    `Run.state.deep_research`, and the orchestrator's `research` phase runs Iris alone.
  - **Full team:** Mike appends Sarah/Adrian tasks when the prompt mentions SEO or ads/marketing. Sarah's tasks
    get the same build verification as Alex's.
- **Web:** Research panel (findings, sources, report) and Growth panel (SEO score ring and checks, ad groups,
  budget, KPIs). Agents roster and role colors include Sarah and Adrian.

## 3. Automations (定时任务)

- **Model:** `automations(id, chat_id, workspace_id, user_id, name, prompt, cron, timezone, enabled,
  last_run_at, next_run_at, last_status, last_message_id, run_count)`.
- **Cron** (`oa/domain/automations.py`): 5 fields with lists, ranges and steps, month/day names, `7`=Sunday,
  @macros, and Vixie day-of-month OR day-of-week semantics. Evaluated in an IANA timezone and stored in UTC.
  No extra dependency.
- **Scheduler:** `tick()` claims due rows with a compare-and-set on `next_run_at`, so concurrent api replicas
  and workers never double-fire. It then posts the prompt through `post_user_message`, which queues it if the
  project is busy, and emits `automation:update`. It runs as an in-process loop (inproc mode) or an arq cron
  job every 30 s (`OA_QUEUE=arq`).
- **REST:** `GET/POST /automations`, `PATCH/DELETE /automations/:id`, `POST /automations/:id/run`,
  `GET /automations/:id/history`, `GET /automations/presets`.
- **Web:** `/automations` page (sidebar nav): list, create with cron presets and timezone, enable toggle,
  run now, history.

## 4. Multimodal tools (`oa/tools/media.py`)

| Tool | Behaviour |
|---|---|
| `imagecreator_generate_image` | Real model: OpenAI-compatible `/images/generations` (`OA_IMAGE_MODEL`, default `gpt-image-1`). `oa-mock` or a provider error: deterministic procedural PNG art (Pillow). Saved to `public/assets/<slug>.png`. |
| `imageanalyzer_analyze_images` | Format, size, orientation, dominant palette and tone; real model adds a vision description. |
| `pdfanalyzer_analyze_pdfs` | pypdf text plus a summary (real model, or an extractive summary offline). |
| `documentextractor_extract_documents` | PDF / DOCX (python-docx) / text extraction. |
| `markdownpdfcreator_convert_file` | Markdown → PDF via fpdf2: headings, lists, tables, code. CJK works when a Unicode TTF is present (Arial Unicode / Noto CJK / DejaVu; otherwise latin-1 fallback). |

- **Blocks:** images use `Assets`, documents use `File`. Content is `{items:[{path, kind, preview, ...}],
  summary}`, where `preview` is a `.storage/...` copy of the image or PDF.
- **Attachments:** composer and dashboard uploads already reach agents via `metadata.attachments`. Engineer and
  Data Analyst have the media tools; the mock analyzes attachments first, generates hero art when asked for
  images, and exports `docs/summary.pdf` when asked for a PDF.
- **Web:** Assets panel showing a gallery with palettes, plus documents with excerpts and open/download.
- **Dependencies added:** pillow, pypdf, python-docx, fpdf2.

## 5. Mobile template (`templates/expo-app`)

- **Stack:** Expo SDK 58, React Native 0.88, react-native-web, React 19.3. pnpm with `nodeLinker: hoisted`
  (Expo's documented pnpm setup), as a standalone workspace like the web template.
- **Scripts:** `dev` runs `expo start --web --port $PORT`, the Metro dev server proxied by the gateway. `build`
  runs `expo export --platform web --output-dir dist`, so versions and publishing work unchanged.
- **Template selection:** `chat.config.template_name: "expo-app"` (dashboard Web/Mobile switch) makes
  `manager.init_workspace` use that template. `template_path()` resolves names at call time and falls back to
  `settings.template_dir`. The template's `.atoms/memory.md` tells agents the stack is Expo (React Native).
- **Agents:** the engineer prompt has a React Native section, and oa-mock writes React Native apps
  (`oa/llm/mock_expo.py`).
- **App Viewer:** opens mobile projects in the phone frame by default.
- **Sandbox image:** pre-warms the Expo dependencies. Checked: a fresh copy installs with
  `pnpm install --offline` and exports with `--network none`.

## Schema / migration

`0002_p9_features` adds `chats.parent_chat_id`, `boosts` and `automations`. It was verified on Postgres 16
(upgrade, no autogenerate drift, then down/up). **If another workstream also adds a revision on top of `0001`,
chain one of them onto the other** (alembic will report multiple heads otherwise).

## Behaviour change (platform)

`SandboxFull` raised inside a tool now aborts the run: the run is marked failed and a System `error` message
explains the plan limit. Previously every tool call failed individually and the run could still report success.

## Verification

- `uv run pytest` passes: 113 passed, 1 skipped (another workstream's test). `tests/test_p9_features.py` adds
  23 tests covering cron, automations API and race-free ticks, queueing, deep research, team with SEO and ads,
  media round-trips, attachments, Expo selection, React Native mock output, boost (plan limit, run, judge,
  merge, abort) and SandboxFull.
- `pnpm --filter @oa/web build` is clean (types and lint).
- `uv run scripts/e2e.py --only-p9` (or `--p9` after P1–P7) passed against a live lite stack (real sandboxd,
  pnpm/Vite, `expo export`, gateway):
  - boost 2 candidates → previews → merge #2 → v1;
  - Iris report;
  - SEO 55 → 100 plus campaign;
  - image analysis, document extraction, hero PNG, PDF export;
  - automation fired by the scheduler;
  - Expo build, preview and publish.
- The default (non-P9) e2e still passes.
