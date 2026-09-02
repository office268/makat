"""מסכי ניהול מערכת - חוצי ארגונים.

מה שנמצא כאן משפיע על כל הלקוחות במערכת ולא על מוסך בודד, ולכן הכל
מוגן ב-superadmin_required ולא בתפקידים שבתוך הארגון.
"""
import json

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from .. import (activity, fleet_stats, live_lookup, part_columns,
                parts_discovery, seed_catalog, services)
from ..auth import superadmin_required
from ..models import Part, db
from ..taxonomy import all_types, type_name
from ..fleet_import import cancel_job as cancel_fleet_job
from ..fleet_import import run_chunk as run_fleet_chunk
from ..fleet_import import start_job as start_fleet_job
from ..vehicle_catalog import VehicleModel, active_job, latest_job
from ..vehicle_import import cancel_job, run_chunk, start_job

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _payload(job):
    if job is None:
        return {"job": None, "models_in_catalog": VehicleModel.query.count()}
    return {"job": job.to_dict(), "models_in_catalog": VehicleModel.query.count()}


@admin_bp.get("/vehicle-import")
@superadmin_required
def vehicle_import():
    """מסך הייבוא. מציג את ההרצה האחרונה כדי שאפשר יהיה להמשיך אותה."""
    return render_template(
        "admin/vehicle_import.html",
        job=latest_job(),
        models_in_catalog=VehicleModel.query.count(),
    )


@admin_bp.get("/vehicle-import/status")
@superadmin_required
def vehicle_import_status():
    return jsonify(_payload(latest_job()))


@admin_bp.post("/vehicle-import/start")
@superadmin_required
def vehicle_import_start():
    job = start_job(user_id=current_user.id)
    activity.note(summary="ייבוא קטלוג דגמי רכב הופעל", entity_type="job",
                  entity_id=job.id if job else None)
    return jsonify(_payload(job))


@admin_bp.post("/vehicle-import/step")
@superadmin_required
def vehicle_import_step():
    """מנה אחת. הדפדפן קורא לזה שוב ושוב עד שהסטטוס מפסיק להיות running."""
    job = active_job()
    if job is None:
        return jsonify({"error": "אין ייבוא פעיל.", **_payload(latest_job())}), 409
    return jsonify(_payload(run_chunk(job)))


@admin_bp.post("/vehicle-import/cancel")
@superadmin_required
def vehicle_import_cancel():
    activity.note(summary="ייבוא קטלוג דגמי רכב בוטל")
    return jsonify(_payload(cancel_job(active_job())))


# ---------------------------------------------------------------------------
# ספירת הרכבים הפעילים בישראל
# ---------------------------------------------------------------------------


def _fleet_payload(job):
    """מצב ההרצה + מה שמוצג כרגע במסך /stats, לא מה שנבנה ברקע."""
    return {"job": job.to_dict() if job else None, "snapshot": fleet_stats.summary()}


@admin_bp.get("/fleet-stats")
@superadmin_required
def fleet_stats_screen():
    """מסך הספירה. מציג את ההרצה האחרונה כדי שאפשר יהיה להמשיך אותה."""
    return render_template(
        "admin/fleet_stats.html",
        job=fleet_stats.latest_job(),
        snapshot=fleet_stats.summary(),
    )


@admin_bp.get("/fleet-stats/status")
@superadmin_required
def fleet_stats_status():
    return jsonify(_fleet_payload(fleet_stats.latest_job()))


@admin_bp.post("/fleet-stats/start")
@superadmin_required
def fleet_stats_start():
    job = start_fleet_job(user_id=current_user.id)
    activity.note(summary="ספירת הרכבים הפעילים הופעלה", entity_type="job",
                  entity_id=job.id if job else None)
    return jsonify(_fleet_payload(job))


