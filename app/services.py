"""Pure-ish business logic: units and macros, calendar helpers, shopping
aggregation, the week randomizer. Routes stay thin."""
import math
import random
from datetime import date, timedelta

from . import db
from .models import (
    DEFAULT_SECTIONS, SLOTS, UNIT_LABEL, Ingredient, MealEntry, MealServing,
    Recipe, RecipeIngredient, Setting, ShoppingExtra, ShoppingMark, Tag, User,
)

DAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
DAY_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTH_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MONTH_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

DEFAULT_TAGS = ["быстро", "будни", "выходные", "вегетарианское", "суп", "на два дня", "завтрак"]

SETTING_DEFAULTS = {
    "repeat_days": "14",
    "default_guests": "0",
    "shopping_period": "days3",
    "visible_slots": "breakfast,lunch,dinner",
    "round_grams": "50",
    "weekday_tag": "быстро",
    "two_days_tag": "на два дня",
    "sections": ",".join(DEFAULT_SECTIONS),
    "default_eaters": "left,right",
    "show_macros": "0",
}

PERIODS = [("today", "Сегодня"), ("tomorrow", "Завтра"), ("days3", "3 дня"),
           ("week", "Неделя"), ("custom", "Даты")]

UNIT_ML = {"ml": 1, "tbsp": 15, "tsp": 5}
NBSP = " "


def today():
    return date.today()


# ---------- settings ----------

def get_setting(key):
    row = db.session.get(Setting, key)
    return row.value if row else SETTING_DEFAULTS[key]


def set_setting(key, value):
    row = db.session.get(Setting, key)
    if row is None:
        db.session.add(Setting(key=key, value=value))
    else:
        row.value = value


def visible_slots():
    wanted = [s for s in get_setting("visible_slots").split(",") if s]
    return [s for s in SLOTS if s in wanted] or ["dinner"]


def sections():
    return [s.strip() for s in get_setting("sections").split(",") if s.strip()]


def default_eaters():
    sides = get_setting("default_eaters").split(",")
    return User.query.filter(User.side.in_(sides)).order_by(User.side).all()


def ensure_tags():
    existing = {t.name for t in Tag.query.all()}
    for i, name in enumerate(DEFAULT_TAGS):
        if name not in existing:
            db.session.add(Tag(name=name, sort_order=i))


# ---------- numbers ----------

def fmt_int(n):
    return f"{int(round(n)):,}".replace(",", NBSP)


def fmt_tenths(tenths):
    if tenths is None:
        return ""
    value = tenths / 10
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}".replace(".", ",")


def fmt_g(grams):
    """Grams with at most one decimal: 42 -> «42», 1.5 -> «1,5»."""
    if grams is None:
        return "—"
    return fmt_tenths(round(grams * 10))


def to_dg(text):
    value = parse_decimal(text)
    return None if value is None else int(round(value * 10))


def parse_decimal(text):
    if text is None:
        return None
    cleaned = str(text).replace(NBSP, "").replace(" ", "").replace(",", ".").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_tenths(text):
    value = parse_decimal(text)
    return None if value is None else int(round(value * 10))


def fmt_qty(qty_tenths, unit):
    return f"{fmt_tenths(qty_tenths)}{NBSP}{UNIT_LABEL.get(unit, unit)}"


# ---------- units and macros ----------

def grams_of(qty_tenths, unit, ing):
    """Grams for a recipe line, or None when it cannot be computed
    («шт» without piece weight)."""
    qty = qty_tenths / 10
    if unit == "g":
        return qty
    if unit == "pcs":
        return qty * ing.piece_grams if ing.piece_grams else None
    ml = qty * UNIT_ML.get(unit, 1)
    return ml * (ing.density_x100 or 100) / 100


