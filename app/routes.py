import random
from datetime import date, timedelta

from flask import Blueprint, abort, g, redirect, render_template, request, url_for
from sqlalchemy import func

from . import db, services
from .models import (
    SLOTS, UNITS, Ingredient, MealEntry, Recipe, RecipeIngredient, ShoppingExtra, Tag, User,
)

bp = Blueprint("main", __name__)


# ---------- helpers ----------

def _date(text, default=None):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        if default is not None:
            return default
        abort(404)


def _users():
    return User.query.order_by(User.side).all()


def _week_url(d, **params):
    year, week = services.week_of(d)
    return url_for("main.week_view", year=year, week=week, **params)


def _back(default):
    nxt = request.form.get("next") or request.args.get("next")
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect(default)


def _int(text, default=None):
    value = services.parse_decimal(text)
    return default if value is None else int(round(value))


def _servings_from_form(users):
    servings = {}
    for u in users:
        servings[u.id] = max(services.parse_tenths(request.form.get(f"portions_{u.id}", "")) or 0, 0)
    guests = max(services.parse_tenths(request.form.get("guests", "0")) or 0, 0)
    return servings, guests


def _recipe_cards(recipes):
    last = services.last_cooked_map(g.today)
    cards = []
    for r in recipes:
        totals = services.recipe_totals(r)
        cards.append({"recipe": r, "totals": totals, "last": last.get(r.id)})
    return cards


def _picker_context(d, slot):
    q = (request.args.get("q") or "").strip()
    tag = request.args.get("tag") or ""
    query = Recipe.query.filter_by(archived=False)
    if q:
        query = query.filter(func.ulower(Recipe.name).contains(q.lower()))
    if tag:
        query = query.filter(Recipe.tags.any(Tag.name == tag))
    cards = _recipe_cards(query.order_by(Recipe.name).all())
    cards.sort(key=lambda c: (c["last"] is not None, c["last"] or date.min, c["recipe"].name.lower()))
    selected = None
    if request.args.get("random") and cards:
        selected = random.choice(cards)["recipe"]
    elif request.args.get("recipe"):
        selected = db.session.get(Recipe, int(request.args["recipe"]))
    return {
        "date": d, "slot": slot, "q": q, "tag": tag, "cards": cards, "selected": selected,
        "tags": Tag.query.order_by(Tag.sort_order, Tag.id).all(),
        "eaters": {u.id for u in services.default_eaters()},
        "guests": services.get_setting("default_guests"),
    }


def _fill_context(dates, slots):
    args = request.args
    only_empty = args.get("only_empty", "1") == "1"
    fslots = [s for s in args.getlist("fslot") if s in SLOTS] or ["dinner"]
    repeat_days = _int(args.get("repeat_days"), int(services.get_setting("repeat_days")))
    weekday_tag = services.get_setting("weekday_tag") if args.get("weekday", "1") == "1" else None
    two_days = args.get("two_days", "1") == "1"
    seed = _int(args.get("seed"), random.randrange(1_000_000))
    raw = args.getlist("p")
    if raw and args.get("reroll"):
        key_date, key_slot = args["reroll"].split("|")
        proposals = services.reroll(services.decode_proposals(raw), dates,
                                    (date.fromisoformat(key_date), key_slot),
                                    repeat_days, weekday_tag, two_days, seed, only_empty)
    elif raw and not args.get("regen"):
        proposals = services.decode_proposals(raw)
    else:
        proposals = services.fill_week(dates, fslots, only_empty, repeat_days, weekday_tag, two_days, seed)
    by_key = {(p["date"], p["slot"]): p for p in proposals}
    rows = [s for s in SLOTS if s in fslots or any(k[1] == s for k in by_key)]
    return {
        "only_empty": only_empty, "fslots": fslots, "repeat_days": repeat_days,
        "weekday": weekday_tag is not None, "two_days": two_days, "seed": seed,
        "proposals": proposals, "by_key": by_key, "rows": rows,
        "encoded": [services.encode_proposal(p) for p in proposals],
        "new_count": sum(1 for p in proposals if p["leftover_of"] is None),
        "replaced": sum(len(services.entries_for(dates).get((p["date"], p["slot"]), [])) for p in proposals),
        "params": {k: v for k, v in args.items(multi=True) if k not in ("p", "reroll", "seed", "regen", "dlg")},
    }


