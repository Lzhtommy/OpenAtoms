"""`oa-mock`: a deterministic, key-less model that plays every role with real tool calls.

It reads the conversation the orchestrator built for a role (system prompt, instruction, prior tool calls
and results) and decides the next step from what has already happened — so it is resumable, reacts to
tool failures and human replies, and drives the exact same code paths as a real LLM.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from oa.llm import mock_apps as apps
from oa.llm.base import LLMResult, TextCallback, ToolCall, Usage, estimate_tokens

REQ_MARK = "User requirement:"


def _delay() -> float:
    try:
        return float(os.environ.get("OA_MOCK_DELAY", "0.012"))
    except ValueError:
        return 0.0


_ATTACHMENT_RE = re.compile(r"\[attachment: [^\]]*\]\s*")


class _Conv:
    """Read-only view over an OpenAI-format message list."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages
        last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
        self.instruction = str(messages[last_user].get("content") or "") if last_user >= 0 else ""
        self.segment = messages[last_user + 1:]
        self.results: dict[str, str] = {m.get("tool_call_id", ""): str(m.get("content") or "")
                                        for m in messages if m.get("role") == "tool"}

    @property
    def requirement(self) -> str:
        return _ATTACHMENT_RE.sub("", self._requirement()).strip()

    def _requirement(self) -> str:
        for m in self.messages:
            if m.get("role") != "user":
                continue
            c = str(m.get("content") or "")
            if REQ_MARK in c:
                return c.split(REQ_MARK, 1)[1].strip().split("\n\n")[0].strip()
        for m in self.messages:
            if m.get("role") == "user":
                return str(m.get("content") or "").strip()
        return ""

    def calls(self, scope: str = "segment") -> list[tuple[str, dict[str, Any], str]]:
        """[(name, args, result)] of tool calls in the current segment (or whole conversation)."""
        msgs = self.segment if scope == "segment" else self.messages
        out = []
        for m in msgs:
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                out.append((fn.get("name", ""), args, self.results.get(tc.get("id", ""), "")))
        return out

    def called(self, name: str, scope: str = "segment") -> bool:
        return any(n == name for n, _, _ in self.calls(scope))

    def last_result(self, name: str, scope: str = "all") -> str | None:
        for n, _, res in reversed(self.calls(scope)):
            if n == name:
                return res
        return None


Step = tuple[str, list[tuple[str, dict[str, Any]]]]


