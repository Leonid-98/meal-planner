from datetime import date

from werkzeug.datastructures import MultiDict

from app import db, services
from app.models import Ingredient, MealEntry, Recipe, ShoppingExtra, ShoppingMark


def test_index_redirects_to_current_week(as_lenya):
    r = as_lenya.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/w/2026/37")
    page = as_lenya.get("/w/2026/37")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "7 — 13 сентября" in html and "Лёня" in html and "сегодня" in html


def test_guest_without_header(client):
    assert "Гость" in client.get("/w/2026/37").get_data(as_text=True)


def test_recipe_flow_with_inline_ingredient(as_lenya, app):
    r = as_lenya.post("/recipes", data={"name": "Тест", "portions": "2"})
    assert r.status_code == 302
    edit_url = r.headers["Location"]
    recipe_id = int(edit_url.rstrip("/").split("/")[-2])

    # known ingredient from the seed library
    r = as_lenya.post(f"/recipes/{recipe_id}/ingredients", data={"name": "Рис", "qty": "200", "unit": "g"})
    assert r.status_code == 302
    # unknown → editor reopens with the create form prefilled
    r = as_lenya.post(f"/recipes/{recipe_id}/ingredients", data={"name": "Соус терияки", "qty": "2", "unit": "tbsp"})
    assert "new=" in r.headers["Location"]
    page = as_lenya.get(r.headers["Location"]).get_data(as_text=True)
    assert "Новый ингредиент" in page and 'value="Соус терияки"' in page
    r = as_lenya.post(f"/recipes/{recipe_id}/ingredients/create", data={
        "name": "Соус терияки", "category": "Специи и соусы", "kcal": "", "unit": "tbsp",
        "qty": "2", "line_unit": "tbsp"})
    assert r.status_code == 302

    page = as_lenya.get(f"/recipes/{recipe_id}/edit").get_data(as_text=True)
    assert "≈ нет данных" in page  # the sauce has no macros
    assert "345" in page  # rice kcal per portion: 200 g × 345 / 2 = 345

    with app.app_context():
        recipe = db.session.get(Recipe, recipe_id)
        assert [ri.ingredient.name for ri in recipe.ingredients] == ["Рис", "Соус терияки"]
        assert Ingredient.query.filter_by(name="Соус терияки").one().kcal_100 is None

    r = as_lenya.post(f"/recipes/{recipe_id}", data={"name": "Тест 2", "portions": "3", "steps": "Раз\nДва",
                                                    "action": "view"})
    assert r.headers["Location"].endswith(f"/recipes/{recipe_id}")
    view = as_lenya.get(f"/recipes/{recipe_id}?portions=6").get_data(as_text=True)
    assert "Тест 2" in view and "400 г" in view  # 200 g × 6/3


def test_plan_entry_with_leftover_and_shopping(as_lenya, app, demo):
    with app.app_context():
        borsch = Recipe.query.filter_by(name="Борщ").one()
        users = services.default_eaters()
    r = as_lenya.post("/entries", data={
        "date": "2026-09-09", "slot": "dinner", "recipe_id": str(borsch.id),
        f"portions_{users[0].id}": "1", f"portions_{users[1].id}": "1", "guests": "0",
        "leftover": "on", "leftover_slot": "lunch", "next": "/w/2026/37",
    })
    assert r.status_code == 302 and r.headers["Location"].endswith("/w/2026/37")
    week = as_lenya.get("/w/2026/37").get_data(as_text=True)
    assert week.count("Борщ") >= 2 and "на завтра → обед" in week and "с ср 9" in week

    with app.app_context():
        cook = MealEntry.query.filter_by(leftover_of_id=None).one()
        assert services.total_portions_tenths(cook) == 40

    # shopping for the cook day lists beetroot; the leftover day alone does not
    day = as_lenya.get("/shopping?period=custom&from=2026-09-09&to=2026-09-09").get_data(as_text=True)
    assert "Свёкла" in day
    assert "Свёкла" not in as_lenya.get("/shopping?period=custom&from=2026-09-10&to=2026-09-10").get_data(as_text=True)

    with app.app_context():
        beet = Ingredient.query.filter_by(name="Свёкла").one()
    r = as_lenya.post(f"/shopping/marks/{beet.id}", data={"state": "bought", "valid_until": "2026-09-13",
                                                          "next": "/shopping?period=week"})
    assert r.headers["Location"] == "/shopping?period=week"
    with app.app_context():
        assert db.session.get(ShoppingMark, beet.id).state == "bought"

    r = as_lenya.post("/shopping/extras", data={"name": "Кофе", "qty": "1"})
    assert r.status_code == 302
    page = as_lenya.get("/shopping?period=week").get_data(as_text=True)
    assert "Кофе" in page and "Своё" in page

    # entry dialog and delete
    with app.app_context():
        cook_id = MealEntry.query.filter_by(leftover_of_id=None).one().id
    dlg = as_lenya.get(f"/w/2026/37?dlg=entry&entry={cook_id}").get_data(as_text=True)
    assert 'id="dlg-entry"' in dlg
    as_lenya.post(f"/entries/{cook_id}/delete", data={"next": "/w/2026/37"})
    with app.app_context():
        assert MealEntry.query.count() == 0  # leftover cascades