def _plan_dialogs(dates, slots):
    """Dialog context shared by the week and day views (?dlg=pick|fill|entry)."""
    ctx = {"dlg": request.args.get("dlg"), "users": _users(), "pick": None, "fill": None, "entry": None}
    if ctx["dlg"] == "pick":
        slot = request.args.get("slot") if request.args.get("slot") in SLOTS else "dinner"
        ctx["pick"] = _picker_context(_date(request.args.get("date"), g.today), slot)
    elif ctx["dlg"] == "fill":
        ctx["fill"] = _fill_context(dates, slots)
    elif ctx["dlg"] == "entry":
        ctx["entry"] = db.session.get(MealEntry, _int(request.args.get("entry"), 0))
        if ctx["entry"] is None:
            ctx["dlg"] = None
    return ctx


# ---------- plan ----------

@bp.get("/")
def index():
    return redirect(_week_url(g.today))


@bp.get("/w/<int:year>/<int:week>")
def week_view(year, week):
    try:
        dates = services.week_dates(year, week)
    except ValueError:
        abort(404)
    slots = services.visible_slots()
    prev_d, next_d = dates[0] - timedelta(days=7), dates[0] + timedelta(days=7)
    return render_template(
        "week.html", dates=dates, slots=slots, by_cell=services.entries_for(dates),
        label=services.fmt_range(dates[0], dates[-1]),
        prev={"url": _week_url(prev_d), "name": services.fmt_range(prev_d, prev_d + timedelta(days=6))},
        next={"url": _week_url(next_d), "name": services.fmt_range(next_d, next_d + timedelta(days=6))},
        here=url_for("main.week_view", year=year, week=week),
        **_plan_dialogs(dates, slots),
    )


@bp.get("/d/<iso>")
def day_view(iso):
    d = g.today if iso == "today" else _date(iso)
    year, week = services.week_of(d)
    dates = services.week_dates(year, week)
    slots = services.visible_slots()
    return render_template(
        "day.html", d=d, dates=dates, slots=slots, by_cell=services.entries_for(dates),
        label=services.fmt_range(dates[0], dates[-1]),
        prev_url=url_for("main.day_view", iso=(d - timedelta(days=1)).isoformat()),
        next_url=url_for("main.day_view", iso=(d + timedelta(days=1)).isoformat()),
        here=url_for("main.day_view", iso=d.isoformat()),
        **_plan_dialogs(dates, slots),
    )


@bp.post("/entries")
def entry_create():
    d = _date(request.form.get("date"))
    slot = request.form.get("slot") if request.form.get("slot") in SLOTS else "dinner"
    recipe = db.session.get(Recipe, _int(request.form.get("recipe_id"), 0))
    free_text = (request.form.get("free_text") or "").strip()
    if recipe is None and not free_text:
        return _back(_week_url(d, dlg="pick", date=d.isoformat(), slot=slot))
    servings, guests = _servings_from_form(_users())
    leftover_slot = None
    if recipe is not None and request.form.get("leftover"):
        leftover_slot = request.form.get("leftover_slot") if request.form.get("leftover_slot") in SLOTS else "lunch"
    services.create_entry(d, slot, recipe=recipe, free_text=free_text or None,
                          servings=servings, guests_tenths=guests, leftover_slot=leftover_slot)
    db.session.commit()
    return _back(_week_url(d))


@bp.post("/entries/<int:entry_id>/servings")
def entry_servings(entry_id):
    entry = db.session.get(MealEntry, entry_id) or abort(404)
    servings, guests = _servings_from_form(_users())
    services.set_servings(entry, servings, guests)
    db.session.commit()
    return _back(_week_url(entry.date))


