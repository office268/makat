"""מסכי ניהול מערכת - חוצי ארגונים.

מה שנמצא כאן משפיע על כל הלקוחות במערכת ולא על מוסך בודד, ולכן הכל
מוגן ב-superadmin_required ולא בתפקידים שבתוך הארגון.
"""
from flask import Blueprint, jsonify, render_template
from flask_login import current_user

from ..auth import superadmin_required
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