class MockLLM:
    model = "oa-mock"

    def __init__(self, model: str = "oa-mock", delay: float | None = None) -> None:
        self.model = model
        self._delay = delay

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_text: TextCallback | None = None,
        *,
        role: str = "",
        temperature: float | None = None,
    ) -> LLMResult:
        conv = _Conv(messages)
        tool_names = {t["function"]["name"] for t in tools or []}
        text, calls = self._decide(role, conv, tool_names)
        calls = [(n, a) for n, a in calls if not tool_names or n in tool_names]
        delay = _delay() if self._delay is None else self._delay
        if on_text and text:
            for tok in _tokens(text):
                await on_text(tok)
                if delay:
                    await asyncio.sleep(delay)
        tool_calls = [
            ToolCall(id=f"call_{len(messages)}_{i}", name=n, arguments=json.dumps(a, ensure_ascii=False))
            for i, (n, a) in enumerate(calls)
        ]
        usage = Usage(estimate_tokens(messages), estimate_tokens(text) + estimate_tokens([t.arguments for t in tool_calls]))
        return LLMResult(text=text, tool_calls=tool_calls, usage=usage,
                         finish_reason="tool_calls" if tool_calls else "stop", model=self.model)

    # ------------------------------------------------------------------ dispatch
    def _decide(self, role: str, c: _Conv, tools: set[str]) -> Step:
        req = c.requirement
        zh = apps.has_cjk(req)
        if role == "Team Leader":
            return self._mike(c, req, zh)
        if role == "Product Manager":
            return self._emma(c, req, zh)
        if role == "Architect":
            return self._bob(c, req, zh)
        if role == "Data Analyst":
            return self._david(c, req, zh)
        if role == "Deep Researcher":
            return self._iris(c, req, zh, tools)
        if role == "SEO Specialist":
            return self._sarah(c, req, zh, tools)
        if role == "Ads Specialist":
            return self._adrian(c, req, zh, tools)
        return self._alex(c, req, zh, tools)

    # ------------------------------------------------------------------ Mike
    def _mike(self, c: _Conv, req: str, zh: bool) -> Step:
        if "[summary]" in c.instruction:
            kind = apps.detect_kind(req)
            title = apps.app_title(kind, req)
            body = (
                f"项目「{title}」已完成 🎉\n\n团队协作完成了调研、PRD、技术设计与实现，构建已通过。"
                f"你可以在右侧 App Viewer 中预览，满意后点击右上角「发布」即可上线。"
                if zh else
                f"**{title}** is ready 🎉\n\nThe team finished research, the PRD, the technical design and the "
                f"implementation, and the production build passes. Preview it in the App Viewer on the right, "
                f"then click **Publish** when you're happy."
            )
            return ("", [("reply_to_human", {"content": body})])

        if "clarify" in req.lower() and not c.called("ask_questions", "all"):
            return (
                "Before planning I want to confirm a few details." if not zh else "在制定计划前，我想先确认几个细节。",
                [("ask_questions", {
                    "title": "A few quick questions",
                    "questions": [
                        {"id": "audience", "question": "Who is the primary audience?", "type": "single",
                         "options": ["Personal use", "Small team", "Public users"]},
                        {"id": "style", "question": "Preferred visual style?", "type": "single",
                         "options": ["Minimal", "Playful", "Corporate"]},
                        {"id": "notes", "question": "Anything else we should know?", "type": "text"},
                    ],
                })],
            )

        kind = apps.detect_kind(req)
        title = apps.app_title(kind, req) or "the product"
        if not c.called("plan_append_task"):
            text = (
                f"收到！目标是构建「{title}」。我会先让 David 做快速调研，Emma 写 PRD，Bob 做技术设计，最后由 Alex 实现并验证构建。"
                if zh else
                f"Got it — we're building **{title}**. I'll have David do quick research, Emma write the PRD, "
                f"Bob produce the technical design, and Alex implement and verify the build."
            )
            tasks = [
                ("1", "Research similar products and best practices", "David",
                 f"Research best practices and reference products for: {req}. Save notes to docs/research/notes.md."),
                ("2", "Write the PRD", "Emma",
                 f"Write a concise PRD for: {req}. Save it to docs/prd.md."),
                ("3", "Design the architecture", "Bob",
                 "Based on docs/prd.md, write the technical design (components, state, files) to docs/design.md."),
                ("4", "Implement the app and verify the build", "Alex",
                 "Implement the app following docs/prd.md and docs/design.md; make sure `pnpm build` passes."),
            ]
            if wants_seo(req):
                tasks.append(("5", "SEO audit and fixes", "Sarah",
                              "Audit the built app with seo_audit and fix title/meta/headings/alt/Open Graph issues."))
            if wants_ads(req):
                tasks.append((str(len(tasks) + 1), "Ad campaign plan", "Adrian",
                              f"Create a launch ad campaign for: {req}. Save it with ads_generate_campaign."))
            return (text, [
                ("plan_append_task", {"task_id": tid, "title": t, "assignee": who, "instruction": ins})
                for tid, t, who, ins in tasks
            ])
        if not c.called("mention"):
            first = next((a for n, a, _ in c.calls() if n == "plan_append_task"), {})
            return ("", [("mention", {"name": first.get("assignee", "David"),
                                      "instruction": first.get("instruction", "Please start.")})])
        return ("", [("reply_to_human", {"content": "The plan is in place." if not zh else "计划已就绪。"})])

    # ------------------------------------------------------------------ Emma
    def _emma(self, c: _Conv, req: str, zh: bool) -> Step:
        kind = apps.detect_kind(req)
        if not c.called("editor_write"):
            feats = apps.features(kind, req)
            title = apps.app_title(kind, req)
            prd = "\n".join([
                f"# PRD — {title}",
                "",
                "## Goal",
                f"Deliver a polished, fast web app that satisfies: _{req}_",
                "",
                "## Target users",
                "- Individuals who want a focused, distraction-free tool",
                "- Works on desktop and mobile browsers",
                "",
                "## User stories",
                *[f"- As a user, I can {f[0].lower() + f[1:]}." for f in feats],
                "",
                "## Functional requirements",
                *[f"{i}. {f}" for i, f in enumerate(feats, 1)],
                "",
                "## UI notes",
                "- Clean card-based layout on a soft gradient background",
                "- Clear primary action, accessible contrast, keyboard friendly",
                "",
                "## Out of scope",
                "- Server-side sync (can be added later with OpenAtoms Cloud)",
                "",
            ])
            return (
                "我会把需求整理成一份简洁的 PRD。" if zh else "I'll turn the requirement into a concise PRD.",
                [("editor_write", {"path": "docs/prd.md", "content": prd})],
            )
        return ("", [("plan_finish_current_task", {"summary": "PRD written to docs/prd.md"})])

    # ------------------------------------------------------------------ Bob
    def _bob(self, c: _Conv, req: str, zh: bool) -> Step:
        kind = apps.detect_kind(req)
        comp = apps.COMPONENT[kind]
        if not c.called("terminal_run"):
            return ("Let me look at the project layout first." if not zh else "先看一下项目结构。",
                    [("terminal_run", {"cmd": "ls && ls src && cat package.json | head -n 25"})])
        if not c.called("editor_write"):
            design = "\n".join([
                f"# Technical design — {apps.app_title(kind, req)}",
                "",
                "## Stack",
                "React 18 + TypeScript + Vite 5 + Tailwind CSS 3 (shadcn-style utilities), lucide-react icons.",
                "",
                "## File plan",
                "| File | Purpose |",
                "|---|---|",
                "| `src/App.tsx` | Page shell: header, main, footer |",
                f"| `src/components/{comp}.tsx` | Feature component with all state and interactions |",
                "",
                "## State",
                "- Local React state (`useState`/`useEffect`); persistence via `localStorage` where useful",
                "",
                "## Quality gates",
                "- `pnpm build` must pass",
                "- No runtime console errors in the preview",
                "",
            ])
            return ("I'll document the component structure and state model." if not zh else "我会写下组件结构与状态设计。",
                    [("editor_write", {"path": "docs/design.md", "content": design})])
        return ("", [("plan_finish_current_task", {"summary": "Design written to docs/design.md"})])

    # ------------------------------------------------------------------ David
    def _david(self, c: _Conv, req: str, zh: bool) -> Step:
        kind = apps.detect_kind(req)
        topic = {"todo": "todo list app", "pomodoro": "pomodoro technique", "counter": "tally counter",
                 "landing": "landing page"}[kind]
        if not c.called("searchenhancedqa_run"):
            return ("Let me search for best practices first." if not zh else "我先搜索一下最佳实践。",
                    [("searchenhancedqa_run", {"query": f"{topic} best practices UX"})])
        if not c.called("browser_goto"):
            page = {"todo": "Time_management", "pomodoro": "Pomodoro_Technique", "counter": "Tally_counter",
                    "landing": "Landing_page"}[kind]
            return ("", [("browser_goto", {"url": f"https://en.wikipedia.org/wiki/{page}"})])
        if not c.called("dataanalyst_write_and_exec_code"):
            feats = apps.features(kind, req)
            code = (
                "features = " + json.dumps(feats) + "\n"
                "scores = {f: round(10 - i * 1.3, 1) for i, f in enumerate(features)}\n"
                "print(f\"{'Feature':<40} Priority\")\n"
                "for f, s in sorted(scores.items(), key=lambda x: -x[1]):\n"
                "    print(f'{f:<40} {s:>5}')\n"
                "print('\\nMust-have count:', sum(1 for s in scores.values() if s >= 6))\n"
            )
            return ("I'll prioritise the candidate features." if not zh else "我来给候选功能排个优先级。",
                    [("dataanalyst_write_and_exec_code", {"code": code})])
        if not c.called("editor_write"):
            nb = c.last_result("dataanalyst_write_and_exec_code", "segment") or ""
            notes = "\n".join([
                f"# Research notes — {topic}",
                "",
                "## Key findings",
                "- Users value speed and a single obvious primary action.",
                "- State should survive reloads (localStorage) for personal tools.",
                "- Keyboard support and clear empty states reduce friction.",
                "",
                "## Feature priority",
                "```",
                nb.strip()[:1500],
                "```",
                "",
            ])
            return ("", [("editor_write", {"path": "docs/research/notes.md", "content": notes})])
        return ("", [("plan_finish_current_task", {"summary": "Research notes saved to docs/research/notes.md"})])

    # ------------------------------------------------------------------ Iris / Sarah / Adrian
    def _iris(self, c: _Conv, req: str, zh: bool, tools: set[str]) -> Step:
        return _iris_mock(c, req, zh, tools)

    def _sarah(self, c: _Conv, req: str, zh: bool, tools: set[str]) -> Step:
        audits = [r for n, _, r in c.calls() if n == "seo_audit"]
        if not audits:
            return ("先跑一次 SEO 审计。" if zh else "Running an SEO audit of the app first.", [("seo_audit", {})])
        if not c.called("editor_read"):
            return ("", [("editor_read", {"path": "index.html"})])
        if not c.called("editor_edit_file_by_replace"):
            html = c.last_result("editor_read") or ""
            kind = apps.detect_kind(req)
            title = (apps.app_title(kind, req) or "App")[:50]
            desc = (f"{title} — {apps.subtitle_for(kind, req)}" if kind != "landing" else
                    f"{title}: a fast, modern web experience built with OpenAtoms.")
            desc = (desc + " Fast, accessible and free to use in your browser.")[:155]
            head = (f'<title>{title}</title>\n    <meta name="description" content="{desc}" />\n'
                    f'    <meta property="og:title" content="{title}" />\n'
                    f'    <meta property="og:description" content="{desc}" />\n'
                    '    <link rel="canonical" href="/" />')
            m = re.search(r"<title>.*?</title>", html)
            old_str, new_str = (m.group(0), head) if m else ("</head>", head + "\n  </head>")
            return ("修复标题、描述和 Open Graph 标签。" if zh else "Fixing the title, meta description and Open Graph tags.",
                    [("editor_edit_file_by_replace", {"path": "index.html", "old_str": old_str, "new_str": new_str})])
        if len(audits) < 2:
            return ("再审计一次确认分数提升。" if zh else "Re-running the audit to confirm the improvement.",
                    [("seo_audit", {})])
        score = re.search(r"SEO score: (\d+)", audits[-1])
        summary = f"SEO score now {score.group(1) if score else '?'}/100 (title, description, Open Graph, canonical fixed)."
        if "## Task" in c.instruction:
            return ("", [("plan_finish_current_task", {"summary": summary})])
        return ("", [("reply_to_human", {"content": summary})])

    def _adrian(self, c: _Conv, req: str, zh: bool, tools: set[str]) -> Step:
        kind = apps.detect_kind(req)
        title = apps.app_title(kind, req) or "the product"
        if not c.called("ads_generate_campaign"):
            groups = [
                {"name": "Brand", "headlines": [title[:30], "Try it free today", "Built for focus"],
                 "descriptions": [f"{title[:50]} — simple, fast and free to start.",
                                  "No sign-up friction. Works on any device."],
                 "keywords": [title.lower()[:30], f"{kind} app", f"best {kind} app"]},
                {"name": "Problem-aware", "headlines": ["Get more done", "Stop losing track", "Your day, organised"],
                 "descriptions": ["Plan, focus and finish with a tool that stays out of your way.",
                                  "Join thousands who switched to a calmer workflow."],
                 "keywords": [f"{kind} tool", "productivity app", "focus tool"]},
                {"name": "Competitor", "headlines": ["A lighter alternative", "Switch in one minute"],
                 "descriptions": ["Everything you need, nothing you don't. Free forever plan.",
                                  "Import your data and start in seconds."],
                 "keywords": [f"{kind} alternative", f"free {kind} app"]},
            ]
            return ("为上线制定广告投放计划。" if zh else "Drafting the launch ad campaign.",
                    [("ads_generate_campaign", {
                        "product": title, "objective": "Drive sign-ups in the first 30 days after launch",
                        "audiences": ["Students and knowledge workers 18–35", "Productivity enthusiasts", "Remote teams"],
                        "channels": ["Google Search", "Meta (Instagram/Facebook)", "Reddit"],
                        "ad_groups": groups,
                        "budget": {"daily": "$50", "split": {"Google Search": "50%", "Meta": "35%", "Reddit": "15%"}},
                        "kpis": ["CTR ≥ 3%", "Cost per sign-up ≤ $2.50", "Day-7 retention ≥ 25%"],
                    })])
        summary = "Campaign plan saved to docs/ads/campaign.md (3 ad groups, $50/day across 3 channels)."
        if "## Task" in c.instruction:
            return ("", [("plan_finish_current_task", {"summary": summary})])
        return ("", [("reply_to_human", {"content": summary})])

    # ------------------------------------------------------------------ Alex
    def _alex(self, c: _Conv, req: str, zh: bool, tools: set[str]) -> Step:
        kind = apps.detect_kind(req)
        comp = apps.COMPONENT[kind]
        in_task = "## Task" in c.instruction
        finish = (
            ("plan_finish_current_task", {"summary": "Implemented the app; build passes."})
            if in_task and "plan_finish_current_task" in tools else None
        )

        # build-repair loop requested by the orchestrator
        if "build failed" in c.instruction.lower():
            if not c.called("terminal_run"):
                return ("Let me rebuild and look at the errors." if not zh else "我重新构建并检查错误。",
                        [("terminal_run", {"cmd": "pnpm build"})])
            if "reply_to_human" in tools:
                return ("", [("reply_to_human", {"content": "I fixed the build." if not zh else "构建问题已修复。"})])
            return ("", [("plan_finish_current_task", {"summary": "Build fixed."})])

        pre = _media_prelude(c, req, zh)
        if pre is not None:
            return pre
        expo = _is_expo(c)

        if "ask me" in req.lower() and not c.called("ask_human", "all"):
            return ("Before I start, one design decision:" if not zh else "开始之前，有一个设计问题想确认：",
                    [("ask_human", {"question": "Which accent color do you prefer?",
                                    "options": [{"label": "Blue"}, {"label": "Green"}, {"label": "Purple"}]})])
        accent = _accent(c.last_result("ask_human") or "") if c.called("ask_human", "all") else _variant_accent(c)
        if expo:
            return _alex_expo(c, req, zh, tools, kind, accent, finish)

        if not c.called("editor_read"):
            return (
                f"I'll implement the {apps.app_title(kind, req) or 'landing page'} in the template. Let me check the entry first."
                if not zh else "我会在模板中实现这个应用，先看一下入口文件。",
                [("editor_read", {"path": "src/App.tsx"})],
            )
        inject = apps.wants_runtime_error(req)
        if not c.called("editor_write"):
            writes = [
                ("editor_write", {"path": f"src/components/{comp}.tsx",
                                  "content": apps.component_tsx(kind, req, accent)}),
                ("editor_write", {"path": "src/App.tsx",
                                  "content": apps.with_telemetry(apps.app_tsx(kind, req, draft=True)) if inject
                                  else apps.app_tsx(kind, req, draft=True)}),
            ]
            if inject:
                writes.insert(1, ("editor_write", {"path": "src/components/Telemetry.tsx",
                                                   "content": apps.TELEMETRY_BROKEN}))
            return (
                f"Writing the `{comp}` component and the page shell." if not zh else f"编写 `{comp}` 组件和页面外壳。",
                writes,
            )
        if not c.called("editor_edit_file_by_replace"):
            return ("Polishing the header copy." if not zh else "润色一下标题文案。",
                    [("editor_edit_file_by_replace", {
                        "path": "src/App.tsx",
                        "old_str": '<p className="mt-2 text-slate-500">Draft</p>',
                        "new_str": f'<p className="mt-2 text-slate-500">{apps.subtitle_for(kind, req)}</p>',
                    })])
        builds = [(a, r) for n, a, r in c.calls() if n == "terminal_run" and "build" in str(a.get("cmd", ""))]
        if not builds:
            return ("Now let me verify the production build." if not zh else "现在验证生产构建。",
                    [("terminal_run", {"cmd": "pnpm build"})])
        if "checkui_run" in tools and not c.called("checkui_run"):
            return ("Build is green. Checking the running preview for errors." if not zh else "构建通过，检查预览是否有报错。",
                    [("checkui_run", {"path": "/"})])
        # runtime-error repair loop: react to what checkui actually observed in the browser
        checks = [r for n, _, r in c.calls() if n == "checkui_run"]
        if checks and checks[-1].startswith("Preview has errors") and len(checks) <= 3:
            if apps.INJECTED_MARK in checks[-1] and not any(
                    n == "editor_write" and a.get("path") == "src/components/Telemetry.tsx"
                    and a.get("content") == apps.TELEMETRY_FIXED for n, a, _ in c.calls()):
                return ("The preview throws at runtime in `Telemetry` (useEffect). Making it fail-safe."
                        if not zh else "预览运行时在 `Telemetry` 的 useEffect 中抛错，改为容错实现。",
                        [("editor_write", {"path": "src/components/Telemetry.tsx", "content": apps.TELEMETRY_FIXED})])
            return ("Re-checking the preview after the fix." if not zh else "修复后重新检查预览。",
                    [("checkui_run", {"path": "/"})])
        if wants_pdf(req) and not c.called("markdownpdfcreator_convert_file"):
            if not c.called("editor_write") or not any(a.get("path") == "docs/summary.md" for n, a, _ in c.calls()
                                                       if n == "editor_write"):
                return ("Writing a project summary to export as PDF." if not zh else "写一份项目说明并导出为 PDF。",
                        [("editor_write", {"path": "docs/summary.md", "content": _summary_md(kind, req)})])
            return ("", [("markdownpdfcreator_convert_file", {"path": "docs/summary.md"})])
        if finish:
            return ("", [finish])
        feats = apps.features(kind, req)
        body = (
            f"已完成「{apps.app_title(kind, req)}」✅\n\n" + "\n".join(f"- {f}" for f in feats)
            + "\n\n生产构建已通过，右侧可以直接预览。"
            if zh else
            f"Done! **{apps.app_title(kind, req)}** is ready ✅\n\n" + "\n".join(f"- {f}" for f in feats)
            + "\n\nThe production build passes and the preview is live on the right."
        )
        return ("", [("reply_to_human", {"content": body})])


