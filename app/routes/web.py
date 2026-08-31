"""מסכי ה-HTML של האפליקציה."""
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy.orm import selectinload

from .. import activity, fleet_stats, services
from ..auth import role_required
from ..models import Category, Manufacturer, Part, Supplier, db
from ..taxonomy import all_types

web_bp = Blueprint("web", __name__)


def _known_makes():
    """יצרני רכב לבחירה: מקטלוג משרד התחבורה, ובנפילה לאלה שכבר בקטלוג."""
    from ..vehicle_catalog import makes

    return makes() or services.vehicle_makes()


def _filters_from_request():
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
        "organization_id": services.current_org_id(),
    }


@web_bp.app_context_processor
def inject_globals():
    return {
        "all_categories": Category.query.order_by(Category.name).all(),
        "all_manufacturers": Manufacturer.query.order_by(Manufacturer.name).all(),
        "all_makes": services.vehicle_makes(),
        "all_part_types": all_types(),
        "org_id": services.current_org_id(),
        "known_makes": _known_makes(),
    }


@web_bp.route("/dashboard")
def dashboard():
    """לוח מחוונים - סטטיסטיקות הקטלוג והתראות מלאי."""
    org_id = services.current_org_id()
    recent = Part.query.order_by(Part.created_at.desc()).limit(8).all()
    low = services.low_stock_parts(org_id, limit=8)
    return render_template(
        "dashboard.html",
        stats=services.stats(org_id),
        recent=recent,
        low_stock=low,
        org_id=org_id,
    )


@web_bp.route("/parts")
def parts_list():
    """רשימת מק"טים עם חיפוש, סינון ועימוד."""
    filters = _filters_from_request()
    page = request.args.get("page", 1, type=int)
    per_page = current_app.config["PER_PAGE"]
    # הטבלה מציגה מק"ט מקורי והתאמות לכל שורה, ובלי טעינה מראש כל שורה
    # הייתה שאילתה נוספת.
    query = services.search_parts(**filters).options(
        selectinload(Part.cross_refs), selectinload(Part.fitments)
    )
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    # מה שהמשתמש בחר, להבדיל ממה שתמיד נשלח (מיון, ארגון, active_only)
    is_filtered = any(
        filters[key]
        for key in ("q", "category_id", "manufacturer_id", "make", "model",
                    "year", "engine", "in_stock", "low_stock")
    )
    activity.note(
        summary=(f'חיפוש "{filters["q"]}"' if filters["q"] else 'רשימת מק"טים')
        + f" · {pagination.total} תוצאות",
        results=pagination.total,
        page=page,
        filtered=is_filtered,
    )
    return render_template(
        "parts/list.html",
        pagination=pagination,
        parts=pagination.items,
        filters=filters,
        is_filtered=is_filtered,
        total_parts=Part.query.count(),
    )


@web_bp.route("/parts/<int:part_id>")
def part_detail(part_id):
    """כרטיס מק"ט מלא."""
    part = db.session.get(Part, part_id)
    if part is None:
        abort(404)
    org_id = services.current_org_id()
    activity.note(
        summary=f"{part.part_number} · {part.name_he}",
        entity_type="part",
        entity_id=part.id,
        part_number=part.part_number,
    )
    return render_template(
        "parts/detail.html",
        part=part,
        org_part=part.for_org(org_id),
        org_id=org_id,
        equivalents=services.equivalent_parts(part),
    )


@web_bp.route("/parts/lookup")
def part_lookup():
    """חיפוש מהיר לפי מק"ט מדויק (כולל מק"טים מקבילים)."""
    number = request.args.get("number", "").strip()
    part = services.find_by_number(number)
    if part:
        activity.note(
            summary=f"{number} → {part.part_number}",
            entity_type="part",
            entity_id=part.id,
            found=True,
        )
        return redirect(url_for("web.part_detail", part_id=part.id))
    activity.note(summary=f"{number} - לא נמצא", found=False)
    flash(f'לא נמצא מק"ט "{number}" בקטלוג', "warning")
    return redirect(url_for("web.parts_list", q=number))


