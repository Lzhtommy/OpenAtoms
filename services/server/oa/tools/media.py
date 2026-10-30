"""Multimodal tools (REPORT §2.4 command names): image generation/analysis, PDF & document extraction,
markdown → PDF. Files live in the workspace; every call records an `Assets` (images) or `File` (documents) block:
  {items: [{path, kind: image|pdf|document, preview: ".storage/..." | null, width?, height?, pages?, chars?}],
   summary?}
Real models (anything but `oa-mock*`) use the OpenAI-compatible endpoint (images/generations, vision chat);
`oa-mock` produces deterministic, real artifacts offline (procedural PNG art, pixel statistics).
"""
from __future__ import annotations

import base64
import colorsys
import hashlib
import io
import json
import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from oa.config import settings
from oa.tools.base import ToolContext, ToolResult, tool, truncate_for_llm
from oa.tools.editor import norm_path

log = logging.getLogger("oa.tools.media")

MAX_TEXT = 60_000
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)


def _is_mock(ctx: ToolContext) -> bool:
    return ctx.rc.model.startswith("oa-mock")


def _slug(text: str, default: str = "image") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    return s or default


async def _read(ctx: ToolContext, path: str) -> bytes:
    sb = await ctx.rc.sandbox()
    return await sb.read(norm_path(path))


async def _write(ctx: ToolContext, path: str, data: bytes | str) -> None:
    sb = await ctx.rc.sandbox()
    await sb.write(norm_path(path), data)
    ctx.rc.state["dirty"] = True


def _paths(args: dict[str, Any]) -> list[str]:
    v = args.get("paths") or args.get("path") or []
    return [norm_path(str(p)) for p in ([v] if isinstance(v, str) else v)][:10]


# ---------------------------------------------------------------- image generation
def procedural_png(prompt: str, width: int = 1024, height: int = 640) -> bytes:
    """Deterministic abstract artwork derived from the prompt: gradient, soft orbs and a grid of facets."""
    from PIL import Image, ImageDraw, ImageFilter

    h = hashlib.sha256(prompt.encode()).digest()
    hue = h[0] / 255
    c1 = tuple(int(x * 255) for x in colorsys.hls_to_rgb(hue, 0.55, 0.75))
    c2 = tuple(int(x * 255) for x in colorsys.hls_to_rgb((hue + 0.18) % 1, 0.45, 0.7))
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        for x in range(0, width, 4):
            t = (x / width * 0.6 + y / height * 0.4)
            col = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
            for dx in range(4):
                if x + dx < width:
                    px[x + dx, y] = col
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i in range(6):
        r = 60 + h[i + 2] % 160
        cx, cy = h[i + 8] * width // 255, h[i + 14] * height // 255
        light = tuple(int(x * 255) for x in colorsys.hls_to_rgb((hue + i * 0.07) % 1, 0.8, 0.6))
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*light, 90))
    layer = layer.filter(ImageFilter.GaussianBlur(28))
    img = Image.alpha_composite(img.convert("RGBA"), layer)
    d = ImageDraw.Draw(img)
    step = 64
    for gx in range(0, width, step):
        d.line([(gx, 0), (gx, height)], fill=(255, 255, 255, 18))
    for gy in range(0, height, step):
        d.line([(0, gy), (width, gy)], fill=(255, 255, 255, 18))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()


