"""המסך הראשי: מספר רישוי + זיהוי חלק -> מק"ט ומחיר.

זו הזרימה שמייחדת את המוצר, ולכן היא יושבת על השורש. הכתובות הישנות
/demo ו-/identify מפנות לכאן, כדי שקישורים שכבר נשלחו לא יישברו.
"""
from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from .. import activity
from .. import identify as identifier
from .. import live_lookup, services, vehicles
from ..models import Part, db
from ..taxonomy import all_types, type_name

identify_bp = Blueprint("identify", __name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024


@identify_bp.route("/", methods=["GET", "POST"])
def index():
    context = {
        "part_types": all_types(),
        "catalog_size": Part.query.count(),
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
        "lookup_on": live_lookup.available(),
        "error": None,
    }
    # GET עם מספר רישוי הוא תוצאה שאפשר לשתף, לסמן ולחזור אליה עם "אחורי" -
    # וזה מה שמאפשר לתגיות הכיסוי להיות קישורים רגילים ולא כפתורי JS.
    source = request.form if request.method == "POST" else request.args
    plate = source.get("plate", "").strip()
    if request.method != "POST" and not plate:
        return render_template("identify.html", **context)

    query = source.get("query", "").strip()
    chosen_type = source.get("part_type", "").strip() or None
    if request.method == "POST":
        # שני כפתורים על אותו טופס. ברירת המחדל היא החיפוש המלא, כדי
        # ששילוב ישן של השדות (וה-API) ימשיך לעבוד בלי action
        action = request.form.get("action", "").strip() or "part"
    else:
        # בקישור אין כפתור: סוג חלק בכתובת אומר "חפש", בלעדיו רק לזהות
        action = "part" if chosen_type else "vehicle"
    context.update(plate=plate, query=query)

    # שלב 1 - הרכב
    vehicle = vehicles.lookup(plate)
    if not vehicle:
        activity.note(
            action="identify.vehicle_lookup",
            summary=f'{plate} - לא נמצא',
            plate=plate,
            found=False,
        )
        context["error"] = f'לא נמצא רכב עבור מספר רישוי "{plate}".'
        return render_template("identify.html", **context)
    context["vehicle"] = vehicle
    context["coverage"] = services.catalog_coverage(vehicle)
    activity.note(
        action="identify.vehicle_lookup",
        summary=f"{plate} · {vehicle.get('make')} {vehicle.get('model')}",
        plate=plate,
        make=vehicle.get("make"),
        model=vehicle.get("model"),
        year=vehicle.get("year"),
        covered_types=len(context["coverage"]),
    )

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
    matches = services.parts_for_vehicle(vehicle, selected)
    # מק"ט שההתאמה שלו מצהירה על המנוע של הרכב אינו כמו מק"ט שרק לא
    # סתר אותו. שניהם נשארים ברשימה - הקטלוג דליל מדי מכדי להסתיר -
    # אבל המאומתים עולים לראש ומסומנים.
    terms = services.vehicle_engine_terms(vehicle)
    verified = services.engine_matched_parts(matches, terms)
    matches.sort(key=lambda part: (part.id not in verified, part.part_number))
    context["matches"] = matches
    context["engine_matched"] = verified
    context["engine_term"] = (vehicle.get("engine_code") or "").strip() or None
    context["org_id"] = services.current_org_id()
    context["searched"] = True
    activity.note(
        action="identify.part_search",
        summary=f"{plate} · {type_name(selected)} · {len(context['matches'])} תוצאות",
        plate=plate,
        part_type=selected,
        results=len(context["matches"]),
        method=candidates[0].get("method"),
        query=query or None,
        photo=bool(image_bytes),
    )
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
        activity.note(summary=f"{plate} - לא נמצא", plate=plate, found=False)
        return jsonify({"error": f"לא נמצא רכב עבור {plate}"}), 404
    activity.note(
        summary=f"{plate} · {vehicle.get('make')} {vehicle.get('model')}",
        plate=plate,
        found=True,
    )
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
    activity.note(
        summary=(
            f"{candidates[0]['part_type'] if candidates else 'לא זוהה'}"
            f" · {len(payload['matches'])} תוצאות"
        ),
        plate=plate or None,
        query=query or None,
        photo=bool(image_bytes),
        results=len(payload["matches"]),
    )
    return jsonify(payload)


# --------------------------------------------------------------------------
# שליפה חיה: מק"ט שאין בקטלוג, מקטלוג היצרן לפי מספר שלדה
# --------------------------------------------------------------------------

def _lookup_vehicle(source):
    """הרכב שעליו השליפה רצה, או (None, תשובת שגיאה)."""
    plate = (source.get("plate") or "").strip()
    vehicle = vehicles.lookup(plate) if plate else None
    if not vehicle:
        return None, (jsonify({"error": f'לא נמצא רכב עבור "{plate}".'}), 404)
    return vehicle, None


def _catalog_row(part):
    """מק"ט מהקטלוג במבנה שהתצוגה של השליפה החיה יודעת להציג."""
    oem = part.oem_numbers
    return {
        "part_number": part.part_number,
        "manufacturer": part.manufacturer.name if part.manufacturer else "",
        "tier": "catalog",
        "oe_number": oem[0] if oem else "",
        "image_url": part.image_url or "",
        "price_eur": None,
        "source_url": "",
        "source_name": "הקטלוג",
        "confidence": "high",
        "note": "",
        "part_id": part.id,
        "verified": True,
        "reason": "",
    }


@identify_bp.post("/lookup/start")
def lookup_start():
    """פותח שליפה חיה - או מחזיר מיד תשובה שכבר שמורה.

    המטמון נבדק כאן ולא בתוך העבודה, כי תשובה שמורה לא צריכה עבודה
    בכלל: היא חוזרת בבקשה אחת, בלי בקשת רשת ובלי קריאה למודל.
    """
    vehicle, failure = _lookup_vehicle(request.form)
    if failure:
        return failure
    part_type = (request.form.get("part_type") or "").strip()
    query = (request.form.get("query") or "").strip()
    if not part_type and query:
        candidates = identifier.identify_from_text(query)
        part_type = candidates[0]["part_type"] if candidates else ""
    if not part_type:
        return jsonify({"error": "לא זוהה סוג חלק. אפשר לבחור אותו מהרשימה."}), 400

    # הקטלוג המקומי קודם. שליפה חיה למק"ט שכבר יש לנו היא בקשת רשת
    # מיותרת, קריאת מודל מיותרת, וזמן המתנה על מסך של מכונאי.
    if request.form.get("force") != "1":
        local = services.parts_for_vehicle(vehicle, part_type)
        if local:
            return jsonify({
                "from_catalog": True,
                "part_type": part_type,
                "part_type_name": type_name(part_type),
                "results": [_catalog_row(part) for part in local],
                "unverified": [],
                "job": None,
            })

    hit = live_lookup.cached(vehicle, part_type)
    if hit is not None:
        activity.note(
            action="lookup.cache_hit",
            summary=f'{vehicle.get("plate")} · {type_name(part_type)} · מהמטמון',
            plate=vehicle.get("plate"), part_type=part_type,
        )
        return jsonify({
            "from_cache": True,
            "part_type": part_type,
            "part_type_name": type_name(part_type),
            "results": hit.get("results") or [],
            "unverified": hit.get("unverified") or [],
            "job": None,
        })

    try:
        job = live_lookup.start_job(vehicle, part_type, user=current_user)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    activity.note(
        action="lookup.start",
        summary=f'{vehicle.get("plate")} · {type_name(part_type)} · {job.total} מקורות',
        plate=vehicle.get("plate"), part_type=part_type, job=job.id,
    )
    return jsonify({"from_cache": False, "job": job.to_dict()})


@identify_bp.post("/lookup/step")
def lookup_step():
    """מריץ מקור אחד. הדפדפן קורא שוב עד שהעבודה נגמרת."""
    job = db.session.get(live_lookup.LookupJob, request.form.get("job", type=int) or 0)
    if job is None or not job.is_running:
        return jsonify({"error": "אין שליפה פעילה."}), 409
    if job.started_by_id and job.started_by_id != current_user.id:
        return jsonify({"error": "השליפה הזו נפתחה על ידי משתמש אחר."}), 403
    return jsonify({"job": live_lookup.run_step(job).to_dict()})


@identify_bp.post("/lookup/cancel")
def lookup_cancel():
    job = db.session.get(live_lookup.LookupJob, request.form.get("job", type=int) or 0)
    if job is None:
        return jsonify({"error": "אין שליפה פעילה."}), 409
    return jsonify({"job": live_lookup.cancel_job(job).to_dict()})