@bp.post("/entries/<int:entry_id>/leftover")
def entry_leftover(entry_id):
    entry = db.session.get(MealEntry, entry_id) or abort(404)
    slot = request.form.get("slot") if request.form.get("slot") in SLOTS else "lunch"
    if entry.recipe is not None and not entry.is_leftover and not entry.children:
        services.add_leftover(entry, slot)
        db.session.commit()
    return _back(_week_url(entry.date))


@bp.post("/entries/<int:entry_id>/move")
def entry_move(entry_id):
    entry = db.session.get(MealEntry, entry_id) or abort(404)
    days = 1 if request.form.get("dir") != "prev" else -1
    entry.date += timedelta(days=days)
    for child in entry.children:
        child.date += timedelta(days=days)
    db.session.commit()
    return _back(_week_url(entry.date))


@bp.post("/entries/<int:entry_id>/delete")
def entry_delete(entry_id):
    entry = db.session.get(MealEntry, entry_id) or abort(404)
    d = entry.date
    db.session.delete(entry)
    db.session.commit()
    return _back(_week_url(d))


@bp.post("/fill/accept")
def fill_accept():
    proposals = services.decode_proposals(request.form.getlist("p"))
    if proposals:
        guests = max(services.parse_tenths(services.get_setting("default_guests")) or 0, 0)
        services.accept_proposals(proposals, services.default_eaters(), guests,
                                  replace=request.form.get("replace") == "1")
        db.session.commit()
        return _back(_week_url(proposals[0]["date"]))
    return _back(_week_url(g.today))


# ---------- recipes ----------

@bp.get("/recipes")
def recipes():
    q = (request.args.get("q") or "").strip()
    tag = request.args.get("tag") or ""
    show_archived = request.args.get("archived") == "1"
    query = Recipe.query.filter_by(archived=show_archived)
    if q:
        query = query.filter(func.ulower(Recipe.name).contains(q.lower()))
    if tag:
        query = query.filter(Recipe.tags.any(Tag.name == tag))
    cards = _recipe_cards(query.order_by(Recipe.name).all())
    if request.args.get("sort") == "old":
        cards.sort(key=lambda c: (c["last"] is not None, c["last"] or date.min))
    return render_template("recipes.html", cards=cards, q=q, tag=tag, sort=request.args.get("sort", ""),
                           show_archived=show_archived,
                           tags=Tag.query.order_by(Tag.sort_order, Tag.id).all(),
                           dlg=request.args.get("dlg"))


@bp.post("/recipes")
def recipe_create():
    name = (request.form.get("name") or "").strip() or "Новый рецепт"
    recipe = Recipe(name=name, portions=max(_int(request.form.get("portions"), 4) or 4, 1))
    db.session.add(recipe)
    db.session.commit()
    return redirect(url_for("main.recipe_edit", recipe_id=recipe.id))


def _recipe_lines(recipe, factor=1.0):
    lines = []
    for ri in recipe.ingredients:
        grams, kcal, protein, fat, carbs = services.line_macros(ri)
        lines.append({"ri": ri, "grams": grams, "kcal": kcal,
                      "qty_tenths": services.scaled_qty(ri, factor)})
    return lines


@bp.get("/recipes/<int:recipe_id>")
def recipe_view(recipe_id):
    recipe = db.session.get(Recipe, recipe_id) or abort(404)
    portions = max(_int(request.args.get("portions"), recipe.portions) or recipe.portions, 1)
    factor = portions / max(recipe.portions, 1)
    return render_template("recipe_view.html", recipe=recipe, portions=portions,
                           lines=_recipe_lines(recipe, factor), totals=services.recipe_totals(recipe),
                           last=services.last_cooked(recipe, g.today),
                           steps=[s.strip() for s in (recipe.steps or "").splitlines() if s.strip()])


