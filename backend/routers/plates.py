"""Labelled OCR data, known plates and the plate review queue."""
import os
import io
import csv
import uuid
from datetime import datetime
from typing import Optional, Literal, Dict, Any

from fastapi import APIRouter, HTTPException, Response, Depends
from pydantic import BaseModel

from plate_pipeline import _persist_entry
import plate_pipeline
from pymongo.errors import DuplicateKeyError
import plate_rules
import security


from core import ROLE_ADMIN_MANAGER, ROLE_ADMIN_ONLY, ROLE_ALL, db, logger, now_utc, require_roles

router = APIRouter(prefix="/api")

# ===================== Labelled data (for measuring and retraining OCR) =====================
async def _save_ocr_label(review: dict, final_plate: Optional[str], user: dict) -> None:
    """Every human decision on a review is a labelled example: the plate crop + the true text
    (or None when the reviewer said it isn't a plate). Corrections are the most valuable ones."""
    try:
        await db.ocr_labels.insert_one({
            "id": str(uuid.uuid4()),
            "review_id": review.get("id"),
            "kind": review.get("kind", "plate"),
            "plate": final_plate,
            "model_plate": review.get("plate"),
            "raw": review.get("raw", ""),
            "candidates": review.get("candidates", []),
            "corrected": bool(final_plate) and final_plate != review.get("plate"),
            "camera_id": review.get("camera_id", ""),
            "plate_image": review.get("plate_image", ""),
            "image": review.get("image", ""),
            "seen_at": review.get("seen_at"),
            "labelled_by": user.get("email"),
            "at": now_utc().isoformat(),
        })
    except Exception:
        logger.exception("[labels] could not save label for review %s", review.get("id"))


async def _snapshot_bytes(url: str) -> Optional[bytes]:
    """Bytes of a /api/snapshots/<file> image from local disk or object storage."""
    if not url or not url.startswith("/api/snapshots/"):
        return None
    try:
        return await plate_pipeline.read_snapshot(os.path.basename(url))
    except Exception:
        return None