@admin_bp.post("/fleet-stats/step")
@superadmin_required
def fleet_stats_step():
    """מנה אחת. הדפדפן קורא לזה שוב ושוב עד שהסטטוס מפסיק להיות running."""
    job = fleet_stats.active_job()
    if job is None:
        return jsonify({"error": "אין ספירה פעילה.",
                        **_fleet_payload(fleet_stats.latest_job())}), 409
    return jsonify(_fleet_payload(run_fleet_chunk(job)))


@admin_bp.post("/fleet-stats/cancel")
@superadmin_required
def fleet_stats_cancel():
    activity.note(summary="ספירת הרכבים הפעילים בוטלה")
    return jsonify(_fleet_payload(cancel_fleet_job(fleet_stats.active_job())))


# ---------------------------------------------------------------------------
# גילוי מק"טים מהאינטרנט
# ---------------------------------------------------------------------------


def _discovery_payload(job):
    return {
        "job": job.to_dict() if job else None,
        "catalog_size": Part.query.count(),
    }


@admin_bp.get("/discovery")
@superadmin_required
def discovery():
    return render_template(
        "admin/discovery.html",
        job=parts_discovery.latest_job(),
        part_types=all_types(),
        available=parts_discovery.discovery_available(),
        catalog_size=Part.query.count(),
    )


@admin_bp.get("/discovery/status")
@superadmin_required
def discovery_status():
    return jsonify(_discovery_payload(parts_discovery.latest_job()))


def _requested_plan(source):
    """המטרות שינבעו מהטופס. שדה ריק מתמלא בברירת מחדל."""
    return parts_discovery.plan_targets(
        source.get("make"), source.get("model"), source.getlist("part_type")
    )


@admin_bp.get("/discovery/plan")
@superadmin_required
def discovery_plan():
    """כמה חיפושים יירוצו, ואילו - לפני שמתחייבים לתשלום."""
    targets, capped = _requested_plan(request.args)
    source = parts_discovery.plan_source(
        request.args.get("make"), request.args.get("model")
    )
    return jsonify({
        "count": len(targets),
        "capped": capped,
        "max": parts_discovery.MAX_TARGETS,
        "source": parts_discovery.PLAN_SOURCES.get(source, ""),
        "sample": [
            f"{mk} {md} · {type_name(t)}" for mk, md, t in targets[:6]
        ],
    })


@admin_bp.post("/discovery/start")
@superadmin_required
def discovery_start():
    """מטרה לכל צירוף של דגם וסוג חלק. שדה ריק מתמלא בברירת מחדל."""
    if not parts_discovery.discovery_available():
        return jsonify({"error": "לא הוגדר ANTHROPIC_API_KEY בשרת."}), 400

    targets, _ = _requested_plan(request.form)
    if not targets:
        return jsonify({
            "error": "לא נמצאו דגמים לחיפוש. ייתכן שקטלוג דגמי הרכב ריק, "
                     "או שנבחר דגם בלי יצרן."
        }), 400

    job = parts_discovery.start_job(targets, user_id=current_user.id)
    activity.note(
        summary=f"גילוי מק\"טים הופעל · {len(targets)} מטרות",
        entity_type="job",
        entity_id=job.id if job else None,
        targets=len(targets),
    )
    return jsonify(_discovery_payload(job))


@admin_bp.post("/discovery/step")
@superadmin_required
def discovery_step():
    job = parts_discovery.active_job()
    if job is None:
        return jsonify({"error": "אין חיפוש פעיל.",
                        **_discovery_payload(parts_discovery.latest_job())}), 409
    return jsonify(_discovery_payload(parts_discovery.run_step(job)))


@admin_bp.post("/discovery/cancel")
@superadmin_required
def discovery_cancel():
    activity.note(summary='גילוי מק"טים בוטל')
    return jsonify(
        _discovery_payload(parts_discovery.cancel_job(parts_discovery.active_job()))
    )


# ---------------------------------------------------------------------------
# סקירת מה שהגילוי הכניס לקטלוג
# ---------------------------------------------------------------------------