@bp.get("/recipes/<int:recipe_id>/edit")
def recipe_edit(recipe_id):
    recipe = db.session.get(Recipe, recipe_id) or abort(404)
    new_name = (request.args.get("new") or "").strip()
    return render_template(
        "recipe_edit.html", recipe=recipe, lines=_recipe_lines(recipe),
        totals=services.recipe_totals(recipe),
        tags=Tag.query.order_by(Tag.sort_order, Tag.id).all(),
        names=[n for (n,) in db.session.query(Ingredient.name).filter_by(archived=False).order_by(Ingredient.name)],
        sections=services.sections(),
        new_ing={"name": new_name, "qty": request.args.get("qty", "1"),
                 "unit": request.args.get("unit", "g")} if new_name else None,
    )


@bp.post("/recipes/<int:recipe_id>")
def recipe_save(recipe_id):
    recipe = db.session.get(Recipe, recipe_id) or abort(404)
    recipe.name = (request.form.get("name") or "").strip() or recipe.name
    recipe.portions = max(_int(request.form.get("portions"), recipe.portions) or 1, 1)
    recipe.prep_minutes = _int(request.form.get("prep_minutes"))
    recipe.cooked_weight_g = _int(request.form.get("cooked_weight_g"))
    recipe.steps = (request.form.get("steps") or "").strip() or None
    tag_ids = {int(t) for t in request.form.getlist("tags") if t.isdigit()}
    recipe.tags = Tag.query.filter(Tag.id.in_(tag_ids)).all() if tag_ids else []
    db.session.commit()
    if request.form.get("action") == "view":
        return redirect(url_for("main.recipe_view", recipe_id=recipe.id))
    return redirect(url_for("main.recipe_edit", recipe_id=recipe.id))


@bp.post("/recipes/<int:recipe_id>/archive")
def recipe_archive(recipe_id):
    recipe = db.session.get(Recipe, recipe_id) or abort(404)
    recipe.archived = not recipe.archived
    db.session.commit()
    return redirect(url_for("main.recipes"))


@bp.post("/recipes/<int:recipe_id>/ingredients")
def recipe_line_add(recipe_id):
    recipe = db.session.get(Recipe, recipe_id) or abort(404)
    name = (request.form.get("name") or "").strip()
    qty = services.parse_tenths(request.form.get("qty")) or 10
    unit = request.form.get("unit") if request.form.get("unit") in UNITS else "g"
    ing = services.find_ingredient(name)
    if ing is None:
        if not name:
            return redirect(url_for("main.recipe_edit", recipe_id=recipe.id))
        return redirect(url_for("main.recipe_edit", recipe_id=recipe.id, new=name,
                                qty=services.fmt_tenths(qty), unit=unit))
    _append_line(recipe, ing, qty, unit)
    db.session.commit()
    return redirect(url_for("main.recipe_edit", recipe_id=recipe.id))


def _append_line(recipe, ing, qty_tenths, unit):
    order = max([ri.sort_order for ri in recipe.ingredients], default=-1) + 1
    recipe.ingredients.append(RecipeIngredient(ingredient=ing, qty_tenths=qty_tenths,
                                               unit=unit, sort_order=order))


@bp.post("/recipes/<int:recipe_id>/ingredients/create")
def recipe_line_create_ingredient(recipe_id):
    recipe = db.session.get(Recipe, recipe_id) or abort(404)
    name = (request.form.get("name") or "").strip()
    if not name:
        return redirect(url_for("main.recipe_edit", recipe_id=recipe.id))
    ing = services.find_ingredient(name)
    if ing is None:
        ing = Ingredient(name=name)
        _apply_ingredient_form(ing)
        db.session.add(ing)
    qty = services.parse_tenths(request.form.get("qty")) or 10
    unit = request.form.get("line_unit") if request.form.get("line_unit") in UNITS else ing.default_unit
    _append_line(recipe, ing, qty, unit)
    db.session.commit()
    return redirect(url_for("main.recipe_edit", recipe_id=recipe.id))


@bp.post("/recipes/<int:recipe_id>/ingredients/<int:line_id>")
def recipe_line_update(recipe_id, line_id):
    ri = db.session.get(RecipeIngredient, line_id) or abort(404)
    if ri.recipe_id != recipe_id:
        abort(404)
    ri.qty_tenths = services.parse_tenths(request.form.get("qty")) or ri.qty_tenths
    if request.form.get("unit") in UNITS:
        ri.unit = request.form["unit"]
    db.session.commit()
    return redirect(url_for("main.recipe_edit", recipe_id=recipe_id))