@web_bp.route("/parts/new", methods=["GET", "POST"])
@role_required("manager")
def part_create():
    """הוספת מק"ט חדש."""
    if request.method == "POST":
        number = request.form.get("part_number", "").strip()
        if not number:
            flash('חובה להזין מק"ט', "danger")
        elif Part.query.filter_by(part_number=number).first():
            flash(f'המק"ט {number} כבר קיים בקטלוג', "danger")
        elif not request.form.get("name_he", "").strip():
            flash("חובה להזין שם חלק", "danger")
        else:
            part = services.part_from_row(
                request.form.to_dict(),
                organization_id=services.current_org_id(),
                rows=request.form.to_dict(flat=False),
            )
            db.session.add(part)
            db.session.commit()
            activity.note(
                summary=f"{part.part_number} · {part.name_he}",
                entity_type="part",
                entity_id=part.id,
                part_number=part.part_number,
            )
            flash(f'המק"ט {part.part_number} נוסף בהצלחה', "success")
            if request.form.get("save_and_new"):
                return redirect(url_for("web.part_create"))
            return redirect(url_for("web.part_detail", part_id=part.id))
    return render_template(
        "parts/form.html", part=None, org_part=None, form=request.form
    )


@web_bp.route("/parts/<int:part_id>/edit", methods=["GET", "POST"])
@role_required("manager")
def part_edit(part_id):
    """עריכת מק"ט קיים."""
    part = db.session.get(Part, part_id)
    if part is None:
        abort(404)
    if request.method == "POST":
        number = request.form.get("part_number", "").strip()
        clash = Part.query.filter(
            Part.part_number == number, Part.id != part.id
        ).first()
        if not number:
            flash('חובה להזין מק"ט', "danger")
        elif clash:
            flash(f'המק"ט {number} כבר משויך לחלק אחר', "danger")
        else:
            services.part_from_row(
                request.form.to_dict(), part,
                organization_id=services.current_org_id(),
                rows=request.form.to_dict(flat=False),
            )
            db.session.commit()
            activity.note(
                summary=f"{part.part_number} · {part.name_he}",
                entity_type="part",
                entity_id=part.id,
                part_number=part.part_number,
            )
            flash("השינויים נשמרו", "success")
            return redirect(url_for("web.part_detail", part_id=part.id))
    return render_template(
        "parts/form.html",
        part=part,
        org_part=part.for_org(services.current_org_id()),
        form=None,
    )


@web_bp.route("/parts/<int:part_id>/delete", methods=["POST"])
@role_required("manager")
def part_delete(part_id):
    """מחיקת מק"ט."""
    part = db.session.get(Part, part_id)
    if part is None:
        abort(404)
    number = part.part_number
    db.session.delete(part)
    db.session.commit()
    activity.note(
        summary=f'מחיקת מק"ט {number}',
        entity_type="part",
        entity_id=part_id,
        part_number=number,
    )
    flash(f'המק"ט {number} נמחק', "info")
    return redirect(url_for("web.parts_list"))


@web_bp.route("/vehicles")
def vehicles():
    """חיפוש חלקים לפי רכב."""
    make = request.args.get("make", "").strip() or None
    model = request.args.get("model", "").strip() or None
    year = request.args.get("year", type=int)
    results = []
    if make or model or year:
        results = services.search_parts(make=make, model=model, year=year).all()
        activity.note(
            summary=f"{make or ''} {model or ''} {year or ''} · {len(results)} תוצאות".strip(),
            make=make,
            model=model,
            year=year,
            results=len(results),
        )
    return render_template(
        "vehicles.html",
        results=results,
        models=services.vehicle_models(make),
        selected={"make": make, "model": model, "year": year},
    )


@web_bp.route("/stats")
def stats():
    """כמה רכבים מכל דגם פעילים בישראל, לפי מרשם משרד התחבורה.

    הטבלה נטענת מראש (scripts/vehicle_stats.py) ולא נמשכת בזמן הבקשה:
    הספירה היא על שלושה מיליון רשומות אצל המאגר, וזה לא משהו שמחכים לו
    בתוך בקשת דפדפן.
    """
    q = request.args.get("q", "").strip() or None
    make = request.args.get("make", "").strip() or None
    sort = request.args.get("sort", "vehicles")
    page = request.args.get("page", 1, type=int)
    # הצילום החי נקבע פעם אחת ומועבר לכל השאילתות: אחרת ספירה שרצה
    # ברקע הייתה יכולה להתפרסם באמצע הבקשה, והמסך היה מציג טבלה מצילום
    # אחד וסכומים מצילום אחר
    taken_at = fleet_stats.live_taken_at()
    totals = fleet_stats.summary(taken_at=taken_at)

    if sort == "gap":
        rows, pagination = _fleet_gaps(q, make, taken_at), None
    else:
        pagination = fleet_stats.search(
            q=q, make=make, taken_at=taken_at, sort=sort
        ).paginate(
            page=page, per_page=current_app.config["PER_PAGE"], error_out=False
        )
        rows = pagination.items

    part_counts = _part_counts_for_rows(rows)
    activity.note(
        summary=(f'צי הרכב: {q or make or "הכל"} · {sort}'),
        results=pagination.total if pagination else len(rows),
        page=page,
        sort=sort,
    )
    return render_template(
        "stats.html",
        pagination=pagination,
        rows=rows,
        totals=totals,
        part_counts=part_counts,
        gap_limit=current_app.config["FLEET_GAP_MODELS"],
        # סך הרכבים בסינון הנוכחי - "8% מהצי" הוא מספר אחר כשמסננים יצרן
        filtered_vehicles=fleet_stats.total_vehicles(q=q, make=make, taken_at=taken_at),
        makes=fleet_stats.makes(taken_at=taken_at),
        sorts=fleet_stats.SORTS,
        selected={"q": q, "make": make, "sort": sort},
    )


