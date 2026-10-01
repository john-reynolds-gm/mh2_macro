"""
seq_api.py -- FastAPI app + router for the Grade Sequencing Tool (v1).

Contract: docs/seq_v1_contract.md §4.  Every route is ONE statement,
`return _run(seq_service.<fn>, CTX, ...)`; there is no SQL and no store or
read-module import here (mh2/seq_service.py composes them).  Auth is mirrored
from review_api.py: HTTP Basic, per-writer credentials in MH2_AUTH_USERS, off
when unset; the gate is app-level, so the static page is authenticated too.

Static files are served by explicit FileResponse routes from seq_static/.
Mounting a sub-application is deliberately not used: a mounted app skips the
app-level auth gate.

Run: uvicorn seq_api:app --reload
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field

from mh2 import seq_auth, seq_service

_security = HTTPBasic(auto_error=False)

STATIC_DIR = Path(__file__).parent / "seq_static"
ASSET_WHITELIST = {"app.js", "app.css"}
_ASSET_TYPES = {"app.js": "text/javascript", "app.css": "text/css"}

CTX = seq_service.Ctx.from_config()


# ----------------------------------------------------------------------- auth

def require_writer(
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> str:
    user = seq_auth.check_basic(
        credentials.username if credentials else None,
        credentials.password if credentials else None)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required",
                            headers={"WWW-Authenticate": "Basic"})
    return user


def current_writer(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> str:
    return seq_auth.resolve_writer(
        credentials.username if credentials else None,
        request.headers.get(seq_auth.DEV_HEADER))[0]


app = FastAPI(title="MH2 Grade Sequencing API", dependencies=[Depends(require_writer)])
router = APIRouter()


def _run(fn, ctx, *args, **kwargs):
    try:
        return fn(ctx, *args, **kwargs)
    except seq_service.SeqError as e:
        raise HTTPException(status_code=e.status,
                            detail={"error": e.code, "message": e.message, **e.extra})


# --------------------------------------------------------------------- bodies
# Client-sent placed_by / *_by / *_at are dropped (extra="ignore"); the stored
# identity is always the authenticated writer.

class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CreateSequenceBody(_Body):
    grade: str
    title: str
    note: str | None = None


class UpdateSequenceBody(_Body):
    expected_rev: int
    title: str | None = None
    owner: str | None = None
    note: str | None = None
    archived: bool | None = None


class CreateModuleBody(_Body):
    expected_rev: int
    title: str
    note: str | None = None


class UpdateModuleBody(_Body):
    expected_rev: int
    title: str | None = None
    note: str | None = None


class MoveBody(_Body):
    expected_rev: int
    direction: str


class UpdateSlotBody(_Body):
    expected_rev: int
    label: str | None = None


class MoveSlotBody(_Body):
    expected_rev: int
    direction: str | None = None
    to_module_id: int | None = None


class MergeSlotBody(_Body):
    expected_rev: int
    into_slot_id: int


class CreateSlotGroupBody(_Body):
    expected_rev: int
    sequence_id: int
    module_id: int
    source_keys: list[str] = Field(min_length=1, max_length=4)
    confirm_off_grade: bool = False
    differentiation_notes: dict[str, str | None] | None = None


class PlaceBody(_Body):
    expected_rev: int
    sequence_id: int
    module_id: int
    source_key: str
    slot_id: int | None = None
    after_slot_id: int | None = None
    differentiation_note: str | None = None
    confirm_off_grade: bool = False


class UpdatePlacementBody(_Body):
    expected_rev: int
    calibration: str | None = None
    period_estimate: float | None = None
    differentiation_note: str | None = None


class CoPlaceBody(_Body):
    expected_rev: int
    target_slot_id: int


class RevBody(_Body):
    expected_rev: int


class ReattachBody(_Body):
    expected_rev: int
    source_key: str


class CreateViewBody(_Body):
    grade: str
    name: str
    state: dict


class UpdateViewBody(_Body):
    name: str | None = None
    state: dict | None = None


# ------------------------------------------------------------------ whoami

@router.get("/api/seq/whoami")
def r_whoami(request: Request,
             credentials: HTTPBasicCredentials | None = Depends(_security)):
    user, source = seq_auth.resolve_writer(
        credentials.username if credentials else None,
        request.headers.get(seq_auth.DEV_HEADER))
    return {"user": user, "auth_enabled": seq_auth.auth_enabled(), "source": source}


# ------------------------------------------------------------------- reads

@router.get("/api/seq/grades")
def r_grades():
    return _run(seq_service.grades, CTX)


@router.get("/api/seq/slice")
def r_get_slice(grade: str):
    return _run(seq_service.get_slice, CTX, grade)


@router.get("/api/seq/node/{source_key}")
def r_get_node(source_key: str, grade: str):
    return _run(seq_service.get_node, CTX, source_key, grade)


@router.get("/api/seq/compare")
def r_get_compare(keys: str, grade: str):
    return _run(seq_service.get_compare, CTX, [k for k in keys.split(",") if k], grade)


@router.get("/api/seq/sequence")
def r_get_sequence(grade: str):
    return _run(seq_service.get_sequence, CTX, grade)


@router.get("/api/seq/guardrail")
def r_get_guardrail(grade: str):
    return _run(seq_service.get_guardrail, CTX, grade)


@router.get("/api/seq/attention")
def r_get_attention(grade: str):
    return _run(seq_service.get_attention, CTX, grade)


@router.get("/api/seq/sequences/{sequence_id}/events")
def r_get_events(sequence_id: int, limit: int = Query(50, ge=1, le=200),
                 before: int | None = None):
    return _run(seq_service.get_events, CTX, sequence_id, limit, before)


# ------------------------------------------------------------------ writes

@router.post("/api/seq/sequences")
def r_create_sequence(body: CreateSequenceBody, writer: str = Depends(current_writer)):
    return _run(seq_service.create_sequence, CTX, writer, body.grade, body.title, body.note)


@router.patch("/api/seq/sequences/{sequence_id}")
def r_update_sequence(sequence_id: int, body: UpdateSequenceBody,
                      writer: str = Depends(current_writer)):
    return _run(seq_service.update_sequence, CTX, writer, sequence_id, body.expected_rev,
                body.model_dump(exclude_unset=True, exclude={"expected_rev"}))


@router.post("/api/seq/sequences/{sequence_id}/modules")
def r_create_module(sequence_id: int, body: CreateModuleBody,
                    writer: str = Depends(current_writer)):
    return _run(seq_service.create_module, CTX, writer, sequence_id, body.expected_rev,
                body.title, body.note)


@router.patch("/api/seq/modules/{module_id}")
def r_update_module(module_id: int, body: UpdateModuleBody,
                    writer: str = Depends(current_writer)):
    return _run(seq_service.update_module, CTX, writer, module_id, body.expected_rev,
                body.model_dump(exclude_unset=True, exclude={"expected_rev"}))


@router.post("/api/seq/modules/{module_id}/move")
def r_move_module(module_id: int, body: MoveBody, writer: str = Depends(current_writer)):
    return _run(seq_service.move_module, CTX, writer, module_id, body.expected_rev,
                body.direction)


@router.delete("/api/seq/modules/{module_id}")
def r_remove_module(module_id: int, expected_rev: int,
                    writer: str = Depends(current_writer)):
    return _run(seq_service.remove_module, CTX, writer, module_id, expected_rev)


@router.patch("/api/seq/slots/{slot_id}")
def r_update_slot(slot_id: int, body: UpdateSlotBody, writer: str = Depends(current_writer)):
    return _run(seq_service.update_slot, CTX, writer, slot_id, body.expected_rev, body.label)


@router.post("/api/seq/slots/{slot_id}/move")
def r_move_slot(slot_id: int, body: MoveSlotBody, writer: str = Depends(current_writer)):
    return _run(seq_service.move_slot, CTX, writer, slot_id, body.expected_rev,
                body.direction, body.to_module_id)


@router.post("/api/seq/slots/{slot_id}/merge")
def r_merge_slot(slot_id: int, body: MergeSlotBody, writer: str = Depends(current_writer)):
    return _run(seq_service.merge_slot, CTX, writer, slot_id, body.expected_rev,
                body.into_slot_id)


@router.post("/api/seq/slots")
def r_create_slot_group(body: CreateSlotGroupBody, writer: str = Depends(current_writer)):
    return _run(seq_service.create_slot_group, CTX, writer, body.sequence_id,
                body.expected_rev, body.module_id, body.source_keys,
                body.confirm_off_grade, body.differentiation_notes)


@router.post("/api/seq/placements")
def r_place(body: PlaceBody, writer: str = Depends(current_writer)):
    return _run(seq_service.place, CTX, writer, body.sequence_id, body.expected_rev,
                body.module_id, body.source_key, body.slot_id, body.after_slot_id,
                body.differentiation_note, body.confirm_off_grade)


@router.patch("/api/seq/placements/{placement_id}")
def r_update_placement(placement_id: int, body: UpdatePlacementBody,
                       writer: str = Depends(current_writer)):
    return _run(seq_service.update_placement, CTX, writer, placement_id, body.expected_rev,
                body.model_dump(exclude_unset=True, exclude={"expected_rev"}))


@router.post("/api/seq/placements/{placement_id}/co-place")
def r_co_place(placement_id: int, body: CoPlaceBody, writer: str = Depends(current_writer)):
    return _run(seq_service.co_place, CTX, writer, placement_id, body.expected_rev,
                body.target_slot_id)


@router.post("/api/seq/placements/{placement_id}/ungroup")
def r_ungroup(placement_id: int, body: RevBody, writer: str = Depends(current_writer)):
    return _run(seq_service.ungroup, CTX, writer, placement_id, body.expected_rev)


@router.delete("/api/seq/placements/{placement_id}")
def r_remove_placement(placement_id: int, expected_rev: int, reason: str | None = None,
                       writer: str = Depends(current_writer)):
    return _run(seq_service.remove_placement, CTX, writer, placement_id, expected_rev, reason)


@router.post("/api/seq/placements/{placement_id}/reattach")
def r_reattach(placement_id: int, body: ReattachBody, writer: str = Depends(current_writer)):
    return _run(seq_service.reattach, CTX, writer, placement_id, body.expected_rev,
                body.source_key)


@router.post("/api/seq/placements/{placement_id}/acknowledge")
def r_acknowledge(placement_id: int, body: RevBody, writer: str = Depends(current_writer)):
    return _run(seq_service.acknowledge, CTX, writer, placement_id, body.expected_rev)


# ------------------------------------------------------------- saved views

@router.get("/api/seq/views")
def r_list_views(grade: str | None = None, owner: str = Depends(current_writer)):
    return _run(seq_service.list_views, CTX, owner, grade)


@router.post("/api/seq/views")
def r_create_view(body: CreateViewBody, owner: str = Depends(current_writer)):
    return _run(seq_service.create_view, CTX, owner, body.grade, body.name, body.state)


@router.put("/api/seq/views/{view_id}")
def r_update_view(view_id: int, body: UpdateViewBody, owner: str = Depends(current_writer)):
    return _run(seq_service.update_view, CTX, owner, view_id,
                body.model_dump(exclude_unset=True))


@router.delete("/api/seq/views/{view_id}")
def r_delete_view(view_id: int, owner: str = Depends(current_writer)):
    return _run(seq_service.delete_view, CTX, owner, view_id)


# ------------------------------------------------------------- static page

@router.get("/seq")
def r_page_redirect():
    return RedirectResponse(url="seq/", status_code=307)


@router.get("/seq/")
def r_page():
    path = STATIC_DIR / "index.html"
    if not path.is_file():
        raise HTTPException(404, "seq_static/index.html not found")
    return FileResponse(path, media_type="text/html")


@router.get("/seq/assets/{name}")
def r_asset(name: str):
    path = STATIC_DIR / name
    if name not in ASSET_WHITELIST or not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type=_ASSET_TYPES[name])


app.include_router(router)