@admin_bp.get("/discovery/review")
@superadmin_required
def discovery_review():
    """מה נכנס לקטלוג מהחיפוש האוטומטי, ומה נראה חשוד."""
    parts = parts_discovery.discovered_parts()
    rows = []
    for part in parts:
        flags = parts_discovery.review_flags(part)
        rows.append({
            "part": part,
            "flags": flags,
            "suspect": parts_discovery.suspect(flags),
            "structural": parts_discovery.structural(flags),
            "source_url": parts_discovery.source_url_of(part),
        })
    return render_template(
        "admin/discovery_review.html",
        rows=rows,
        flagged=sum(1 for row in rows if row["suspect"]),
        available=parts_discovery.discovery_available(),
        catalog_size=Part.query.count(),
    )


@admin_bp.post("/discovery/verify")
@superadmin_required
def discovery_verify():
    """אימות מק"ט אחד מול הרשת. מק"ט אחד לכל בקשה, כמו הגילוי עצמו."""
    if not parts_discovery.discovery_available():
        return jsonify({"error": "לא הוגדר ANTHROPIC_API_KEY בשרת."}), 400
    part = db.session.get(Part, request.form.get("part_id", type=int))
    if part is None:
        return jsonify({"error": 'המק"ט לא נמצא.'}), 404
    activity.note(
        summary=f"אימות {part.part_number}",
        entity_type="part",
        entity_id=part.id,
        part_number=part.part_number,
    )
    try:
        return jsonify(parts_discovery.verify(part))
    except Exception as exc:  # רשת, מפתח, מכסה או תשובה פגומה
        return jsonify({"error": str(exc)}), 502


@admin_bp.post("/discovery/delete")
@superadmin_required
def discovery_delete():
    """מחיקת מק"טים שנפסלו בסקירה. ההתאמות נמחקות איתם ב-cascade."""
    ids = request.form.getlist("part_id", type=int)
    deleted = []
    for part in Part.query.filter(Part.id.in_(ids)).all() if ids else []:
        deleted.append(part.part_number)
        db.session.delete(part)
    db.session.commit()
    activity.note(
        summary=f'נמחקו {len(deleted)} מק"טים שנפסלו בסקירה',
        deleted=deleted[:20],
        count=len(deleted),
    )
    return jsonify({"deleted": deleted, "catalog_size": Part.query.count()})


# ---------- עמודות טבלת המק"טים ----------

@admin_bp.get("/columns")
@superadmin_required
def columns():
    """מה מוצג בטבלת המק"טים, ובאיזה סדר.

    ההחלטה הזאת היא של מנהל האפליקציה ולא של המוסך: הטבלה אחת לכולם,
    ומי שמשנה אותה משנה אותה לכל המשתמשים.
    """
    shown = services.column_layout()
    shown_keys = {column.key for column in shown}
    return render_template(
        "admin/columns.html",
        shown=shown,
        available=[c for c in part_columns.COLUMNS if c.key not in shown_keys],
        is_default=[c.key for c in shown] == list(part_columns.DEFAULT_KEYS),
    )


@admin_bp.post("/columns")
@superadmin_required
def columns_save():
    """הוספה, הסרה והזזה - כולן מגיעות לכאן ומשנות רשימה אחת.

    הרשימה עצמה נשלחת מהטופס בכל פעם, ולכן פעולה שנשלחה פעמיים מתוך
    מסך ישן לא תזיז עמודה שכבר זזה.
    """
    keys = [key for key in request.form.getlist("key") if part_columns.by_key(key)]
    # "up:price" - הפעולה והעמודה שהיא מדברת עליה, בערך אחד של הכפתור
    action, _, target = (request.form.get("action") or "").partition(":")

    if action == "reset":
        keys = list(part_columns.DEFAULT_KEYS)
    elif action == "add" and part_columns.by_key(target) and target not in keys:
        keys.append(target)
    elif action == "remove" and target in keys:
        keys.remove(target)
    elif action in ("up", "down") and target in keys:
        index = keys.index(target)
        swap = index - 1 if action == "up" else index + 1
        if 0 <= swap < len(keys):
            keys[index], keys[swap] = keys[swap], keys[index]

    if not keys:
        flash("חייבת להישאר לפחות עמודה אחת.", "warning")
        return redirect(url_for("admin.columns"))

    services.save_column_layout(keys, user=current_user)
    activity.note(
        summary=f"{len(keys)} עמודות: " + ", ".join(
            part_columns.by_key(key).label for key in keys
        ),
        columns=keys,
        action=action or "save",
    )
    return redirect(url_for("admin.columns"))


