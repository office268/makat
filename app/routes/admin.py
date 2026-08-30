"""מסכי ניהול מערכת - חוצי ארגונים.

מה שנמצא כאן משפיע על כל הלקוחות במערכת ולא על מוסך בודד, ולכן הכל
מוגן ב-superadmin_required ולא בתפקידים שבתוך הארגון.
"""
from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

from .. import parts_discovery
from ..auth import superadmin_required
from ..models import Part
from ..taxonomy import all_types, type_name
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
    return jsonify(_payload(start_job(user_id=current_user.id)))


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
    return jsonify(_payload(cancel_job(active_job())))


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
    return jsonify({
        "count": len(targets),
        "capped": capped,
        "max": parts_discovery.MAX_TARGETS,
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
    return jsonify(
        _discovery_payload(parts_discovery.cancel_job(parts_discovery.active_job()))
    )
