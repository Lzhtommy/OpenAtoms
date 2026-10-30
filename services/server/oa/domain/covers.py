"""Version cover images: render a built version (dist/) in headless Chromium and store
`covers/{chat_id}/{version}.png`. Served by GET /api/v1/covers/{chat_id}/{version}.png?sig=… so plain
<img> tags work (no Authorization header); the signature binds the URL to that chat+version."""
from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import hmac
import http.server
import logging
import socket
import threading
from pathlib import Path

from oa.config import settings
from oa.core.storage import get_storage

log = logging.getLogger("oa.covers")

VIEWPORT = {"width": 1280, "height": 800}
RENDER_TIMEOUT = 45.0


def cover_key(chat_id: str, version: str) -> str:
    return f"covers/{chat_id}/{version}.png"


def cover_sig(chat_id: str, version: str) -> str:
    key = (settings.secret_key + settings.jwt_secret).encode()
    return hmac.new(key, f"cover:{chat_id}:{version}".encode(), hashlib.sha256).hexdigest()[:24]


def verify_cover_sig(chat_id: str, version: str, sig: str) -> bool:
    return hmac.compare_digest(cover_sig(chat_id, version), sig or "")


def cover_url(chat_id: str, version: str) -> str:
    return f"/api/v1/covers/{chat_id}/{version}.png?sig={cover_sig(chat_id, version)}"


class _SpaHandler(http.server.SimpleHTTPRequestHandler):
    """Static handler with SPA fallback to index.html; silent."""

    def send_head(self):
        path = Path(self.translate_path(self.path))
        if not path.exists() and "." not in path.name:
            self.path = "/index.html"
        return super().send_head()

    def log_message(self, *args) -> None:
        pass


@contextlib.contextmanager
def _serve(root: Path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(_SpaHandler, directory=str(root)))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def render_dist(dist: Path) -> bytes | None:
    """Screenshot dist/index.html served over HTTP (absolute /assets paths need a real origin)."""
    from oa.tools.web import _pw, playwright_available

    if not playwright_available() or not (dist / "index.html").is_file():
        return None
    with _serve(dist) as url:
        browser = await _pw.browser()
        ctx = await browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
        try:
            page = await ctx.new_page()
            await page.goto(url, wait_until="load", timeout=30000)
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=5000)
            await asyncio.sleep(0.5)
            return await page.screenshot(type="png")
        finally:
            await ctx.close()


async def make_cover(chat_id: str, version: str, dist: Path) -> str | None:
    """Render + store the cover; returns the cover URL or None (never raises)."""
    try:
        png = await asyncio.wait_for(render_dist(dist), RENDER_TIMEOUT)
    except Exception as e:
        log.warning("cover render failed for %s %s: %s", chat_id, version, e)
        return None
    if not png:
        return None
    await get_storage().put_bytes(cover_key(chat_id, version), png, "image/png")
    return cover_url(chat_id, version)
