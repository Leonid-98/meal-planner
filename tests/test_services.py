from datetime import date
import random

import pytest

from app import db, services
from app.models import Ingredient, MealEntry, Recipe, RecipeIngredient, Tag, User


def ing(name, kcal=100, protein=10, fat=5, carbs=20, unit="g", piece=None, density=100, staple=False):
    clash = services.find_ingredient(name)  # the seed library may already have this name
    if clash is not None:
        db.session.delete(clash)
        db.session.flush()
    row = Ingredient(name=name, kcal_100=kcal, protein_100_dg=protein * 10, fat_100_dg=fat * 10,
                     carbs_100_dg=carbs * 10, default_unit=unit, piece_grams=piece,
                     density_x100=density, is_staple=staple, category="Бакалея")
    db.session.add(row)
    db.session.flush()
    return row


def recipe(name, portions, lines, tags=()):
    r = Recipe(name=name, portions=portions)
    r.tags = [Tag.query.filter_by(name=t).first() for t in tags]
    for i, (ingredient, qty_tenths, unit) in enumerate(lines):
        r.ingredients.append(RecipeIngredient(ingredient=ingredient, qty_tenths=qty_tenths, unit=unit, sort_order=i))
    db.session.add(r)
    db.session.flush()
    return r


def test_grams_of_units(app):
    with app.app_context():
        egg = ing("Яйцо", unit="pcs", piece=55)
        oil = ing("Масло", unit="tbsp", density=92)
        flour = ing("Мука")
        nopiece = ing("Лаврушка", unit="pcs")
        assert services.grams_of(20, "pcs", egg) == 110
        assert services.grams_of(20, "tbsp", oil) == pytest.approx(27.6)
        assert services.grams_of(2500, "g", flour) == 250
        assert services.grams_of(10, "pcs", nopiece) is None


def test_recipe_totals_and_missing_data(app):
    with app.app_context():
        chicken = ing("Курица", kcal=110, protein=23, fat=1.2, carbs=0)
        rice = ing("Рис", kcal=345, protein=7.5, fat=0.6, carbs=78)
        sauce = ing("Соус", kcal=None)
        sauce.protein_100_dg = None
        r = recipe("Курица с рисом", 4, [(chicken, 6000, "g"), (rice, 2500, "g"), (sauce, 30, "tbsp")])
        t = services.recipe_totals(r)
        assert round(t["total"]["kcal"]) == 660 + 862 or round(t["total"]["kcal"]) == 660 + 863
        assert round(t["per_portion"]["kcal"]) in (380, 381)
        assert t["per_portion"]["protein"] == pytest.approx((600 * 0.23 + 250 * 0.075) / 4)
        assert t["missing"] == ["Соус"]
        assert not t["complete"] and t["has_data"]
        r.cooked_weight_g = 1000
        assert round(services.recipe_totals(r)["per_100g"]) in (152, 153)


def test_number_formatting():
    assert services.fmt_tenths(15) == "1,5"
    assert services.fmt_tenths(20) == "2"
    assert services.parse_tenths("1,5") == 15
    assert services.parse_tenths("0.5") == 5
    assert services.parse_tenths("") is None
    assert services.fmt_int(1234) == "1 234"
    assert services.fmt_qty(15, "tbsp") == "1,5 ст. л."
    assert services.round_amount(137, 50) == 140
    assert services.round_amount(318, 50) == 350
    assert services.round_amount(600, 50) == 600


def test_calendar_helpers():
    d = date(2026, 9, 11)
    assert services.week_of(d) == (2026, 37)
    dates = services.week_dates(2026, 37)
    assert dates[0] == date(2026, 9, 7) and dates[-1] == date(2026, 9, 13)
    assert services.fmt_range(dates[0], dates[-1]) == "7 — 13 сентября"
    assert services.fmt_range(date(2026, 9, 28), date(2026, 10, 4)) == "28 сентября — 4 октября"
    assert services.fmt_long(d) == "пятница, 11 сентября"
    assert services.fmt_since(d, d) == "сегодня"
    assert services.fmt_since(date(2026, 9, 1), d) == "10 дн. назад"
    assert services.fmt_since(date(2026, 8, 1), d) == "1 авг"
    assert services.fmt_since(None, d) == "ещё не готовили"
    assert services.period_range("today", d) == (d, d)
    assert services.period_range("tomorrow", d) == (date(2026, 9, 12), date(2026, 9, 12))
    assert services.period_range("days3", d) == (d, date(2026, 9, 13))
    assert services.period_range("week", d) == (d, date(2026, 9, 17))
    assert services.period_range("custom", d, date(2026, 9, 20), date(2026, 9, 15)) == (date(2026, 9, 15), date(2026, 9, 20))


