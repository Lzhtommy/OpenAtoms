# Visual editing (PLAN P9-2)

Atoms equivalent: `appviewer:sync-design-selection` + `/chats/:id/edit-session[/commit]`.

## Flow

```
template dev server ── Babel plugin stamps JSX: data-oa-loc="src/App.tsx:12:5"
        │
bridge.js (design mode) ── click → appviewer:sync-design-selection {selector, loc, tag, text, text_editable,
        │                                                          classes[], rect, styles{…}, path}
        │               ◀── appviewer:apply-preview {loc, add?, remove?, text?, style?}  (instant, optimistic)
workbench Properties panel ── POST /chats/:id/edit-session {edits:[…]}   (staged, merged per element)
        │                 ── POST /chats/:id/edit-session/commit
api ── rewrite.cjs (project's own `typescript` compiler API) → Editor blocks (owner "User") in a System
       message → create_version("Visual edit: …") → git commit + build → version:* events
```

## Pieces

| Piece | Path |
|---|---|
| Source mapping (dev only, zero new deps — runs inside `@vitejs/plugin-react`'s Babel) | `templates/react-shadcn-vite/vite-plugins/oa-loc.ts`, `vite.config.ts` |
| Bridge design mode, selected outline, apply-preview | `apps/bridge/src/index.ts` → `apps/gateway/assets/bridge.js` |
| Rewriter | `services/server/oa/visual_edit/rewrite.cjs` |
| Staging/merge/commit logic | `services/server/oa/visual_edit/__init__.py` |
| REST | `services/server/oa/api/edit_session.py` |
| Agent prompt hook (`metadata.selection`) | `services/server/oa/agents/runner.py::_prompt_text` |
| UI | `apps/web/src/components/workspace/visual-edit-panel.tsx`, `app-viewer.tsx` (toggle), `store/visual-edit.ts`, composer chip |
| Tests | `services/server/tests/test_visual_edit.py` |

## API

```
GET    /api/v1/chats/:id/edit-session          → {edits:[StagedEdit], count}
POST   /api/v1/chats/:id/edit-session          {edits:[{loc, add?, remove?, classes?, orig_classes?, text?, selector?, tag?}]}
DELETE /api/v1/chats/:id/edit-session
POST   /api/v1/chats/:id/edit-session/commit   → {version, files, edits}; 4400 {errors:[{loc,error}]} (nothing written);
                                                 4409 while an agent run is in progress
StagedEdit = {loc, add[], remove[], text|null, selector?, tag?}
```
- `loc` = `path:line:col` (1-based, `<` of the JSX tag), must be a .tsx/.jsx/.ts/.js file inside the workspace, not node_modules.
- `classes` + `orig_classes` (desired vs. original class list) is converted to add/remove. Edits of the same element merge
  (net add/remove, last text wins). Staging lives in `data/chats/{id}/edit_session.json` (shared volume).
- New socket event **`edit_session:update`** `{edits, count}` (shares the chat seq counter; the web client handles it).

## Rewrite rules (rewrite.cjs)
- `className="…"` / `{"…"}` / `` {`…`} `` (no substitutions): token-level remove + append.
- `cn(…)`/`clsx`/`twMerge`/`classNames`: removed tokens are stripped from string-literal args; additions appended as a
  last string argument (later args win in tailwind-merge).
- No className → inserted after the tag name.
- Template literal with `${}` or any other expression → error "ask the AI to change this element" (fallback).
- Text: only when the element's children are a single static text node or string literal (or empty); text with `{}<>`
  is emitted as `{"…"}`. Mixed children/expressions → error. Self-closing → error.
- All edits of a file are applied in one parse as non-overlapping splices; a stale loc ("file changed since the preview
  loaded") is reported per edit. Commit is all-or-nothing.

## Deviations / notes
- The visual-edit chat message is `role:"System"` with the user's name (not `role:"User"`): user messages are the nodes of
  the round/attempt branch tree, and a round-0 user message would become the root and hide the conversation (regression
  test covers this). Its Editor timeline blocks are owned by `"User"`; the workbench does not auto-follow those blocks.
- Newly picked Tailwind classes have no CSS in the dev server until written to source (JIT), so the panel also sends the
  equivalent inline `style` for the optimistic preview; after commit + HMR the real classes apply.
- The rewriter runs `node` on the API host with `NODE_PATH` = workspace (fallback template) `node_modules` — the API image
  already ships Node. Workspaces created before this template change have no `data-oa-loc`; the panel then shows
  "no source mapping" and offers "Ask AI".
- Hover label in the iframe shows the element's classes at hover time (it does not refresh after an optimistic preview).
- Observed (not fixed, outside scope): in `use-chat-session.ts`, an event arriving while `catchUp` is in flight is applied
  immediately and can advance `lastSeq` past replayed events. Any server event that is not in `CHAT_EVENTS` but consumes
  the chat seq triggers it — new chat events must be added to that list.