def line_macros(ri):
    """(grams, kcal, protein_g, fat_g, carbs_g) for one recipe line; kcal is
    None when the ingredient has no data."""
    ing = ri.ingredient
    grams = grams_of(ri.qty_tenths, ri.unit, ing)
    if grams is None or not ing.has_macros:
        return grams, None, None, None, None
    return (
        grams,
        grams * ing.kcal_100 / 100,
        grams * (ing.protein_100_dg or 0) / 1000,
        grams * (ing.fat_100_dg or 0) / 1000,
        grams * (ing.carbs_100_dg or 0) / 1000,
    )


def recipe_totals(recipe):
    kcal = protein = fat = carbs = 0.0
    grams_total = 0.0
    missing = []
    counted = 0
    for ri in recipe.ingredients:
        grams, k, p, f, c = line_macros(ri)
        if grams is not None:
            grams_total += grams
        if k is None:
            missing.append(ri.ingredient.name)
            continue
        counted += 1
        kcal += k
        protein += p
        fat += f
        carbs += c
    portions = max(recipe.portions or 1, 1)
    weight = recipe.cooked_weight_g or grams_total
    return {
        "total": {"kcal": kcal, "protein": protein, "fat": fat, "carbs": carbs},
        "per_portion": {"kcal": kcal / portions, "protein": protein / portions,
                        "fat": fat / portions, "carbs": carbs / portions},
        "per_100g": (kcal * 100 / weight) if weight else None,
        "portion_grams": weight / portions if weight else None,
        "missing": missing,
        "complete": not missing,
        "has_data": counted > 0,
    }


def entry_kcal(entry):
    """Per-portion kcal of the entry's recipe, or None when unknown."""
    if entry.recipe is None:
        return None
    totals = recipe_totals(entry.recipe)
    return totals["per_portion"]["kcal"] if totals["has_data"] else None


def scaled_qty(ri, factor):
    return int(round(ri.qty_tenths * factor))


# ---------- calendar ----------

def week_of(d):
    iso = d.isocalendar()
    return iso[0], iso[1]


def week_dates(year, week):
    start = date.fromisocalendar(year, week, 1)
    return [start + timedelta(days=i) for i in range(7)]


def fmt_range(d1, d2):
    if d1 == d2:
        return f"{d1.day} {MONTH_GEN[d1.month - 1]}"
    if d1.month == d2.month:
        return f"{d1.day} — {d2.day} {MONTH_GEN[d2.month - 1]}"
    return f"{d1.day} {MONTH_GEN[d1.month - 1]} — {d2.day} {MONTH_GEN[d2.month - 1]}"


def fmt_long(d):
    return f"{DAY_FULL[d.weekday()]}, {d.day} {MONTH_GEN[d.month - 1]}"


def fmt_short(d):
    return f"{DAY_SHORT[d.weekday()].lower()} {d.day}"


def fmt_since(d, ref):
    if d is None:
        return "ещё не готовили"
    delta = (ref - d).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "вчера"
    if delta < 0:
        return f"в плане: {fmt_short(d)}"
    if delta <= 13:
        return f"{delta} дн. назад"
    return f"{d.day} {MONTH_SHORT[d.month - 1]}"


# ---------- plan entries ----------

def entries_for(dates):
    rows = (MealEntry.query.filter(MealEntry.date >= dates[0], MealEntry.date <= dates[-1])
            .order_by(MealEntry.sort_order, MealEntry.id).all())
    by_cell = {}
    for e in rows:
        by_cell.setdefault((e.date, e.slot), []).append(e)
    return by_cell


def servings_map(entry):
    return {s.user_id: s.portions_tenths for s in entry.servings}


def own_portions_tenths(entry):
    return sum(s.portions_tenths for s in entry.servings) + entry.guest_portions_tenths


def total_portions_tenths(entry):
    """Portions a cook entry must produce: its own eaters + guests + every
    leftover entry that eats from it."""
    return own_portions_tenths(entry) + sum(own_portions_tenths(c) for c in entry.children)