@router.get("/ocr-labels/stats")
async def ocr_label_stats(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    total = await db.ocr_labels.count_documents({})
    corrected = await db.ocr_labels.count_documents({"corrected": True})
    rejected = await db.ocr_labels.count_documents({"plate": None})
    return {"total": total, "corrected": corrected, "rejected": rejected, "confirmed": total - corrected - rejected}


@router.get("/ocr-labels/export")
async def export_ocr_labels(only_corrected: bool = False, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    """ZIP of plate crops + labels.csv, for benchmark_ocr.py and model fine-tuning."""
    import zipfile
    q: Dict[str, Any] = {"plate_image": {"$ne": ""}}
    if only_corrected:
        q["corrected"] = True
    buf = io.BytesIO()
    rows = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["file", "plate", "model_plate", "raw", "corrected", "kind", "camera_id", "seen_at"])
        async for d in db.ocr_labels.find(q, {"_id": 0}).sort("at", 1):
            data = await _snapshot_bytes(d.get("plate_image", ""))
            if not data:
                continue
            truth = d.get("plate") or "NOTAPLATE"
            fname = f"images/{truth}_{d['id'][:8]}.jpg"  # benchmark_ocr.py reads the plate from the file name
            z.writestr(fname, data)
            w.writerow([fname, d.get("plate") or "", d.get("model_plate") or "", d.get("raw", ""),
                        int(bool(d.get("corrected"))), d.get("kind", "plate"), d.get("camera_id", ""), d.get("seen_at", "")])
            rows += 1
        z.writestr("labels.csv", out.getvalue())
    await security.audit(db, user, "ocr_labels.export", "", rows=rows, only_corrected=only_corrected)
    stamp = now_utc().strftime("%Y%m%d")
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="plate-labels-{stamp}.zip"'})


# ===================== Known plates (demo, test-drive, trade, staff) =====================
class KnownPlateIn(BaseModel):
    plate: str
    label: Optional[str] = ""
    kind: Literal["demo", "test_drive", "trade", "staff", "other"] = "other"


@router.get("/known-plates")
async def list_known_plates(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    return await db.known_plates.find({}, {"_id": 0}).sort("plate", 1).to_list(2000)


@router.post("/known-plates")
async def add_known_plate(payload: KnownPlateIn, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Vehicles the showroom owns or expects. Exact reads of these are trusted more; trade /
    temporary plates listed here are accepted even though they don't follow the standard format."""
    fixed, _ = plate_rules.correct(payload.plate)
    plate = fixed or plate_rules.clean(payload.plate)
    if not 4 <= len(plate) <= 12:
        raise HTTPException(status_code=400, detail="Enter the plate as written (4–12 letters/digits)")
    doc = {"plate": plate, "label": payload.label or "", "kind": payload.kind, "standard": bool(fixed),
           "created_at": now_utc().isoformat(), "created_by": user.get("email")}
    try:
        await db.known_plates.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="That plate is already on the list")
    await security.audit(db, user, "known_plate.add", plate, kind=payload.kind)
    doc.pop("_id", None)
    return doc


@router.delete("/known-plates/{plate}")
async def delete_known_plate(plate: str, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    p = plate_rules.clean(plate)
    r = await db.known_plates.delete_one({"plate": p})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not on the list")
    await security.audit(db, user, "known_plate.delete", p)
    return {"ok": True}


# ===================== Plate review queue =====================
class ReviewAcceptIn(BaseModel):
    plate: str
    # Trade / temporary / foreign plates that don't follow the Indian format.
    nonstandard: bool = False


def _review_public(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


@router.get("/plate-reviews")
async def list_plate_reviews(status: str = "pending", limit: int = 100,
                             user: dict = Depends(require_roles(*ROLE_ALL))):
    q = {} if status == "all" else {"status": status}
    docs = await db.plate_reviews.find(q).sort("created_at", -1).to_list(min(max(limit, 1), 500))
    return [_review_public(d) for d in docs]


@router.get("/plate-reviews/count")
async def count_plate_reviews(user: dict = Depends(require_roles(*ROLE_ALL))):
    return {"pending": await db.plate_reviews.count_documents({"status": "pending"})}


@router.post("/plate-reviews/{rid}/accept")
async def accept_plate_review(rid: str, payload: ReviewAcceptIn, user: dict = Depends(require_roles(*ROLE_ALL))):
    review = await db.plate_reviews.find_one({"id": rid})
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Already {review.get('status')}")
    if payload.nonstandard:
        plate = plate_rules.clean(payload.plate)
        if not 4 <= len(plate) <= 12:
            raise HTTPException(status_code=400, detail="Enter the plate as written (4–12 letters/digits)")
    else:
        plate, _ = plate_rules.correct(payload.plate)
        if not plate:
            raise HTTPException(status_code=400, detail="That isn't a valid Indian number plate (e.g. KA05MG9999 or 22BH1234AA). "
                                                        "For trade or temporary plates tick 'Non-standard plate'.")
    # Claim the review first so two people can't accept it twice.
    claimed = await db.plate_reviews.update_one({"id": rid, "status": "pending"}, {"$set": {"status": "processing"}})
    if claimed.modified_count == 0:
        raise HTTPException(status_code=409, detail="Someone else is handling this review")
    seen_at = datetime.fromisoformat(review["seen_at"]) if review.get("seen_at") else now_utc()
    try:
        result = await _persist_entry(db, review.get("camera_id", ""), plate, b"", plate_jpeg=b"",
                                      confidence="confirmed", at=seen_at, source="review", confirmed=True)
    except Exception:
        await db.plate_reviews.update_one({"id": rid}, {"$set": {"status": "pending"}})
        raise
    # Reuse the review's images on the visit it created / closed.
    sess = result.get("session") or {}
    if sess.get("id") and not result.get("duplicate"):
        prefix = "exit" if result.get("event") == "exit" else "entry"
        img_updates = {k: v for k, v in {f"{prefix}_image": review.get("image"),
                                         f"{prefix}_plate_image": review.get("plate_image")}.items() if v}
        if img_updates:
            await db.visit_sessions.update_one({"id": sess["id"]}, {"$set": img_updates})
    await security.audit(db, user, "review.accept", rid, plate=plate, original=review.get("plate"),
                         event=result.get("event"), duplicate=result.get("duplicate", False))
    await _save_ocr_label(review, plate, user)
    await db.plate_reviews.update_one({"id": rid}, {"$set": {
        "status": "accepted", "final_plate": plate, "result_event": result.get("event"),
        "duplicate": result.get("duplicate", False),
        "resolved_by": user.get("email"), "resolved_at": now_utc().isoformat(),
    }})
    return {"ok": True, "plate": plate, "event": result.get("event"), "duplicate": result.get("duplicate", False),
            "duplicate_reason": result.get("duplicate_reason")}


@router.post("/plate-reviews/{rid}/reject")
async def reject_plate_review(rid: str, user: dict = Depends(require_roles(*ROLE_ALL))):
    r = await db.plate_reviews.update_one({"id": rid, "status": "pending"}, {"$set": {
        "status": "rejected", "resolved_by": user.get("email"), "resolved_at": now_utc().isoformat(),
    }})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Pending review not found")
    await security.audit(db, user, "review.reject", rid)
    review = await db.plate_reviews.find_one({"id": rid}) or {}
    await _save_ocr_label(review, None, user)
    return {"ok": True}