async def _remote_image(prompt: str, size: str) -> bytes:
    if not settings.llm_api_key:
        raise RuntimeError("no LLM API key configured")
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(settings.llm_base_url.rstrip("/") + "/images/generations",
                         headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                         json={"model": settings.image_model, "prompt": prompt, "size": size, "n": 1,
                               "response_format": "b64_json"})
        r.raise_for_status()
        item = r.json()["data"][0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        img = await c.get(item["url"])
        img.raise_for_status()
        return img.content


async def _asset_block(ctx: ToolContext, command: str, items: list[dict[str, Any]], *, blobs: dict[str, bytes],
                       block_type: str = "Assets", summary: str = "", status: str = "success") -> None:
    async with ctx.record(command, block_type, extra={"paths": [i["path"] for i in items]}) as blk:
        snaps: dict[str, bytes | str] = {"assets.json": b""}
        snaps.update(blobs)
        # first snapshot is the primary file: put the JSON first, patch previews to their storage paths below
        doc_items = []
        for it in items:
            name = PurePosixPath(it["path"]).name
            doc_items.append({**it, "preview_name": name if name in blobs else None})
        doc = json.dumps({"items": doc_items, "summary": summary}, ensure_ascii=False)
        snaps["assets.json"] = doc
        await blk.replace(doc)
        rel = await blk.complete(status=status, snapshots=snaps)
        if rel:  # rewrite preview_name → full .storage path for the replayed block content
            base = rel.rsplit("/", 1)[0]
            final = json.dumps({"items": [{**i, "preview": f"{base}/{i['preview_name']}" if i["preview_name"] else None}
                                          for i in doc_items], "summary": summary}, ensure_ascii=False)
            from oa.db.models import BlockTimeline
            from oa.db.session import session_scope

            async with session_scope() as s:
                tl = await s.get(BlockTimeline, blk.tl_id)
                if tl is not None:
                    tl.content = final
            from oa.core.events import bus

            await bus.emit(ctx.chat_id, "timeline:content", {"index": blk.index, "content": final, "mode": "replace"})


@tool(
    "imagecreator_generate_image",
    "Generate an image from a text prompt and save it into the project (default public/assets/<slug>.png) so the "
    "app can use it as /assets/<slug>.png.",
    {
        "prompt": {"type": "string"},
        "path": {"type": "string", "description": "Workspace path, e.g. public/assets/hero.png", "optional": True},
        "size": {"type": "string", "description": "WIDTHxHEIGHT, default 1024x640", "optional": True},
    },
    block_type="Assets",
)
async def imagecreator_generate_image(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    prompt = str(args["prompt"]).strip()
    path = norm_path(str(args.get("path") or f"public/assets/{_slug(prompt)}.png"))
    if not path.lower().endswith(IMAGE_EXT):
        path += ".png"
    m = re.match(r"^(\d{2,4})x(\d{2,4})$", str(args.get("size") or "1024x640"))
    w, h = (int(m.group(1)), int(m.group(2))) if m else (1024, 640)
    w, h = max(64, min(w, 2048)), max(64, min(h, 2048))
    source = "procedural"
    data: bytes | None = None
    if not _is_mock(ctx):
        try:
            data = await _remote_image(prompt, f"{w}x{h}")
            source = settings.image_model
        except Exception as e:
            log.warning("image generation via provider failed, using procedural art: %s", e)
    if data is None:
        data = procedural_png(prompt, w, h)
    await _write(ctx, path, data)
    item = {"path": path, "kind": "image", "prompt": prompt, "width": w, "height": h, "source": source,
            "url": "/" + path.removeprefix("public/") if path.startswith("public/") else None}
    await _asset_block(ctx, "imagecreator_generate_image", [item], blobs={PurePosixPath(path).name: data})
    use = f" Reference it in code as `{item['url']}`." if item["url"] else ""
    return ToolResult(f"Image saved to {path} ({w}x{h}, {len(data) // 1024} KB, {source}).{use}")


# ---------------------------------------------------------------- image analysis
def describe_image(data: bytes) -> dict[str, Any]:
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    fmt, (w, h), mode = img.format, img.size, img.mode
    small = img.convert("RGB").resize((64, 64))
    pal = small.quantize(colors=5).convert("RGB")
    counts = sorted(pal.getcolors(64 * 64) or [], reverse=True)
    colors = [{"hex": "#{:02x}{:02x}{:02x}".format(*c), "share": round(n / (64 * 64), 2)} for n, c in counts[:5]]
    from PIL import ImageStat

    lum = ImageStat.Stat(small.convert("L")).mean[0] / 255  # ITU-R 601 luma
    return {"format": fmt, "width": w, "height": h, "mode": mode, "orientation":
            "landscape" if w > h else "portrait" if h > w else "square", "dominant_colors": colors,
            "brightness": round(lum, 2), "tone": "dark" if lum < 0.35 else "light" if lum > 0.7 else "balanced"}


async def _vision(ctx: ToolContext, question: str, images: list[tuple[str, bytes]]) -> str:
    from oa.llm import get_llm

    parts: list[dict[str, Any]] = [{"type": "text", "text": question}]
    for path, data in images:
        mime = "image/" + (PurePosixPath(path).suffix.lstrip(".").replace("jpg", "jpeg") or "png")
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}})
    res = await get_llm(ctx.rc.model).stream([{"role": "user", "content": parts}])
    return res.text