def test_picker_and_fill_dialogs_render(as_lenya, app, demo):
    pick = as_lenya.get("/w/2026/37?dlg=pick&date=2026-09-08&slot=dinner&q=бор").get_data(as_text=True)
    assert 'id="dlg-pick"' in pick and "Борщ" in pick and "Паста" not in pick
    fill = as_lenya.get("/w/2026/37?dlg=fill&seed=5").get_data(as_text=True)
    assert 'id="dlg-fill"' in fill and 'name="p"' in fill and "Принять" in fill

    # accept whatever was proposed
    import re
    props = re.findall(r'name="p" value="([^"]+)"', fill)
    r = as_lenya.post("/fill/accept", data=MultiDict([("p", p) for p in props] + [("next", "/w/2026/37")]))
    assert r.status_code == 302
    week = as_lenya.get("/w/2026/37").get_data(as_text=True)
    in_week = [p for p in props if p[:10] <= "2026-09-13"]  # a leftover may spill into next week
    assert week.count('class="dish ') == len(in_week)
    with app.app_context():
        assert MealEntry.query.count() == len(props)


def test_day_view_and_recipes_and_ingredients_pages(as_lenya, demo):
    assert "Сегодня" in as_lenya.get("/d/today").get_data(as_text=True)
    assert "Борщ" in as_lenya.get("/recipes").get_data(as_text=True)
    assert "Борщ" in as_lenya.get("/recipes?tag=суп").get_data(as_text=True)
    assert "Паста" not in as_lenya.get("/recipes?tag=суп").get_data(as_text=True)
    ing = as_lenya.get("/ingredients?q=свёк").get_data(as_text=True)
    assert "Свёкла" in ing and "Картофель" not in ing
    assert as_lenya.get("/ingredients?new=1").status_code == 200


def test_settings_and_prefs(as_lenya, app):
    r = as_lenya.post("/settings", data=MultiDict([("repeat_days", "7"), ("default_guests", "1"),
                                          ("shopping_period", "tomorrow"), ("visible_slots", "dinner"),
                                          ("round_grams", "100"), ("weekday_tag", "быстро"),
                                          ("sections", "Овощи\nМясо"), ("default_eaters", "left"),
                                          ("next", "/w/2026/37")]))
    assert r.status_code == 302
    with app.app_context():
        assert services.get_setting("repeat_days") == "7"
        assert services.visible_slots() == ["dinner"]
        assert services.sections() == ["Овощи", "Мясо"]
        assert [u.side for u in services.default_eaters()] == ["left"]
    week = as_lenya.get("/w/2026/37").get_data(as_text=True)
    assert "Завтрак" not in week.split("<dialog")[0]  # grid shows dinner only
    assert as_lenya.get("/shopping").get_data(as_text=True).count('class="on"') >= 1

    assert as_lenya.post("/prefs", data={"theme": "dark", "accent": "rose"}).status_code == 204
    assert 'data-theme="dark"' in as_lenya.get("/w/2026/37").get_data(as_text=True)

    r = as_lenya.post("/tags", data={"name": "детское", "next": "/w/2026/37?dlg=settings"})
    assert r.headers["Location"] == "/w/2026/37?dlg=settings"
    assert "детское" in as_lenya.get("/recipes").get_data(as_text=True)
