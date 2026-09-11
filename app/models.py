from datetime import datetime, timezone

from . import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

SLOTS = ["breakfast", "lunch", "dinner", "snack"]
SLOT_LABEL = {"breakfast": "Завтрак", "lunch": "Обед", "dinner": "Ужин", "snack": "Перекус"}
UNITS = ["g", "ml", "pcs", "tbsp", "tsp"]
UNIT_LABEL = {"g": "г", "ml": "мл", "pcs": "шт", "tbsp": "ст. л.", "tsp": "ч. л."}
DEFAULT_SECTIONS = ["Овощи и фрукты", "Мясо и рыба", "Молочное", "Бакалея",
                    "Заморозка", "Специи и соусы", "Прочее"]


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String, unique=True, nullable=False)
    display_name = db.Column(db.String, nullable=False)
    side = db.Column(db.String, unique=True, nullable=False)  # left / right
    theme = db.Column(db.String, nullable=False, default="system")
    accent_color = db.Column(db.String, nullable=False, default="teal")

    @property
    def initial(self):
        return self.display_name[:1].upper()


class Ingredient(db.Model):
    __tablename__ = "ingredients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)
    category = db.Column(db.String, nullable=False, default="Прочее")
    kcal_100 = db.Column(db.Integer)          # per 100 g, None = unknown
    protein_100_dg = db.Column(db.Integer)    # tenths of a gram per 100 g
    fat_100_dg = db.Column(db.Integer)
    carbs_100_dg = db.Column(db.Integer)
    default_unit = db.Column(db.String, nullable=False, default="g")
    piece_grams = db.Column(db.Integer)       # weight of one piece, for «шт»
    density_x100 = db.Column(db.Integer, nullable=False, default=100)  # g per 100 ml
    is_staple = db.Column(db.Boolean, nullable=False, default=False)
    source = db.Column(db.String, nullable=False, default="manual")
    archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    @property
    def has_macros(self):
        return self.kcal_100 is not None


recipe_tags = db.Table(
    "recipe_tags",
    db.Column("recipe_id", db.Integer, db.ForeignKey("recipes.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tags.id"), primary_key=True),
)


class Tag(db.Model):
    __tablename__ = "tags"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)


class Recipe(db.Model):
    __tablename__ = "recipes"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    portions = db.Column(db.Integer, nullable=False, default=4)
    cooked_weight_g = db.Column(db.Integer)
    prep_minutes = db.Column(db.Integer)
    steps = db.Column(db.Text)
    notes = db.Column(db.Text)
    archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    ingredients = db.relationship(
        "RecipeIngredient", back_populates="recipe", cascade="all, delete-orphan",
        order_by="RecipeIngredient.sort_order",
    )
    tags = db.relationship("Tag", secondary=recipe_tags, order_by="Tag.sort_order")

    def has_tag(self, name):
        return any(t.name == name for t in self.tags)


class RecipeIngredient(db.Model):
    __tablename__ = "recipe_ingredients"
    id = db.Column(db.Integer, primary_key=True)
    recipe_id = db.Column(db.Integer, db.ForeignKey("recipes.id"), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredients.id"), nullable=False)
    qty_tenths = db.Column(db.Integer, nullable=False)
    unit = db.Column(db.String, nullable=False, default="g")
    note = db.Column(db.String)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    recipe = db.relationship("Recipe", back_populates="ingredients")
    ingredient = db.relationship("Ingredient")


class MealEntry(db.Model):
    """One dish in one calendar cell. `leftover_of_id` set = eaten from an
    earlier cook entry: counts for the eaters, adds nothing to shopping."""
    __tablename__ = "meal_entries"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    slot = db.Column(db.String, nullable=False)
    recipe_id = db.Column(db.Integer, db.ForeignKey("recipes.id"))
    free_text = db.Column(db.String)
    leftover_of_id = db.Column(db.Integer, db.ForeignKey("meal_entries.id"))
    guest_portions_tenths = db.Column(db.Integer, nullable=False, default=0)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    recipe = db.relationship("Recipe")
    servings = db.relationship("MealServing", back_populates="entry",
                               cascade="all, delete-orphan")
    parent = db.relationship("MealEntry", remote_side=[id], back_populates="children")
    children = db.relationship("MealEntry", back_populates="parent",
                               cascade="all, delete-orphan")

    @property
    def is_leftover(self):
        return self.leftover_of_id is not None

    @property
    def label(self):
        return self.recipe.name if self.recipe else (self.free_text or "")


class MealServing(db.Model):
    __tablename__ = "meal_servings"
    entry_id = db.Column(db.Integer, db.ForeignKey("meal_entries.id"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    portions_tenths = db.Column(db.Integer, nullable=False, default=10)

    entry = db.relationship("MealEntry", back_populates="servings")
    user = db.relationship("User")


class ShoppingMark(db.Model):
    """«куплено» / «есть дома» for an ingredient, valid until the end of the
    period it was marked in — so last week's purchase never hides this week's need."""
    __tablename__ = "shopping_marks"
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredients.id"), primary_key=True)
    state = db.Column(db.String, nullable=False)  # bought / have
    valid_until = db.Column(db.Date, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class ShoppingExtra(db.Model):
    """Manually added line («кофе», «хлеб»), independent of the plan."""
    __tablename__ = "shopping_extras"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    qty_text = db.Column(db.String)
    state = db.Column(db.String, nullable=False, default="todo")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Setting(db.Model):
    __tablename__ = "settings"
    key = db.Column(db.String, primary_key=True)
    value = db.Column(db.String, nullable=False)