@tool(
    "imageanalyzer_analyze_images",
    "Analyze images in the project (uploaded attachments, generated assets): dimensions, colors, and a description "
    "answering the optional question.",
    {"paths": {"type": "array", "items": {"type": "string"}}, "question": {"type": "string", "optional": True}},
    block_type="Assets",
)
async def imageanalyzer_analyze_images(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    question = str(args.get("question") or "Describe this image and how it could be used in the app.")
    items, blobs, lines, loaded = [], {}, [], []
    for p in _paths(args):
        try:
            data = await _read(ctx, p)
            info = describe_image(data)
        except Exception as e:
            lines.append(f"{p}: cannot analyze ({type(e).__name__}: {e})")
            continue
        loaded.append((p, data))
        blobs[PurePosixPath(p).name] = data
        items.append({"path": p, "kind": "image", **info})
        cols = ", ".join(f"{c['hex']} ({int(c['share'] * 100)}%)" for c in info["dominant_colors"])
        lines.append(f"{p}: {info['format']} {info['width']}x{info['height']} {info['orientation']}, "
                     f"{info['tone']} tone; dominant colors {cols}")
    if not items:
        return ToolResult("Error: no readable images.\n" + "\n".join(lines), ok=False)
    summary = ""
    if not _is_mock(ctx):
        try:
            summary = await _vision(ctx, question, loaded)
        except Exception as e:
            log.warning("vision analysis failed: %s", e)
    if not summary:
        first = items[0]
        summary = (f"A {first['tone']} {first['orientation']} image dominated by "
                   f"{first['dominant_colors'][0]['hex'] if first['dominant_colors'] else 'mixed colors'}; "
                   "suitable as a hero/background visual. Pick UI accents from its palette.")
    await _asset_block(ctx, "imageanalyzer_analyze_images", items, blobs=blobs, summary=summary)
    return ToolResult(truncate_for_llm("\n".join(lines) + f"\n\nAnalysis: {summary}"))


# ---------------------------------------------------------------- documents
def extract_text(name: str, data: bytes) -> tuple[str, dict[str, Any]]:
    ext = PurePosixPath(name).suffix.lower()
    if ext == ".pdf" or data[:5] == b"%PDF-":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
        text = "\n\n".join(f"--- page {i} ---\n{t}" for i, t in enumerate(pages, 1) if t)
        return text, {"kind": "pdf", "pages": len(pages)}
    if ext == ".docx":
        import docx

        d = docx.Document(io.BytesIO(data))
        paras = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                paras.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n".join(paras), {"kind": "document", "paragraphs": len(paras)}
    return data.decode("utf-8", "replace"), {"kind": "document"}


def _summarize(text: str) -> str:
    """Extractive summary for offline mode: headings + the first sentences of the densest paragraphs."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) > 40 and not p.startswith("--- page")]
    heads = [ln.strip("# ").strip() for ln in text.splitlines() if ln.startswith("#")][:5]
    top = sorted(paras, key=len, reverse=True)[:3]
    sents = [re.split(r"(?<=[.!?。！？])\s*", p)[0][:240] for p in top]
    out = []
    if heads:
        out.append("Sections: " + "; ".join(heads))
    out += [f"- {s}" for s in sents if s]
    return "\n".join(out) or text[:400]


async def _documents(ctx: ToolContext, command: str, args: dict[str, Any], *, summarize: bool) -> ToolResult:
    question = str(args.get("question") or "")
    items, parts, errors = [], [], []
    for p in _paths(args):
        try:
            text, meta = extract_text(p, await _read(ctx, p))
        except Exception as e:
            errors.append(f"{p}: {type(e).__name__}: {e}")
            continue
        text = text[:MAX_TEXT]
        items.append({"path": p, **meta, "chars": len(text), "excerpt": text[:1500]})
        parts.append((p, text))
    if not items:
        return ToolResult("Error: no readable documents.\n" + "\n".join(errors), ok=False)
    summary = ""
    if summarize:
        joined = "\n\n".join(f"# {p}\n{t}" for p, t in parts)
        if not _is_mock(ctx):
            try:
                from oa.llm import complete

                res = await complete([{"role": "user", "content":
                                       f"{question or 'Summarize the key points of these documents.'}\n\n{joined[:40000]}"}],
                                     model=ctx.rc.model)
                summary = res.text
            except Exception as e:
                log.warning("document summary failed: %s", e)
        summary = summary or "\n\n".join(f"{p}:\n{_summarize(t)}" for p, t in parts)
    await _asset_block(ctx, command, items, blobs={}, block_type="File", summary=summary,
                       status="success" if not errors else "failed")
    body = "\n\n".join(f"## {p}\n{t}" for p, t in parts)
    head = f"Summary:\n{summary}\n\n" if summary else ""
    tail = ("\n\nErrors:\n" + "\n".join(errors)) if errors else ""
    return ToolResult(truncate_for_llm(head + body + tail, 12000))


@tool(
    "pdfanalyzer_analyze_pdfs",
    "Read PDF files from the project and summarise them (optionally answering a question about them).",
    {"paths": {"type": "array", "items": {"type": "string"}}, "question": {"type": "string", "optional": True}},
    block_type="File",
)
async def pdfanalyzer_analyze_pdfs(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    return await _documents(ctx, "pdfanalyzer_analyze_pdfs", args, summarize=True)


@tool(
    "documentextractor_extract_documents",
    "Extract the plain text of documents in the project (PDF, DOCX, TXT, MD, CSV, JSON).",
    {"paths": {"type": "array", "items": {"type": "string"}}},
    block_type="File",
)
async def documentextractor_extract_documents(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    return await _documents(ctx, "documentextractor_extract_documents", args, summarize=False)


# ---------------------------------------------------------------- markdown → PDF
def _font_path() -> str | None:
    return next((f for f in FONT_CANDIDATES if Path(f).exists()), None)


def _inline(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


def markdown_to_pdf(md: str, title: str = "") -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=18)
    pdf.set_margins(18, 18, 18)
    font = _font_path()
    if font:
        pdf.add_font("Body", "", font)
        family, unicode_ok = "Body", True
    else:
        family, unicode_ok = "Helvetica", False
    pdf.add_page()
    if title:
        pdf.set_title(title)

    def txt(s: str) -> str:
        s = _inline(s)
        return s if unicode_ok else s.encode("latin-1", "replace").decode("latin-1")

    sizes = {1: 20, 2: 16, 3: 13}
    in_code = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            pdf.set_font(family, size=9)
            pdf.set_fill_color(245, 245, 245)
            pdf.multi_cell(0, 5, txt(line) or " ", fill=True, new_x="LMARGIN", new_y="NEXT")
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            pdf.ln(2)
            pdf.set_font(family, size=sizes[len(m.group(1))])
            pdf.multi_cell(0, sizes[len(m.group(1))] * 0.55, txt(m.group(2)), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
            continue
        if not line.strip():
            pdf.ln(3)
            continue
        pdf.set_font(family, size=11)
        if re.match(r"^\s*([-*]|\d+\.)\s+", line):
            bullet = "•" if unicode_ok else "-"
            body = re.sub(r"^\s*([-*]|\d+\.)\s+", "", line)
            pdf.multi_cell(0, 6, txt(f"  {bullet} " + body.replace("**", "")), new_x="LMARGIN", new_y="NEXT")
        elif line.startswith("|"):
            if re.match(r"^\|[\s:|-]+\|$", line):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            pdf.set_font(family, size=10)
            pdf.multi_cell(0, 5.5, txt("  |  ".join(cells)), new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.multi_cell(0, 6, txt(line.replace("**", "")), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


@tool(
    "markdownpdfcreator_convert_file",
    "Convert a markdown file in the project to a PDF document (e.g. a report or spec for download).",
    {"path": {"type": "string", "description": "Markdown source, e.g. docs/prd.md"},
     "output_path": {"type": "string", "optional": True}},
    block_type="File",
)
async def markdownpdfcreator_convert_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    src = norm_path(str(args["path"]))
    out = norm_path(str(args.get("output_path") or re.sub(r"\.(md|markdown|txt)$", "", src) + ".pdf"))
    try:
        md = (await _read(ctx, src)).decode("utf-8", "replace")
    except Exception as e:
        return ToolResult(f"Error: cannot read {src}: {e}", ok=False)
    title = next((ln.lstrip("# ").strip() for ln in md.splitlines() if ln.startswith("#")), PurePosixPath(src).stem)
    data = markdown_to_pdf(md, title)
    await _write(ctx, out, data)
    from pypdf import PdfReader

    pages = len(PdfReader(io.BytesIO(data)).pages)
    item = {"path": out, "kind": "pdf", "pages": pages, "source": src, "bytes": len(data)}
    await _asset_block(ctx, "markdownpdfcreator_convert_file", [item], blobs={PurePosixPath(out).name: data},
                       block_type="File", summary=f"{src} → {out}")
    return ToolResult(f"PDF saved to {out} ({pages} page(s), {len(data) // 1024} KB).")