def create_entry(d, slot, recipe=None, free_text=None, servings=None,
                 guests_tenths=0, leftover_slot=None):
    entry = MealEntry(date=d, slot=slot, recipe=recipe, free_text=free_text,
                      guest_portions_tenths=guests_tenths)
    for user_id, tenths in (servings or {}).items():
        if tenths > 0:
            entry.servings.append(MealServing(user_id=user_id, portions_tenths=tenths))
    db.session.add(entry)
    if leftover_slot and recipe is not None:
        add_leftover(entry, leftover_slot, servings)
    db.session.flush()
    return entry


def add_leftover(entry, slot, servings=None):
    child = MealEntry(date=entry.date + timedelta(days=1), slot=slot,
                      recipe=entry.recipe, parent=entry)
    for user_id, tenths in (servings or servings_map(entry)).items():
        if tenths > 0:
            child.servings.append(MealServing(user_id=user_id, portions_tenths=tenths))
    db.session.add(child)
    return child


def set_servings(entry, servings, guests_tenths):
    existing = {s.user_id: s for s in entry.servings}
    for user_id, tenths in servings.items():
        if tenths > 0:
            if user_id in existing:
                existing[user_id].portions_tenths = tenths
            else:
                entry.servings.append(MealServing(user_id=user_id, portions_tenths=tenths))
        elif user_id in existing:
            db.session.delete(existing[user_id])
    entry.guest_portions_tenths = guests_tenths


def last_cooked(recipe, ref):
    row = (MealEntry.query.filter(MealEntry.recipe_id == recipe.id,
                                  MealEntry.leftover_of_id.is_(None),
                                  MealEntry.date <= ref)
           .order_by(MealEntry.date.desc()).first())
    return row.date if row else None


def last_cooked_map(ref):
    rows = (db.session.query(MealEntry.recipe_id, db.func.max(MealEntry.date))
            .filter(MealEntry.leftover_of_id.is_(None), MealEntry.date <= ref,
                    MealEntry.recipe_id.isnot(None))
            .group_by(MealEntry.recipe_id).all())
    return {rid: d for rid, d in rows}


# ---------- shopping ----------

def period_range(preset, ref, d_from=None, d_to=None):
    if preset == "today":
        return ref, ref
    if preset == "tomorrow":
        return ref + timedelta(days=1), ref + timedelta(days=1)
    if preset == "week":
        return ref, ref + timedelta(days=6)
    if preset == "custom" and d_from and d_to:
        return (d_from, d_to) if d_from <= d_to else (d_to, d_from)
    return ref, ref + timedelta(days=2)  # days3


def round_amount(value, step):
    if value <= 0:
        return 0
    if value >= 200:
        return int(math.ceil(value / step - 1e-9) * step)
    return int(math.ceil(value / 10 - 1e-9) * 10)


def _display_qty(ing, grams, pieces, step):
    unit = ing.default_unit
    if unit == "pcs":
        n = pieces + (grams / ing.piece_grams if ing.piece_grams and grams else 0)
        return f"{int(math.ceil(n - 1e-9))}{NBSP}шт"
    if unit in UNIT_ML:
        ml = grams / ((ing.density_x100 or 100) / 100)
        return f"{fmt_int(round_amount(ml, step))}{NBSP}мл"
    return f"{fmt_int(round_amount(grams, step))}{NBSP}г"


