"""Color-profile routes: list log→709 profiles, assign one to clips, import a .cube,
and serve a profile's .cube to the player (it applies the LUT live in WebGL so
the proxy itself stays log).

Imports auth + commands + color_profiles + ingest — no server import, no cycle.
"""
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, field_validator

import color_profiles
import commands
from auth import require_scopes

router = APIRouter()


class ColorProfileUpdate(BaseModel):
    color_profile: Optional[str] = None

    @field_validator("color_profile")
    @classmethod
    def _blank_is_null(cls, v):
        v = (v or "").strip()
        return v or None


class ColorProfileBatch(ColorProfileUpdate):
    ids: List[int]


@router.get("/api/color-profiles")
def list_color_profiles(_tok: dict = Depends(require_scopes("videos_read"))):
    return {"profiles": color_profiles.list_profiles(),
            "user_lut_dir": str(color_profiles.USER_LUT_DIR)}


@router.get("/api/color-profiles/{profile_id}/cube", response_class=PlainTextResponse)
def get_cube(profile_id: str, _tok: dict = Depends(require_scopes("videos_read"))):
    path = color_profiles.lut_file_for(profile_id)
    if not path:
        raise HTTPException(404, "找不到這個 LUT")
    with open(path, encoding="utf-8-sig") as fh:
        return PlainTextResponse(fh.read(), headers={"Cache-Control": "private, max-age=3600"})


@router.post("/api/color-profiles/import")
async def import_lut(file: UploadFile = File(...),
                     _tok: dict = Depends(require_scopes("videos_write"))):
    data = await file.read(color_profiles.MAX_USER_LUT_BYTES + 1)
    try:
        pid = color_profiles.save_user_lut(file.filename or "", data)
    except color_profiles.InvalidLUT as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "id": pid}


def _set_one(media_id: int, profile: Optional[str], tok: dict) -> dict:
    try:
        out = commands.set_color_profile(media_id, {"color_profile": profile}, tok)
    except commands.NotFound:
        raise HTTPException(404, "找不到")
    except commands.Invalid as e:
        raise HTTPException(422, str(e))
    return out


@router.patch("/api/media/{media_id}/color-profile")
def update_color_profile(media_id: int, body: ColorProfileUpdate,
                         _tok: dict = Depends(require_scopes("videos_write"))):
    """Set (or clear) a clip's profile and rebuild its poster with it right away."""
    import ingest
    out = _set_one(media_id, body.color_profile, _tok)
    thumb = ingest.regrade_thumbnail(media_id)
    return {"ok": True, **out, "thumbnail_regraded": bool(thumb)}


@router.post("/api/media/color-profile")
def update_color_profile_batch(body: ColorProfileBatch, background: BackgroundTasks,
                               _tok: dict = Depends(require_scopes("videos_write"))):
    """Batch assign (e.g. a whole card shot in the same log mode). The profile is
    written synchronously; posters are rebuilt in the background (≈1 s per 4K clip)."""
    import ingest
    if not body.ids:
        raise HTTPException(422, "ids 不可為空")
    if not color_profiles.is_known(body.color_profile):
        raise HTTPException(422, "未知的色彩設定檔：{0}".format(body.color_profile))
    done, missing = [], []
    for mid in body.ids:
        try:
            _set_one(mid, body.color_profile, _tok)
            done.append(mid)
        except HTTPException as e:
            if e.status_code != 404:
                raise
            missing.append(mid)
    for mid in done:
        background.add_task(ingest.regrade_thumbnail, mid)
    return {"ok": True, "updated": done, "missing": missing, "color_profile": body.color_profile}