# ---------------------------------------------------------------------------
# זריעת הקטלוג: מק"טים שמובאים מראש לרכבים נפוצים
# ---------------------------------------------------------------------------

def _seed_payload(job):
    return {
        "job": job.to_dict() if job else None,
        "catalog_size": Part.query.count(),
    }


@admin_bp.get("/seed")
@superadmin_required
def seed():
    return render_template(
        "admin/seed.html",
        job=seed_catalog.latest_job(),
        part_types=all_types(),
        default_types=seed_catalog.DEFAULT_PART_TYPES,
        default_vehicles=seed_catalog.DEFAULT_VEHICLES,
        available=live_lookup.available(),
        catalog_size=Part.query.count(),
    )


@admin_bp.get("/seed/status")
@superadmin_required
def seed_status():
    return jsonify(_seed_payload(seed_catalog.latest_job()))


@admin_bp.get("/seed/propose")
@superadmin_required
def seed_propose():
    """הצעת רשימת מטרות - לפני שמתחייבים לתשלום.

    כל מטרה היא כמה בקשות רשת וקריאות מודל, ולכן הרשימה מוצגת לעריכה
    ולא מורצת מיד. הדילוגים מוצגים גם הם: "הצענו 7 מתוך 10" היא עובדה
    שצריך לראות, לא שקט.
    """
    try:
        limit = min(30, max(1, int(request.args.get("vehicles", 0) or
                                   seed_catalog.DEFAULT_VEHICLES)))
    except ValueError:
        limit = seed_catalog.DEFAULT_VEHICLES
    chosen = request.args.getlist("part_type") or None
    targets, skipped = seed_catalog.propose(limit, part_types=chosen)
    return jsonify({
        "targets": targets,
        "skipped": skipped,
        "count": len(targets),
        "vehicles": len({t["vin"] for t in targets}),
    })


@admin_bp.post("/seed/start")
@superadmin_required
def seed_start():
    if not live_lookup.available():
        return jsonify({"error": "השליפה החיה כבויה - אין מפתח או מקור."}), 400
    try:
        targets = json.loads(request.form.get("targets") or "[]")
    except ValueError:
        return jsonify({"error": "רשימת המטרות אינה תקינה."}), 400
    targets = [t for t in targets if isinstance(t, dict) and t.get("vin")
               and t.get("part_type")]
    if not targets:
        return jsonify({"error": "אין מטרות לזרוע."}), 400
    job = seed_catalog.start_job(targets, user_id=current_user.id)
    activity.note(
        summary=f"זריעת קטלוג הופעלה · {len(targets)} מטרות",
        entity_type="job", entity_id=job.id, targets=len(targets),
    )
    return jsonify(_seed_payload(job))


@admin_bp.post("/seed/step")
@superadmin_required
def seed_step():
    job = seed_catalog.active_job()
    if job is None:
        return jsonify({"error": "אין זריעה פעילה.",
                        **_seed_payload(seed_catalog.latest_job())}), 409
    return jsonify(_seed_payload(seed_catalog.run_step(job, user=current_user)))


@admin_bp.post("/seed/cancel")
@superadmin_required
def seed_cancel():
    activity.note(summary="זריעת קטלוג בוטלה")
    return jsonify(_seed_payload(seed_catalog.cancel_job(seed_catalog.active_job())))