def test_leftover_scales_shopping_but_is_not_listed_twice(app):
    with app.app_context():
        users = User.query.order_by(User.side).all()
        chicken = ing("Курица", kcal=110)
        onion = ing("Лук", unit="pcs", piece=110)
        salt = ing("Соль", unit="tsp", staple=True)
        r = recipe("Курица", 4, [(chicken, 6000, "g"), (onion, 10, "pcs"), (salt, 10, "tsp")])
        d = date(2026, 9, 7)
        entry = services.create_entry(d, "dinner", recipe=r, servings={users[0].id: 15, users[1].id: 10},
                                      guests_tenths=0, leftover_slot="lunch")
        db.session.commit()
        assert len(entry.children) == 1
        child = entry.children[0]
        assert child.date == date(2026, 9, 8) and child.slot == "lunch" and child.is_leftover
        assert services.total_portions_tenths(entry) == 15 + 10 + 15 + 10  # child copies servings

        data = services.shopping_items(d, date(2026, 9, 13), d)
        names = {i["ingredient"].name: i for s in data["sections"] for i in s["items"]}
        # 5 portions of a 4-portion recipe: 600 g × 1.25 = 750 g; onion 1 × 1.25 → 2 pcs
        assert names["Курица"]["qty"] == "750 г"
        assert names["Лук"]["qty"] == "2 шт"
        assert [s["ingredient"].name for s in data["staples"]] == ["Соль"]
        assert data["total"] == 3 and data["done"] == 0

        # only the cook day counts: the leftover day alone has nothing to buy
        assert services.shopping_items(date(2026, 9, 8), date(2026, 9, 8), d)["total"] == 0


def test_shopping_marks_expire_with_their_period(app):
    with app.app_context():
        chicken = ing("Курица")
        r = recipe("Курица", 2, [(chicken, 4000, "g")])
        services.create_entry(date(2026, 9, 11), "dinner", recipe=r, servings={})
        services.create_entry(date(2026, 9, 16), "dinner", recipe=r, servings={})
        services.set_mark(chicken.id, "bought", date(2026, 9, 13))
        db.session.commit()
        today = date(2026, 9, 11)
        assert services.shopping_items(today, today, today)["sections"][0]["items"][0]["state"] == "bought"
        later = date(2026, 9, 15)
        assert services.shopping_items(later, date(2026, 9, 17), later)["sections"][0]["items"][0]["state"] == "todo"
        services.set_mark(chicken.id, "todo", today)
        db.session.commit()
        assert services.shopping_items(today, today, today)["sections"][0]["items"][0]["state"] == "todo"


def test_fill_week_respects_rules(app):
    with app.app_context():
        flour = ing("Мука")
        quick = recipe("Омлет", 2, [(flour, 100, "g")], tags=["быстро"])
        soup = recipe("Борщ", 6, [(flour, 100, "g")], tags=["на два дня"])
        slow = recipe("Плов", 6, [(flour, 100, "g")])
        recent = recipe("Недавнее", 2, [(flour, 100, "g")])
        dates = services.week_dates(2026, 37)
        # cooked 5 days before the week → excluded by the 14-day rule
        services.create_entry(dates[0].replace(day=2), "dinner", recipe=recent, servings={})
        # Monday dinner already planned → untouched with only_empty
        services.create_entry(dates[0], "dinner", recipe=slow, servings={})
        db.session.commit()

        proposals = services.fill_week(dates, ["dinner"], True, 14, "быстро", True, seed=1)
        cells = {(p["date"], p["slot"]) for p in proposals}
        assert (dates[0], "dinner") not in cells
        assert all(p["recipe"].name != "Недавнее" for p in proposals)
        assert all(p["recipe"].name != "Плов" for p in proposals)  # already in the week
        cooks = [p for p in proposals if p["leftover_of"] is None]
        assert len({p["recipe"].id for p in cooks}) == len(cooks)  # no repeats
        weekday_dinner = next(p for p in cooks if p["date"].weekday() < 5)
        assert weekday_dinner["recipe"].name == "Омлет"  # preferred tag on weekdays
        borsch = next(p for p in cooks if p["recipe"].name == "Борщ")
        leftover = next(p for p in proposals if p["leftover_of"] == (borsch["date"], "dinner"))
        assert leftover["slot"] == "lunch" and leftover["date"] == borsch["date"].replace(day=borsch["date"].day + 1)

        assert services.fill_week(dates, ["dinner"], True, 14, "быстро", True, seed=1) == proposals

        encoded = [services.encode_proposal(p) for p in proposals]
        assert services.decode_proposals(encoded) == proposals

        created = services.accept_proposals(proposals, User.query.all(), 0)
        db.session.commit()
        assert created == len(proposals)
        leftovers = MealEntry.query.filter(MealEntry.leftover_of_id.isnot(None)).all()
        assert len(leftovers) == 1 and leftovers[0].parent.recipe.name == "Борщ"


def test_reroll_changes_only_one_cell(app):
    with app.app_context():
        flour = ing("Мука")
        for name in ["А", "Б", "В", "Г"]:
            recipe(name, 2, [(flour, 100, "g")])
        db.session.commit()
        dates = services.week_dates(2026, 37)
        proposals = services.fill_week(dates[:3], ["dinner"], True, 14, None, False, seed=3)
        assert len(proposals) == 3
        key = (proposals[1]["date"], "dinner")
        rerolled = services.reroll(proposals, dates[:3], key, 14, None, False, seed=9)
        assert len(rerolled) == 3
        others = {(p["date"], p["recipe"].id) for p in proposals if (p["date"], p["slot"]) != key}
        assert others <= {(p["date"], p["recipe"].id) for p in rerolled}
        new = next(p for p in rerolled if (p["date"], p["slot"]) == key)
        assert new["recipe"].id != proposals[1]["recipe"].id
