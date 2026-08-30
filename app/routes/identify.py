"""המסך הראשי: מספר רישוי + זיהוי חלק -> מק"ט ומחיר.

זו הזרימה שמייחדת את המוצר, ולכן היא יושבת על השורש. הכתובות הישנות
/demo ו-/identify מפנות לכאן, כדי שקישורים שכבר נשלחו לא יישברו.
"""
from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from .. import identify as identifier
from .. import services, vehicles
from ..taxonomy import all_types, type_name

identify_bp = Blueprint("identify", __name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024


@identify_bp.route("/", methods=["GET", "POST"])
def index():
    context = {
        "part_types": all_types(),
        "vision_on": identifier.vision_available(),
        "plate": "",
        "query": "",
        "vehicle": None,
        "candidates": [],
        "selected_type": None,
        "matches": [],
        "coverage": {},
        "searched": False,
        "org_id": services.current_org_id(),
        "error": None,
    }
    if request.method != "POST":
        return render_template("identify.html", **context)

    plate = request.form.get("plate", "").strip()
    query = request.form.get("query", "").strip()
    chosen_type = request.form.get("part_type", "").strip() or None
    # שני כפתורים על אותו טופס. ברירת המחדל היא החיפוש המלא, כדי
    # ששילוב ישן של השדות (וה-API) ימשיך לעבוד בלי action
    action = request.form.get("action", "").strip() or "part"
    context.update(plate=plate, query=query)

    # שלב 1 - הרכב
    vehicle = vehicles.lookup(plate)
    if not vehicle:
        context["error"] = f'לא נמצא רכב עבור מספר רישוי "{plate}".'
        return render_template("identify.html", **context)
    context["vehicle"] = vehicle
    context["coverage"] = services.catalog_coverage(vehicle)

    if action == "vehicle":
        return render_template("identify.html", **context)

    # שלב 2 - סוג החלק
    image_bytes = None
    media_type = "image/jpeg"
    upload = request.files.get("photo")
    if upload and upload.filename:
        image_bytes = upload.read(MAX_IMAGE_BYTES + 1)
        if len(image_bytes) > MAX_IMAGE_BYTES:
            context["error"] = "הקובץ גדול מ-5MB."
            return render_template("identify.html", **context)
        media_type = upload.mimetype or "image/jpeg"

    if not (chosen_type or query or image_bytes):
        context["error"] = "יש לתאר את החלק, לצלם אותו או לבחור אותו מהרשימה."
        return render_template("identify.html", **context)

    if chosen_type:
        candidates = [
            {
                "part_type": chosen_type,
                "name": type_name(chosen_type),
                "confidence": 1.0,
                "method": "manual",
                "note": None,
                "category": None,
            }
        ]
    else:
        candidates = identifier.identify(
            text=query, image_bytes=image_bytes, media_type=media_type
        )

    context["candidates"] = candidates
    if not candidates or not candidates[0].get("part_type"):
        context["error"] = context["error"] or (
            "לא זוהה סוג חלק. אפשר לתאר אותו במילים אחרות או לבחור מהרשימה."
        )
        return render_template("identify.html", **context)

    # שלב 3 - ההצטלבות
    selected = candidates[0]["part_type"]
    context["selected_type"] = selected
    context["matches"] = services.parts_for_vehicle(vehicle, selected)
    context["org_id"] = services.current_org_id()
    context["searched"] = True
    return render_template("identify.html", **context)


@identify_bp.get("/demo")
@identify_bp.get("/identify")
def legacy_urls():
    """כתובות קודמות - שומר על קישורים שכבר נשלחו."""
    return redirect(url_for("identify.index"))


@identify_bp.get("/api/vehicle/<plate>")
def api_vehicle(plate):
    """שליפת רכב לפי מספר רישוי."""
    vehicle = vehicles.lookup(plate)
    if not vehicle:
        return jsonify({"error": f"לא נמצא רכב עבור {plate}"}), 404
    return jsonify(vehicle)


@identify_bp.post("/api/identify")
def api_identify():
    """זיהוי סוג חלק מטקסט או מתמונה, והצלבה מול רכב אם נמסר מספר רישוי."""
    payload_json = request.get_json(silent=True) or {}
    plate = (request.form.get("plate") or payload_json.get("plate") or "").strip()
    query = (request.form.get("query") or payload_json.get("query") or "").strip()

    image_bytes = None
    media_type = "image/jpeg"
    upload = request.files.get("photo")
    if upload and upload.filename:
        image_bytes = upload.read(MAX_IMAGE_BYTES + 1)
        if len(image_bytes) > MAX_IMAGE_BYTES:
            return jsonify({"error": "הקובץ גדול מ-5MB"}), 413
        media_type = upload.mimetype or "image/jpeg"

    candidates = identifier.identify(
        text=query, image_bytes=image_bytes, media_type=media_type
    )
    payload = {"candidates": candidates, "vehicle": None, "matches": []}

    if plate:
        vehicle = vehicles.lookup(plate)
        payload["vehicle"] = vehicle
        if vehicle and candidates and candidates[0].get("part_type"):
            payload["matches"] = [
                part.to_dict(full=True, organization_id=services.current_org_id())
                for part in services.parts_for_vehicle(
                    vehicle, candidates[0]["part_type"]
                )
            ]
    return jsonify(payload)