def _part_counts_for_rows(rows):
    """{מזהה שורה: (מק"טים, מתוכם מתכלים)}, בשאילתה אחת לכל הדגמים.

    דגם שחוזר בכמה קודי דגם נספר פעם אחת - הקטלוג לא יודע להבחין
    ביניהם, ושתי שורות שיציגו את אותו מספר אינן שתי בדיקות.
    """
    counts = services.part_counts_for(
        [(row.search_make, row.model) for row in rows]
    )
    return {row.id: counts[(row.search_make, row.model)] for row in rows}


def _fleet_gaps(q, make, taken_at):
    """הדגמים עם הפער הגדול ביותר. אותו דירוג שמנוע הגילוי מכוון לפיו."""
    ranked, _ = fleet_stats.gap_ranking(
        q=q, make=make, taken_at=taken_at,
        limit=current_app.config["FLEET_GAP_MODELS"],
    )
    return ranked


@web_bp.route("/stats.csv")
def stats_csv():
    """ייצוא הפילוח שעל המסך, באותו סינון."""
    q = request.args.get("q", "").strip() or None
    make = request.args.get("make", "").strip() or None
    rows = [row.to_dict() for row in fleet_stats.search(q=q, make=make).all()]
    activity.note(summary=f"ייצוא צי הרכב · {len(rows)} דגמים", rows=len(rows))
    return Response(
        fleet_stats.to_csv(rows),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=fleet_by_model.csv"},
    )


@web_bp.route("/manufacturers")
def manufacturers():
    return render_template(
        "manufacturers.html",
        manufacturers=Manufacturer.query.order_by(Manufacturer.name).all(),
    )


@web_bp.route("/categories")
def categories():
    roots = (
        Category.query.filter(Category.parent_id.is_(None))
        .order_by(Category.name)
        .all()
    )
    return render_template("categories.html", roots=roots)


@web_bp.route("/suppliers")
def suppliers():
    org_id = services.current_org_id()
    rows = (
        Supplier.query.filter_by(organization_id=org_id).order_by(Supplier.name).all()
        if org_id
        else []
    )
    return render_template("suppliers.html", suppliers=rows)


@web_bp.route("/export.csv")
def export_csv():
    """ייצוא תוצאות החיפוש הנוכחיות ל-CSV."""
    org_id = services.current_org_id()
    parts = services.search_parts(**_filters_from_request()).all()
    activity.note(summary=f'ייצוא {len(parts)} מק"טים', rows=len(parts))
    return Response(
        services.export_csv(parts, organization_id=org_id),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=makat_export.csv"},
    )


@web_bp.route("/import", methods=["GET", "POST"])
@role_required("manager")
def import_csv():
    """ייבוא מק"טים מקובץ CSV."""
    result = None
    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            flash("לא נבחר קובץ", "danger")
        else:
            created, updated, errors = services.import_csv(
                file.stream, organization_id=services.current_org_id()
            )
            result = {"created": created, "updated": updated, "errors": errors}
            activity.note(
                summary=(
                    f"{file.filename}: {created} חדשים, {updated} עודכנו"
                    + (f", {len(errors)} שגיאות" if errors else "")
                ),
                filename=file.filename,
                created=created,
                updated=updated,
                errors=len(errors),
            )
            flash(f"יובאו {created} מק\"טים חדשים, עודכנו {updated}", "success")
    return render_template("import.html", result=result, columns=services.CSV_COLUMNS)


@web_bp.app_errorhandler(404)
def not_found(_error):
    return render_template("404.html"), 404