def _accent(reply: str) -> str:
    r = reply.lower()
    if "green" in r or "绿" in r:
        return "emerald"
    if "purple" in r or "violet" in r or "紫" in r:
        return "violet"
    return "blue"


_TOKEN_RE = re.compile(r"[一-鿿]|\s*\S{1,6}|\s+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


# ---------------------------------------------------------------- P9 helpers (specialists, media, BoN, Expo)
VARIANT_ACCENTS = ("blue", "emerald", "violet", "amber")
_VARIANT_RE = re.compile(r"candidate (\d+) of (\d+)")
_ATT_RE = re.compile(r"^- (.+?) \(([^,()]+), ([^)]*)\)$", re.M)


def wants_seo(req: str) -> bool:
    return bool(re.search(r"\bseo\b|search engine|搜索引擎|搜索排名", req.lower()))


def wants_ads(req: str) -> bool:
    return bool(re.search(r"\bads?\b|advert|campaign|marketing|广告|投放|营销", req.lower()))


def wants_image(req: str) -> bool:
    return bool(re.search(r"\bimage\b|\blogo\b|illustration|hero (image|art)|picture|配图|图片|插画|生成图|海报", req.lower()))


def wants_pdf(req: str) -> bool:
    return bool(re.search(r"\bpdf\b", req.lower()))


def attachments(c: _Conv) -> list[tuple[str, str, str]]:
    """[(name, path, mime)] parsed from the 'Attached files' block the runner appends to the prompt."""
    text = c.instruction if "Attached files" in c.instruction else next(
        (str(m.get("content") or "") for m in c.messages if m.get("role") == "user"
         and "Attached files" in str(m.get("content") or "")), "")
    if "Attached files" not in text:
        return []
    block = text.split("Attached files", 1)[1]
    return [(n.strip(), p.strip().lstrip("/"), mime.strip()) for n, p, mime in _ATT_RE.findall(block)]


def _variant_accent(c: _Conv) -> str:
    m = _VARIANT_RE.search(c.instruction) or next(
        (_VARIANT_RE.search(str(x.get("content") or "")) for x in c.messages if x.get("role") == "user"
         and _VARIANT_RE.search(str(x.get("content") or ""))), None)
    return VARIANT_ACCENTS[(int(m.group(1)) - 1) % len(VARIANT_ACCENTS)] if m else "blue"


def _is_expo(c: _Conv) -> bool:
    blob = c.instruction + "".join(str(m.get("content") or "") for m in c.messages if m.get("role") == "user")
    return "Expo (React Native)" in blob


def _media_prelude(c: _Conv, req: str, zh: bool) -> Step | None:
    """Analyze uploaded attachments and generate requested imagery before writing code."""
    atts = attachments(c)
    images = [p for _, p, mime in atts if mime.startswith("image/") or p.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))]
    pdfs = [p for _, p, mime in atts if mime == "application/pdf" or p.lower().endswith(".pdf")]
    docs = [p for _, p, _ in atts if p not in images and p not in pdfs]
    if images and not c.called("imageanalyzer_analyze_images", "all"):
        return ("先分析一下你上传的图片。" if zh else "Let me look at the image you attached first.",
                [("imageanalyzer_analyze_images", {"paths": images,
                                                   "question": "What does it show and which palette fits the UI?"})])
    if pdfs and not c.called("pdfanalyzer_analyze_pdfs", "all"):
        return ("先阅读你上传的 PDF。" if zh else "Reading the PDF you attached.",
                [("pdfanalyzer_analyze_pdfs", {"paths": pdfs, "question": "Summarize the requirements."})])
    if docs and not c.called("documentextractor_extract_documents", "all"):
        return ("提取文档内容。" if zh else "Extracting the attached document.",
                [("documentextractor_extract_documents", {"paths": docs})])
    if wants_image(req) and not c.called("imagecreator_generate_image", "all"):
        kind = apps.detect_kind(req)
        prompt = f"Hero illustration for {apps.app_title(kind, req) or req[:60]}, soft gradients, modern, clean"
        return ("先为页面生成一张主视觉图。" if zh else "Generating a hero image for the page first.",
                [("imagecreator_generate_image", {"prompt": prompt, "path": "public/assets/hero.png"})])
    return None


