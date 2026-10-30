"""Deep research (Iris), SEO (Sarah) and ads (Adrian) tools.

Research block: searchnavigator_next_query / researchexecutor_finalize_article → JSON
  {question, queries:[...], findings:[{query, summary, sources:[url]}], report?: path}
Growth block: seo_audit → {url, score, checks:[{id, label, ok, detail, weight}], issues:[...]}
              ads_generate_campaign → {objective, audiences, channels, ad_groups, budget, kpis, path}
"""
from __future__ import annotations

import html as htmllib
import json
import re
from pathlib import PurePosixPath
from typing import Any

import httpx

from oa.tools.base import ToolContext, ToolResult, tool, truncate_for_llm
from oa.tools.editor import norm_path

REPORT_PATH = "docs/research/report.md"
CAMPAIGN_PATH = "docs/ads/campaign.md"


def _research(ctx: ToolContext) -> dict[str, Any]:
    st = ctx.rc.state.setdefault("research", {})
    st.setdefault("queries", [])
    st.setdefault("findings", [])
    return st


async def _write(ctx: ToolContext, path: str, content: str | bytes) -> None:
    sb = await ctx.rc.sandbox()
    await sb.write(path, content)
    ctx.rc.state["dirty"] = True


# ---------------------------------------------------------------- Iris
@tool(
    "searchnavigator_next_query",
    "Record what you learned from the last search/source and decide the next search query. "
    "Call after reading sources for one sub-question.",
    {
        "findings": {"type": "string", "description": "Concise findings from the sources you just read"},
        "sources": {"type": "array", "items": {"type": "string"}, "description": "Source URLs backing the findings",
                    "optional": True},
        "next_query": {"type": "string", "description": "Next search query, or empty when research is complete",
                       "optional": True},
    },
    block_type="Research",
)
async def searchnavigator_next_query(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    st = _research(ctx)
    last_q = st["queries"][-1] if st["queries"] else ""
    st["findings"].append({"query": last_q, "summary": str(args["findings"]).strip(),
                           "sources": [str(s) for s in args.get("sources") or []][:10]})
    nxt = str(args.get("next_query") or "").strip()
    if nxt:
        st["queries"].append(nxt)
    doc = json.dumps(st, ensure_ascii=False)
    async with ctx.record("searchnavigator_next_query", "Research", extra={"next_query": nxt}) as blk:
        await blk.replace(doc)
        await blk.complete(snapshots={"research.json": doc})
    n = len(st["findings"])
    if nxt:
        return ToolResult(f"Recorded finding #{n}. Next: search for {nxt!r} with searchenhancedqa_run.")
    return ToolResult(f"Recorded finding #{n}. Research complete — call researchexecutor_finalize_article.")


def _citations(sources: list[str]) -> str:
    return "\n".join(f"[{i}] {u}" for i, u in enumerate(sources, 1))


@tool(
    "researchexecutor_finalize_article",
    f"Write the final research report (markdown with numbered citations) to {REPORT_PATH}.",
    {
        "title": {"type": "string"},
        "content": {"type": "string", "description": "Markdown body: summary, sections, conclusion; cite as [n]"},
        "sources": {"type": "array", "items": {"type": "string"}, "description": "Ordered source URLs for [n]",
                    "optional": True},
        "path": {"type": "string", "optional": True},
    },
    block_type="Research",
)
async def researchexecutor_finalize_article(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    st = _research(ctx)
    sources = [str(s) for s in args.get("sources") or []]
    if not sources:  # fall back to every source recorded during navigation, de-duplicated in order
        sources = list(dict.fromkeys(u for f in st["findings"] for u in f.get("sources", [])))
    path = norm_path(str(args.get("path") or REPORT_PATH))
    body = str(args["content"]).strip()
    report = f"# {str(args['title']).strip()}\n\n{body}\n"
    if sources and "## Sources" not in body and "## 参考" not in body:
        report += f"\n## Sources\n\n{_citations(sources)}\n"
    await _write(ctx, path, report)
    st["report"] = path
    doc = json.dumps({**st, "report_markdown": report}, ensure_ascii=False)
    async with ctx.record("researchexecutor_finalize_article", "Research", src_path=f"/workspace/{path}",
                          extra={"path": path}) as blk:
        await blk.replace(doc)
        await blk.complete(snapshots={"report.md": report, "research.json": doc})
    cited = len(set(re.findall(r"\[(\d+)\]", body)))
    return ToolResult(f"Report saved to {path} ({len(report)} chars, {len(sources)} sources, {cited} cited).")


# ---------------------------------------------------------------- Sarah
_TAG = re.compile(r"<(/?)([a-zA-Z0-9]+)([^>]*)>", re.S)


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf'\b{name}\s*=\s*("([^"]*)"|\'([^\']*)\'|\{{([^}}]*)\}})', attrs, re.S)
    if not m:
        return None
    return next(g for g in (m.group(2), m.group(3), m.group(4)) if g is not None)


def audit_html(doc: str, sources: dict[str, str] | None = None) -> dict[str, Any]:
    """Lighthouse-style SEO checks over the served HTML plus (for SPAs) the JSX sources."""
    sources = sources or {}
    head = doc.split("</head>", 1)[0] if "</head>" in doc else doc
    title_m = re.search(r"<title[^>]*>(.*?)</title>", head, re.S | re.I)
    title = htmllib.unescape(title_m.group(1).strip()) if title_m else ""
    metas = {(_attr(a, "name") or _attr(a, "property") or "").lower(): _attr(a, "content") or ""
             for _, t, a in _TAG.findall(head) if t.lower() == "meta"}
    html_tag = re.search(r"<html([^>]*)>", doc, re.I)
    lang = _attr(html_tag.group(1), "lang") if html_tag else None
    canonical = re.search(r'<link[^>]+rel=["\']canonical["\']', head, re.I)

    markup = doc + "\n".join(sources.values())
    h1 = len(re.findall(r"<h1[\s>]", markup, re.I))
    imgs = [(a, f) for f, src in [("index.html", doc), *sources.items()] for _, t, a in _TAG.findall(src)
            if t.lower() == "img"]
    no_alt = [f for a, f in imgs if not (_attr(a, "alt") or "").strip()]
    desc = metas.get("description", "")

    checks = [
        ("title", "Document has a descriptive <title>", 10 <= len(title) <= 65 and title.lower() not in ("app", "vite + react + ts"),
         f"title={title!r} ({len(title)} chars; aim for 10–65)", 15),
        ("meta-description", "Meta description present", 50 <= len(desc) <= 160,
         f"{len(desc)} chars (aim for 50–160)" if desc else "missing", 15),
        ("viewport", "Mobile viewport meta tag", "viewport" in metas, metas.get("viewport") or "missing", 10),
        ("lang", "<html> has a lang attribute", bool(lang), lang or "missing", 10),
        ("h1", "Exactly one <h1> heading", h1 == 1, f"{h1} found", 15),
        ("img-alt", "Images have alt text", not no_alt, f"{len(no_alt)} of {len(imgs)} images missing alt", 10),
        ("og", "Open Graph title/description", bool(metas.get("og:title")) and bool(metas.get("og:description")),
         "present" if metas.get("og:title") else "missing og:title / og:description", 10),
        ("canonical", "Canonical link", bool(canonical), "present" if canonical else "missing", 5),
        ("robots", "Page is indexable", "noindex" not in metas.get("robots", ""), metas.get("robots") or "default (index)", 10),
    ]
    total = sum(w for *_, w in checks)
    score = round(100 * sum(w for _, _, ok, _, w in checks if ok) / total)
    out = [{"id": i, "label": label, "ok": ok, "detail": detail, "weight": w} for i, label, ok, detail, w in checks]
    return {"score": score, "title": title, "description": desc, "checks": out,
            "issues": [f"{c['label']}: {c['detail']}" for c in out if not c["ok"]]}


async def _source_markup(ctx: ToolContext) -> dict[str, str]:
    """JSX/TSX sources (SPAs render headings/images client-side, so static HTML alone undercounts)."""
    sb = await ctx.rc.sandbox()
    out: dict[str, str] = {}
    try:
        entries = await sb.list("src", depth=4)
    except Exception:
        return out
    for e in entries:
        p = e.get("path", "")
        if e.get("type") == "file" and PurePosixPath(p).suffix in (".tsx", ".jsx", ".html") and len(out) < 60:
            try:
                out[p] = (await sb.read(p)).decode("utf-8", "replace")
            except Exception:
                continue
    return out


@tool(
    "seo_audit",
    "Audit the app for SEO (title, meta description, viewport, lang, single h1, image alt, Open Graph, canonical, "
    "indexability) and return a 0–100 score with the failing checks.",
    {"path": {"type": "string", "description": "Route to audit, default /", "optional": True}},
    block_type="Growth",
)
async def seo_audit(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    route = "/" + str(args.get("path") or "/").lstrip("/")
    sb = await ctx.rc.sandbox()
    doc = ""
    upstream = await ctx.rc.preview_upstream()
    if upstream:
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False) as c:
                r = await c.get(upstream.rstrip("/") + route)
                if r.status_code < 400:
                    doc = r.text
        except httpx.HTTPError:
            doc = ""
    if not doc:  # dev server unavailable: audit the source entry document
        try:
            doc = (await sb.read("index.html")).decode("utf-8", "replace")
        except Exception:
            doc = ""
    res = audit_html(doc, await _source_markup(ctx))
    res["url"] = route
    runs = ctx.rc.state.setdefault("seo_scores", [])
    runs.append(res["score"])
    res["history"] = list(runs)
    out = json.dumps(res, ensure_ascii=False)
    async with ctx.record("seo_audit", "Growth", extra={"kind": "seo", "score": res["score"]}) as blk:
        await blk.replace(out)
        await blk.complete(snapshots={"seo.json": out})
    fails = [f"- FAIL {i}" for i in res["issues"]] or ["- all checks pass"]
    return ToolResult("\n".join([f"SEO score: {res['score']}/100", *fails]))


# ---------------------------------------------------------------- Adrian
def _clip(items: list[Any], limit: int) -> tuple[list[str], list[str]]:
    ok, warn = [], []
    for x in items:
        s = str(x).strip()
        if len(s) > limit:
            warn.append(f"{s!r} exceeds {limit} chars; truncated")
            s = s[: limit - 1].rstrip() + "…"
        ok.append(s)
    return ok, warn


def render_campaign(c: dict[str, Any]) -> str:
    lines = [f"# Ad campaign — {c.get('product') or 'Launch'}", "", f"**Objective:** {c['objective']}", ""]
    if c.get("audiences"):
        lines += ["## Target audiences", *[f"- {a}" for a in c["audiences"]], ""]
    if c.get("channels"):
        lines += ["## Channels", *[f"- {ch}" for ch in c["channels"]], ""]
    lines += ["## Ad groups", ""]
    for g in c["ad_groups"]:
        lines += [f"### {g['name']}", "", "| Headlines (≤30) | Descriptions (≤90) |", "|---|---|"]
        rows = max(len(g["headlines"]), len(g["descriptions"]))
        for i in range(rows):
            h = g["headlines"][i] if i < len(g["headlines"]) else ""
            d = g["descriptions"][i] if i < len(g["descriptions"]) else ""
            lines.append(f"| {h} | {d} |")
        if g.get("keywords"):
            lines += ["", "**Keywords:** " + ", ".join(f"`{k}`" for k in g["keywords"])]
        lines.append("")
    b = c.get("budget") or {}
    if b:
        lines += ["## Budget", f"- Daily: {b.get('daily', 'n/a')}"]
        lines += [f"- {k}: {v}" for k, v in (b.get("split") or {}).items()]
        lines.append("")
    if c.get("kpis"):
        lines += ["## KPIs", *[f"- {k}" for k in c["kpis"]], ""]
    return "\n".join(lines)


@tool(
    "ads_generate_campaign",
    f"Create a structured ad campaign plan and save it to {CAMPAIGN_PATH}.",
    {
        "product": {"type": "string", "optional": True},
        "objective": {"type": "string"},
        "audiences": {"type": "array", "items": {"type": "string"}, "optional": True},
        "channels": {"type": "array", "items": {"type": "string"}, "optional": True},
        "ad_groups": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "headlines": {"type": "array", "items": {"type": "string"}},
            "descriptions": {"type": "array", "items": {"type": "string"}},
            "keywords": {"type": "array", "items": {"type": "string"}},
        }, "required": ["name", "headlines", "descriptions"]}},
        "budget": {"type": "object", "optional": True,
                   "properties": {"daily": {"type": "string"}, "split": {"type": "object"}}},
        "kpis": {"type": "array", "items": {"type": "string"}, "optional": True},
    },
    block_type="Growth",
)
async def ads_generate_campaign(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    groups, warnings = [], []
    for g in args.get("ad_groups") or []:
        if not isinstance(g, dict) or not g.get("name"):
            continue
        hs, w1 = _clip(g.get("headlines") or [], 30)
        ds, w2 = _clip(g.get("descriptions") or [], 90)
        warnings += w1 + w2
        groups.append({"name": str(g["name"]), "headlines": hs, "descriptions": ds,
                       "keywords": [str(k) for k in g.get("keywords") or []]})
    if not groups:
        return ToolResult("Error: provide at least one ad group with headlines and descriptions.", ok=False)
    camp = {
        "product": str(args.get("product") or ""), "objective": str(args["objective"]),
        "audiences": [str(a) for a in args.get("audiences") or []],
        "channels": [str(c) for c in args.get("channels") or []],
        "ad_groups": groups, "budget": args.get("budget") or {}, "kpis": [str(k) for k in args.get("kpis") or []],
    }
    md = render_campaign(camp)
    await _write(ctx, CAMPAIGN_PATH, md)
    camp["path"] = CAMPAIGN_PATH
    camp["warnings"] = warnings
    doc = json.dumps(camp, ensure_ascii=False)
    async with ctx.record("ads_generate_campaign", "Growth", src_path=f"/workspace/{CAMPAIGN_PATH}",
                          extra={"kind": "ads", "path": CAMPAIGN_PATH}) as blk:
        await blk.replace(doc)
        await blk.complete(snapshots={"campaign.json": doc, "campaign.md": md})
    note = ("\nWarnings:\n" + "\n".join(f"- {w}" for w in warnings)) if warnings else ""
    return ToolResult(truncate_for_llm(f"Campaign saved to {CAMPAIGN_PATH} with {len(groups)} ad groups.{note}"))