def shopping_items(d_from, d_to, ref):
    entries = (MealEntry.query.filter(MealEntry.date >= d_from, MealEntry.date <= d_to,
                                      MealEntry.leftover_of_id.is_(None),
                                      MealEntry.recipe_id.isnot(None)).all())
    acc = {}
    for e in entries:
        recipe = e.recipe
        scale = (total_portions_tenths(e) / 10) / max(recipe.portions or 1, 1)
        for ri in recipe.ingredients:
            a = acc.setdefault(ri.ingredient_id, {"ingredient": ri.ingredient, "grams": 0.0,
                                                  "pieces": 0.0, "dishes": set()})
            grams = grams_of(ri.qty_tenths, ri.unit, ri.ingredient)
            if grams is None:
                a["pieces"] += ri.qty_tenths / 10 * scale
            else:
                a["grams"] += grams * scale
            a["dishes"].add((e.date, recipe.name))

    marks = {m.ingredient_id: m.state
             for m in ShoppingMark.query.filter(ShoppingMark.valid_until >= ref)}
    step = int(get_setting("round_grams"))
    order = {"todo": 0, "have": 1, "bought": 2}

    items = []
    for a in acc.values():
        ing = a["ingredient"]
        items.append({
            "ingredient": ing,
            "qty": _display_qty(ing, a["grams"], a["pieces"], step),
            "state": marks.get(ing.id, "todo"),
            "dishes": [f"{fmt_short(d)} · {name}" for d, name in sorted(a["dishes"])],
        })

    known = sections()
    by_section = {}
    staples = []
    for item in items:
        if item["ingredient"].is_staple:
            staples.append(item)
        else:
            cat = item["ingredient"].category if item["ingredient"].category in known else known[-1]
            by_section.setdefault(cat, []).append(item)
    result_sections = []
    for name in known:
        rows = by_section.get(name)
        if rows:
            rows.sort(key=lambda i: (order[i["state"]], i["ingredient"].name.lower()))
            result_sections.append({"name": name, "items": rows})
    staples.sort(key=lambda i: (order[i["state"]], i["ingredient"].name.lower()))

    extras = ShoppingExtra.query.order_by(ShoppingExtra.created_at).all()
    extras.sort(key=lambda x: (0 if x.state == "todo" else 1))
    dishes = sorted({(d, n) for a in acc.values() for d, n in a["dishes"]})
    total = len(items) + len(extras)
    done = sum(1 for i in items if i["state"] != "todo") + sum(1 for x in extras if x.state != "todo")
    return {"sections": result_sections, "staples": staples, "extras": extras,
            "dishes": [f"{fmt_short(d)} · {n}" for d, n in dishes],
            "total": total, "done": done}


def set_mark(ingredient_id, state, valid_until):
    mark = db.session.get(ShoppingMark, ingredient_id)
    if state == "todo":
        if mark:
            db.session.delete(mark)
        return
    if mark is None:
        db.session.add(ShoppingMark(ingredient_id=ingredient_id, state=state, valid_until=valid_until))
    else:
        mark.state = state
        mark.valid_until = valid_until


# ---------- fill the week ----------

def encode_proposal(p):
    parent = p.get("leftover_of")
    tail = f"{parent[0].isoformat()}|{parent[1]}" if parent else "|"
    return f"{p['date'].isoformat()}|{p['slot']}|{p['recipe'].id}|{tail}"


def decode_proposals(values):
    out = []
    for raw in values:
        parts = raw.split("|")
        if len(parts) != 5:
            continue
        recipe = db.session.get(Recipe, int(parts[2]))
        if recipe is None:
            continue
        parent = (date.fromisoformat(parts[3]), parts[4]) if parts[3] else None
        out.append({"date": date.fromisoformat(parts[0]), "slot": parts[1],
                    "recipe": recipe, "leftover_of": parent})
    return out


def recipe_pool(dates, repeat_days):
    window_start = dates[0] - timedelta(days=repeat_days)
    recently = {rid for (rid,) in db.session.query(MealEntry.recipe_id)
                .filter(MealEntry.date >= window_start, MealEntry.date <= dates[-1],
                        MealEntry.leftover_of_id.is_(None), MealEntry.recipe_id.isnot(None))}
    return [r for r in Recipe.query.filter_by(archived=False).order_by(Recipe.id)
            if r.id not in recently]