def _summary_md(kind: str, req: str) -> str:
    feats = apps.features(kind, req)
    return "\n".join([f"# {apps.app_title(kind, req) or 'Project'} — summary", "",
                      f"Requirement: {req}", "", "## Features", *[f"- {f}" for f in feats], "",
                      "## How to use", "Open the preview, try the main flow, then publish from the top-right.", ""])


def _alex_expo(c: _Conv, req: str, zh: bool, tools: set[str], kind: str, accent: str, finish: Any) -> Step:
    from oa.llm import mock_expo

    if not c.called("editor_read"):
        return ("这是一个 Expo 移动应用项目，先看一下入口。" if zh else "This is an Expo mobile project; checking the entry first.",
                [("editor_read", {"path": "App.tsx"})])
    if not c.called("editor_write"):
        return ("编写 React Native 界面。" if zh else "Writing the React Native screen.",
                [("editor_write", {"path": "App.tsx", "content": mock_expo.app_tsx(kind, req, accent)})])
    if not any(n == "terminal_run" and "build" in str(a.get("cmd", "")) for n, a, _ in c.calls()):
        return ("导出 Web 版本以验证构建。" if zh else "Exporting the web build to verify it compiles.",
                [("terminal_run", {"cmd": "pnpm build"})])
    if "checkui_run" in tools and not c.called("checkui_run"):
        return ("", [("checkui_run", {"path": "/"})])
    if finish:
        return ("", [finish])
    body = (f"移动应用「{apps.app_title(kind, req)}」已完成 📱 构建通过，右侧以手机框预览。" if zh else
            f"The mobile app **{apps.app_title(kind, req)}** is ready 📱 — the build passes and the preview "
            "on the right shows it in a phone frame.")
    return ("", [("reply_to_human", {"content": body})])