@bp.post("/recipes/<int:recipe_id>/ingredients/<int:line_id>/delete")
def recipe_line_delete(recipe_id, line_id):
    ri = db.session.get(RecipeIngredient, line_id) or abort(404)
    if ri.recipe_id != recipe_id:
        abort(404)
    db.session.delete(ri)
    db.session.commit()
    return redirect(url_for("main.recipe_edit", recipe_id=recipe_id))


@bp.get("/recipes/<int:recipe_id>/plan")
def recipe_plan(recipe_id):
    recipe = db.session.get(Recipe, recipe_id) or abort(404)
    return redirect(_week_url(g.today, dlg="pick", date=g.today.isoformat(), slot="dinner",
                              recipe=recipe.id))


# ---------- ingredients ----------

def _apply_ingredient_form(ing):
    form = request.form
    ing.name = (form.get("name") or "").strip() or ing.name
    ing.category = (form.get("category") or "").strip() or "Прочее"
    ing.kcal_100 = _int(form.get("kcal"))
    ing.protein_100_dg = services.to_dg(form.get("protein"))
    ing.fat_100_dg = services.to_dg(form.get("fat"))
    ing.carbs_100_dg = services.to_dg(form.get("carbs"))
    ing.default_unit = form.get("unit") if form.get("unit") in UNITS else "g"
    ing.piece_grams = _int(form.get("piece_grams"))
    ing.is_staple = bool(form.get("staple"))


@bp.get("/ingredients")
def ingredients():
    q = (request.args.get("q") or "").strip()
    category = request.args.get("category") or ""
    query = Ingredient.query.filter_by(archived=False)
    if q:
        query = query.filter(func.ulower(Ingredient.name).contains(q.lower()))
    if category:
        query = query.filter_by(category=category)
    if request.args.get("missing") == "1":
        query = query.filter(Ingredient.kcal_100.is_(None))
    rows = query.order_by(Ingredient.category, Ingredient.name).all()
    missing_total = Ingredient.query.filter_by(archived=False).filter(Ingredient.kcal_100.is_(None)).count()
    return render_template("ingredients.html", rows=rows, q=q, category=category,
                           sections=services.sections(), usage=services.recipe_usage(),
                           edit_id=_int(request.args.get("edit"), 0), missing_total=missing_total,
                           show_new=request.args.get("new") == "1")


@bp.post("/ingredients")
def ingredient_create():
    name = (request.form.get("name") or "").strip()
    if name and services.find_ingredient(name) is None:
        ing = Ingredient(name=name)
        _apply_ingredient_form(ing)
        db.session.add(ing)
        db.session.commit()
    return _back(url_for("main.ingredients"))


@bp.post("/ingredients/<int:ingredient_id>")
def ingredient_save(ingredient_id):
    ing = db.session.get(Ingredient, ingredient_id) or abort(404)
    _apply_ingredient_form(ing)
    if ing.source == "seed":
        ing.source = "manual"
    db.session.commit()
    return _back(url_for("main.ingredients"))


@bp.post("/ingredients/<int:ingredient_id>/archive")
def ingredient_archive(ingredient_id):
    ing = db.session.get(Ingredient, ingredient_id) or abort(404)
    ing.archived = True
    db.session.commit()
    return _back(url_for("main.ingredients"))


# ---------- shopping ----------

@bp.get("/shopping")
def shopping():
    preset = request.args.get("period") or services.get_setting("shopping_period")
    if preset not in dict(services.PERIODS):
        preset = "days3"
    d_from = _date(request.args.get("from"), g.today) if request.args.get("from") else None
    d_to = _date(request.args.get("to"), g.today) if request.args.get("to") else None
    if preset == "custom" and not (d_from and d_to):
        d_from, d_to = g.today, g.today + timedelta(days=2)
    start, end = services.period_range(preset, g.today, d_from, d_to)
    data = services.shopping_items(start, end, g.today)
    return render_template("shopping.html", preset=preset, start=start, end=end,
                           label=services.fmt_range(start, end), data=data,
                           here=request.full_path.rstrip("?"))


