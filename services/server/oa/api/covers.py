"""Public, signed version cover images (used by version history, My Projects and Discover cards)."""
from __future__ import annotations

import re

from fastapi import APIRouter, Query
from fastapi.responses import Response

from oa.core.envelope import ApiError
from oa.core.storage import get_storage
from oa.domain.covers import cover_key, verify_cover_sig

router = APIRouter(tags=["covers"])


@router.get("/covers/{chat_id}/{version}.png")
async def get_cover(chat_id: str, version: str, sig: str = Query("")):
    if not re.fullmatch(r"[0-9a-f]{32}", chat_id) or not re.fullmatch(r"v\d+", version) \
            or not verify_cover_sig(chat_id, version, sig):
        raise ApiError(4404, "Not found")
    data = await get_storage().get_bytes(cover_key(chat_id, version))
    if data is None:
        raise ApiError(4404, "Not found")
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000, immutable"})