def fill_cells(cells, pool, used, filled, rng, weekday_tag, allow_two_days, two_days_tag):
    proposals = []
    for d, slot in cells:
        if (d, slot) in filled:
            continue
        cands = [r for r in pool if r.id not in used]
        if not cands:
            break
        if weekday_tag and slot == "dinner" and d.weekday() < 5:
            preferred = [r for r in cands if r.has_tag(weekday_tag)]
            if preferred:
                cands = preferred
        recipe = rng.choice(cands)
        used.add(recipe.id)
        filled.add((d, slot))
        proposals.append({"date": d, "slot": slot, "recipe": recipe, "leftover_of": None})
        if allow_two_days and slot == "dinner" and recipe.has_tag(two_days_tag):
            nxt = (d + timedelta(days=1), "lunch")
            if nxt not in filled:
                filled.add(nxt)
                proposals.append({"date": nxt[0], "slot": "lunch", "recipe": recipe,
                                  "leftover_of": (d, slot)})
    return proposals


def fill_week(dates, slots, only_empty, repeat_days, weekday_tag, allow_two_days, seed):
    rng = random.Random(seed)
    existing = set(entries_for(dates).keys()) if only_empty else set()
    cells = [(d, s) for d in dates for s in SLOTS if s in slots]
    pool = recipe_pool(dates, repeat_days)
    return fill_cells(cells, pool, set(), set(existing), rng, weekday_tag, allow_two_days,
                      get_setting("two_days_tag"))


def reroll(proposals, dates, key, repeat_days, weekday_tag, allow_two_days, seed):
    """Replace one proposed cell (and its leftover child) with another recipe."""
    rng = random.Random(seed)
    old = next((p for p in proposals if (p["date"], p["slot"]) == key), None)
    kept = [p for p in proposals if (p["date"], p["slot"]) != key
            and p.get("leftover_of") != key]
    used = {p["recipe"].id for p in kept}
    if old:
        used.add(old["recipe"].id)
    filled = set(entries_for(dates).keys()) | {(p["date"], p["slot"]) for p in kept}
    pool = recipe_pool(dates, repeat_days)
    fresh = fill_cells([key], pool, used, filled, rng, weekday_tag, allow_two_days,
                       get_setting("two_days_tag"))
    if not fresh and old:
        fresh = [old]
    return sorted(kept + fresh, key=lambda p: (p["date"], SLOTS.index(p["slot"])))


def accept_proposals(proposals, eaters, guests_tenths):
    servings = {u.id: 10 for u in eaters}
    parents = {}
    created = 0
    for p in sorted(proposals, key=lambda p: (p["leftover_of"] is not None, p["date"])):
        if p["leftover_of"] is None:
            entry = create_entry(p["date"], p["slot"], recipe=p["recipe"], servings=servings,
                                 guests_tenths=guests_tenths)
            parents[(p["date"], p["slot"])] = entry
            created += 1
        else:
            parent = parents.get(p["leftover_of"])
            if parent is None:
                parent = (MealEntry.query.filter_by(date=p["leftover_of"][0], slot=p["leftover_of"][1],
                                                    recipe_id=p["recipe"].id, leftover_of_id=None).first())
            if parent is None:
                continue
            add_leftover(parent, p["slot"], servings)
            created += 1
    return created


# ---------- ingredients ----------

def find_ingredient(name):
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    return Ingredient.query.filter(db.func.ulower(Ingredient.name) == cleaned.lower()).first()


def recipe_usage():
    rows = (db.session.query(RecipeIngredient.ingredient_id, db.func.count(RecipeIngredient.id))
            .group_by(RecipeIngredient.ingredient_id).all())
    return {iid: n for iid, n in rows}


# ---------- demo data ----------