def _iris_mock(c: _Conv, req: str, zh: bool, tools: set[str]) -> Step:
    topic = req.strip().splitlines()[0][:120] if req.strip() else "the topic"
    plan = [f"{topic} overview", f"{topic} key data and trends", f"{topic} risks and open questions"]
    searches = [a.get("query") for n, a, _ in c.calls() if n == "searchenhancedqa_run"]
    navs = [a for n, a, _ in c.calls() if n == "searchnavigator_next_query"]
    if c.called("researchexecutor_finalize_article"):
        body = ("研究报告已完成，见 docs/research/report.md。要点：\n" if zh else
                "The research report is ready in docs/research/report.md. Highlights:\n")
        body += "\n".join(f"- {f.get('findings', '')[:140]}" for f in navs)
        if "## Task" in c.instruction:
            return ("", [("plan_finish_current_task", {"summary": "Report written to docs/research/report.md"})])
        return ("", [("reply_to_human", {"content": body})])
    if len(searches) <= len(navs):
        if len(navs) >= len(plan):
            sources = [u for f in navs for u in f.get("sources", [])]
            sections = "\n\n".join(
                f"## {i}. {plan[i - 1].removeprefix(topic).strip().capitalize() or 'Overview'}\n\n"
                f"{f.get('findings', '')} [{i}]" for i, f in enumerate(navs, 1))
            content = (f"## Executive summary\n\nThis report investigates **{topic}** across {len(navs)} lines of "
                       f"inquiry using {len(sources)} sources.\n\n{sections}\n\n## Conclusion\n\n"
                       "The evidence points to clear opportunities; validate the open questions with users before "
                       "committing resources.")
            return ("资料已覆盖全部子问题，撰写报告。" if zh else "All sub-questions are covered; writing the report.",
                    [("researchexecutor_finalize_article", {"title": f"Research report: {topic}", "content": content,
                                                            "sources": sources})])
        q = plan[len(searches)]
        return (("检索：" if zh else "Searching: ") + q, [("searchenhancedqa_run", {"query": q})])
    # a search just ran → record findings from its result and pick the next query
    res = c.last_result("searchenhancedqa_run", "segment") or ""
    urls = re.findall(r"https?://\S+", res)[:3]
    first = next((ln.strip() for ln in res.splitlines() if len(ln.strip()) > 40 and not ln.startswith("http")), "")
    finding = first[:300] or f"No external sources were reachable; summarised prior knowledge about {plan[len(navs)]}."
    nxt = plan[len(navs) + 1] if len(navs) + 1 < len(plan) else ""
    return ("", [("searchnavigator_next_query", {"findings": finding, "sources": urls, "next_query": nxt})])
