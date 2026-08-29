"""REST API - JSON לכל נתוני הקטלוג."""
from flask import Blueprint, current_app, jsonify, request

from .. import services
from ..models import Category, Manufacturer, Part, Supplier, db

api_bp = Blueprint("api", __name__)


def _filters():
    return {
        "q": request.args.get("q", "").strip() or None,
        "category_id": request.args.get("category_id", type=int),
        "manufacturer_id": request.args.get("manufacturer_id", type=int),
        "make": request.args.get("make", "").strip() or None,
        "model": request.args.get("model", "").strip() or None,
        "year": request.args.get("year", type=int),
        "engine": request.args.get("engine", "").strip() or None,
        "in_stock": request.args.get("in_stock") == "1",
        "low_stock": request.args.get("low_stock") == "1",
        "active_only": request.args.get("show_inactive") != "1",
        "sort": request.args.get("sort", "part_number"),
    }


@api_bp.get("/parts")
def list_parts():
    """רשימת מק"טים עם חיפוש ועימוד."""
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", current_app.config["PER_PAGE"], type=int), 200)
    pagination = services.search_parts(**_filters()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return jsonify(
        {
            "items": [p.to_dict() for p in pagination.items],
            "page": pagination.page,
            "pages": pagination.pages,
            "per_page": pagination.per_page,
            "total": pagination.total,
        }
    )


@api_bp.get("/parts/<int:part_id>")
def get_part(part_id):
    part = db.session.get(Part, part_id)
    if part is None:
        return jsonify({"error": 'מק"ט לא נמצא'}), 404
    return jsonify(part.to_dict(full=True))


@api_bp.get("/parts/number/<path:number>")
def get_part_by_number(number):
    """שליפה לפי מק"ט או לפי מק"ט מקביל."""
    part = services.find_by_number(number)
    if part is None:
        return jsonify({"error": f'מק"ט {number} לא נמצא'}), 404
    data = part.to_dict(full=True)
    data["matched_number"] = number
    data["equivalents"] = [p.to_dict() for p in services.equivalent_parts(part)]
    return jsonify(data)


@api_bp.post("/parts")
def create_part():
    payload = request.get_json(silent=True) or {}
    number = (payload.get("part_number") or "").strip()
    if not number:
        return jsonify({"error": 'שדה part_number הוא חובה'}), 400
    if not (payload.get("name_he") or "").strip():
        return jsonify({"error": "שדה name_he הוא חובה"}), 400
    if Part.query.filter_by(part_number=number).first():
        return jsonify({"error": f'המק"ט {number} כבר קיים'}), 409
    part = services.part_from_row(payload)
    db.session.add(part)
    db.session.commit()
    return jsonify(part.to_dict(full=True)), 201


@api_bp.put("/parts/<int:part_id>")
@api_bp.patch("/parts/<int:part_id>")
def update_part(part_id):
    part = db.session.get(Part, part_id)
    if part is None:
        return jsonify({"error": 'מק"ט לא נמצא'}), 404
    payload = request.get_json(silent=True) or {}
    merged = part.to_dict(full=True)
    merged["manufacturer"] = part.manufacturer.name if part.manufacturer else ""
    merged["category"] = part.category.full_name if part.category else ""
    merged["cross_refs"] = services.format_cross_refs(part)
    merged["fitments"] = services.format_fitments(part)
    merged.update(payload)
    number = (merged.get("part_number") or "").strip()
    clash = Part.query.filter(Part.part_number == number, Part.id != part.id).first()
    if clash:
        return jsonify({"error": f'המק"ט {number} כבר משויך לחלק אחר'}), 409
    services.part_from_row(merged, part)
    db.session.commit()
    return jsonify(part.to_dict(full=True))


@api_bp.delete("/parts/<int:part_id>")
def delete_part(part_id):
    part = db.session.get(Part, part_id)
    if part is None:
        return jsonify({"error": 'מק"ט לא נמצא'}), 404
    db.session.delete(part)
    db.session.commit()
    return jsonify({"deleted": part_id})


@api_bp.get("/categories")
def list_categories():
    return jsonify(
        [c.to_dict() for c in Category.query.order_by(Category.name).all()]
    )


@api_bp.get("/manufacturers")
def list_manufacturers():
    return jsonify(
        [m.to_dict() for m in Manufacturer.query.order_by(Manufacturer.name).all()]
    )


@api_bp.get("/suppliers")
def list_suppliers():
    return jsonify([s.to_dict() for s in Supplier.query.order_by(Supplier.name).all()])


@api_bp.get("/vehicles/makes")
def list_makes():
    return jsonify(services.vehicle_makes())


@api_bp.get("/vehicles/models")
def list_models():
    return jsonify(services.vehicle_models(request.args.get("make")))


@api_bp.get("/stats")
def get_stats():
    return jsonify(services.stats())