def seed_demo_recipes():
    """A handful of recipes built from the seed library, for a first local run."""
    demo = [
        ("Куриная грудка с рисом", 4, 35, ["будни", "быстро"],
         [("Куриная грудка, сырая", 6000, "g"), ("Рис", 2500, "g"), ("Лук репчатый", 10, "pcs"),
          ("Масло оливковое", 20, "tbsp"), ("Сметана 15 %", 700, "g")],
         "Рис промыть, залить водой 1:1,5, варить 12 минут под крышкой.\n"
         "Грудку нарезать, обжарить с луком 8–10 минут.\nДобавить сметану, потушить 3 минуты."),
        ("Борщ", 6, 90, ["суп", "на два дня", "выходные"],
         [("Говядина", 6000, "g"), ("Свёкла", 5000, "g"), ("Капуста белокочанная", 4000, "g"),
          ("Картофель", 40, "pcs"), ("Морковь", 20, "pcs"), ("Лук репчатый", 10, "pcs"),
          ("Томатная паста", 20, "tbsp"), ("Сметана 15 %", 1000, "g")],
         "Бульон из говядины 1 час, снять пену.\nСвёклу натереть, потушить с томатной пастой 15 минут.\n"
         "В бульон картофель и капусту, через 10 минут — овощи. Ещё 10 минут."),
        ("Паста болоньезе", 4, 50, ["выходные", "на два дня"],
         [("Макароны", 4000, "g"), ("Говяжий фарш", 5000, "g"), ("Помидоры", 40, "pcs"),
          ("Лук репчатый", 10, "pcs"), ("Чеснок", 20, "pcs"), ("Масло оливковое", 20, "tbsp"),
          ("Сыр твёрдый", 800, "g")],
         "Обжарить лук и чеснок, добавить фарш.\nПомидоры, тушить 25 минут.\nПаста аль денте, смешать."),
        ("Омлет с овощами", 2, 15, ["завтрак", "быстро"],
         [("Яйцо куриное", 40, "pcs"), ("Молоко 2,5 %", 600, "ml"), ("Перец болгарский", 10, "pcs"),
          ("Помидоры", 10, "pcs"), ("Масло сливочное", 100, "g")],
         "Овощи обжарить 3 минуты.\nЯйца взбить с молоком, вылить, под крышкой 5 минут."),
        ("Греческий салат", 2, 15, ["быстро", "вегетарианское"],
         [("Огурцы", 20, "pcs"), ("Помидоры", 30, "pcs"), ("Сыр фета", 1500, "g"),
          ("Оливки", 600, "g"), ("Масло оливковое", 20, "tbsp"), ("Лук репчатый", 5, "pcs")],
         "Нарезать крупно, заправить маслом."),
        ("Чечевичный суп", 4, 40, ["суп", "вегетарианское", "на два дня"],
         [("Чечевица", 3000, "g"), ("Морковь", 20, "pcs"), ("Лук репчатый", 10, "pcs"),
          ("Картофель", 20, "pcs"), ("Томатная паста", 10, "tbsp"), ("Масло оливковое", 10, "tbsp")],
         "Овощи обжарить, добавить чечевицу и 1,5 л воды.\nВарить 25 минут."),
        ("Лосось с картофелем", 2, 40, ["выходные"],
         [("Лосось, филе", 4000, "g"), ("Картофель", 60, "pcs"), ("Масло оливковое", 10, "tbsp"),
          ("Лимон", 5, "pcs")],
         "Картофель в духовку на 20 минут при 200°.\nЛосось сверху ещё на 15 минут."),
        ("Гречка с грибами", 3, 30, ["будни", "вегетарианское"],
         [("Гречка", 3000, "g"), ("Грибы шампиньоны", 3000, "g"), ("Лук репчатый", 10, "pcs"),
          ("Масло сливочное", 200, "g")],
         "Гречку варить 15 минут.\nГрибы с луком обжарить, смешать."),
    ]
    tags = {t.name: t for t in Tag.query.all()}
    created = 0
    for name, portions, minutes, tag_names, lines, steps in demo:
        if Recipe.query.filter_by(name=name).first():
            continue
        recipe = Recipe(name=name, portions=portions, prep_minutes=minutes, steps=steps)
        recipe.tags = [tags[t] for t in tag_names if t in tags]
        for i, (ing_name, qty_tenths, unit) in enumerate(lines):
            ing = find_ingredient(ing_name)
            if ing is None:
                continue
            recipe.ingredients.append(RecipeIngredient(ingredient=ing, qty_tenths=qty_tenths,
                                                       unit=unit, sort_order=i))
        db.session.add(recipe)
        created += 1
    db.session.commit()
    return created