@bp.post("/shopping/marks/<int:ingredient_id>")
def shopping_mark(ingredient_id):
    state = request.form.get("state") if request.form.get("state") in ("todo", "bought", "have") else "todo"
    valid_until = _date(request.form.get("valid_until"), g.today)
    services.set_mark(ingredient_id, state, max(valid_until, g.today))
    db.session.commit()
    return _back(url_for("main.shopping"))


@bp.post("/shopping/extras")
def shopping_extra_add():
    name = (request.form.get("name") or "").strip()
    if name:
        db.session.add(ShoppingExtra(name=name, qty_text=(request.form.get("qty") or "").strip() or None))
        db.session.commit()
    return _back(url_for("main.shopping"))


@bp.post("/shopping/extras/<int:extra_id>/state")
def shopping_extra_state(extra_id):
    extra = db.session.get(ShoppingExtra, extra_id) or abort(404)
    extra.state = "todo" if extra.state == "bought" else "bought"
    db.session.commit()
    return _back(url_for("main.shopping"))


@bp.post("/shopping/extras/<int:extra_id>/delete")
def shopping_extra_delete(extra_id):
    extra = db.session.get(ShoppingExtra, extra_id) or abort(404)
    db.session.delete(extra)
    db.session.commit()
    return _back(url_for("main.shopping"))


@bp.post("/shopping/clear")
def shopping_clear():
    from .models import ShoppingMark
    ShoppingMark.query.filter_by(state="bought").delete()
    ShoppingExtra.query.filter_by(state="bought").delete()
    db.session.commit()
    return _back(url_for("main.shopping"))


# ---------- settings ----------

@bp.post("/settings")
def settings_save():
    form = request.form
    services.set_setting("repeat_days", str(max(_int(form.get("repeat_days"), 14) or 0, 0)))
    services.set_setting("default_guests", str(max(_int(form.get("default_guests"), 0) or 0, 0)))
    if form.get("shopping_period") in dict(services.PERIODS):
        services.set_setting("shopping_period", form["shopping_period"])
    slots = [s for s in form.getlist("visible_slots") if s in SLOTS]
    services.set_setting("visible_slots", ",".join(slots or ["dinner"]))
    services.set_setting("round_grams", str(max(_int(form.get("round_grams"), 50) or 10, 1)))
    services.set_setting("weekday_tag", (form.get("weekday_tag") or "").strip())
    sections = [s.strip() for s in (form.get("sections") or "").splitlines() if s.strip()]
    if sections:
        services.set_setting("sections", ",".join(sections))
    eaters = [s for s in form.getlist("default_eaters") if s in ("left", "right")]
    services.set_setting("default_eaters", ",".join(eaters))
    services.set_setting("show_macros", "1" if form.get("show_macros") else "0")
    db.session.commit()
    return _back(url_for("main.index"))


@bp.post("/tags")
def tag_add():
    name = (request.form.get("name") or "").strip()
    if name and not Tag.query.filter(func.ulower(Tag.name) == name.lower()).first():
        order = max([t.sort_order for t in Tag.query.all()], default=-1) + 1
        db.session.add(Tag(name=name, sort_order=order))
        db.session.commit()
    return _back(url_for("main.index"))


@bp.post("/tags/<int:tag_id>/delete")
def tag_delete(tag_id):
    tag = db.session.get(Tag, tag_id) or abort(404)
    db.session.delete(tag)
    db.session.commit()
    return _back(url_for("main.index"))


@bp.post("/prefs")
def prefs():
    if g.user is None:
        return ("", 204)
    accent = request.form.get("accent")
    theme = request.form.get("theme")
    if accent in ("teal", "violet", "ocean", "coral", "graphite", "rose"):
        g.user.accent_color = accent
    if theme in ("system", "light", "dark"):
        g.user.theme = theme
    db.session.commit()
    return ("", 204)
