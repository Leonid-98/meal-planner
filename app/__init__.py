import csv
import os

from flask import Flask, g, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, func

db = SQLAlchemy()

APP_VERSION = "0.1.0"


def users_from_env():
    return [
        {
            "email": os.environ.get("FOOD_USER1_EMAIL", "user1@example.com").lower(),
            "name": os.environ.get("FOOD_USER1_NAME", "Лёня"),
            "side": "left",
        },
        {
            "email": os.environ.get("FOOD_USER2_EMAIL", "user2@example.com").lower(),
            "name": os.environ.get("FOOD_USER2_NAME", "Аня"),
            "side": "right",
        },
    ]


def create_app(config=None):
    app = Flask(__name__)
    os.makedirs(app.instance_path, exist_ok=True)
    db_path = os.environ.get("DATABASE_PATH", os.path.join(app.instance_path, "food.db"))
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + db_path
    app.config["IDENTITY_HEADER"] = os.environ.get("IDENTITY_HEADER", "X-Forwarded-Email")
    app.config["MOCK_AUTH_EMAIL"] = os.environ.get("MOCK_AUTH_EMAIL", "")
    app.config["USERS"] = users_from_env()
    app.config["SEED_INGREDIENTS"] = True
    if config:
        app.config.update(config)

    db.init_app(app)

    from . import models  # noqa: F401  (register models before create_all)
    from . import services
    from .routes import bp

    with app.app_context():
        # SQLite's own lower() is ASCII-only; register a Unicode one for
        # case-insensitive matching of Cyrillic names.
        @event.listens_for(db.engine, "connect")
        def _register_functions(dbapi_conn, _record):
            dbapi_conn.create_function("ulower", 1, lambda s: s.lower() if isinstance(s, str) else s)

        db.create_all()
        _migrate()
        _seed(app)

    app.register_blueprint(bp)

    app.jinja_env.filters["qty"] = services.fmt_qty
    app.jinja_env.filters["tenths"] = services.fmt_tenths
    app.jinja_env.filters["g1"] = services.fmt_g
    app.jinja_env.filters["num"] = services.fmt_int
    app.jinja_env.filters["dlong"] = services.fmt_long
    app.jinja_env.filters["dshort"] = services.fmt_short
    app.jinja_env.filters["since"] = services.fmt_since
    app.jinja_env.globals.update(
        APP_VERSION=APP_VERSION,
        SLOTS=models.SLOTS, SLOT_LABEL=models.SLOT_LABEL,
        UNITS=models.UNITS, UNIT_LABEL=models.UNIT_LABEL,
        DAY_SHORT=services.DAY_SHORT, PERIODS=services.PERIODS,
        setting=services.get_setting,
        totals_of=services.recipe_totals, kcal_of=services.entry_kcal,
    )

    @app.context_processor
    def inject_common():
        from .models import Tag, User
        return {
            "all_users": User.query.order_by(User.side).all(),
            "all_tags": Tag.query.order_by(Tag.sort_order, Tag.id).all(),
        }

    @app.before_request
    def identify():
        from .models import User

        header = app.config["IDENTITY_HEADER"]
        email = (request.headers.get(header) or app.config["MOCK_AUTH_EMAIL"] or "").strip().lower()
        g.user = None
        if email:
            g.user = User.query.filter(func.lower(User.email) == email).first()
        g.today = services.today()

    @app.cli.command("seed-demo")
    def seed_demo():
        """Create a few sample recipes so the planner is not empty on first run."""
        n = services.seed_demo_recipes()
        print(f"recipes added: {n}")

    return app


def _migrate():
    """create_all() never alters existing tables — evolve databases created
    by earlier releases here (idempotent, keyed on PRAGMA table_info)."""
    db.session.commit()


def _seed(app):
    from . import services
    from .models import Ingredient, User

    for cfg in app.config["USERS"]:
        user = User.query.filter_by(side=cfg["side"]).first()
        if user is None:
            db.session.add(User(email=cfg["email"], display_name=cfg["name"], side=cfg["side"]))
        else:
            user.email = cfg["email"]
            user.display_name = cfg["name"]

    services.ensure_tags()

    if app.config["SEED_INGREDIENTS"] and Ingredient.query.count() == 0:
        path = os.path.join(os.path.dirname(__file__), "seed_ingredients.csv")
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                db.session.add(Ingredient(
                    name=row["name"].strip(),
                    category=row["category"].strip(),
                    kcal_100=int(row["kcal"]) if row["kcal"] else None,
                    protein_100_dg=services.to_dg(row["protein"]),
                    fat_100_dg=services.to_dg(row["fat"]),
                    carbs_100_dg=services.to_dg(row["carbs"]),
                    default_unit=row["unit"] or "g",
                    piece_grams=int(row["piece_grams"]) if row["piece_grams"] else None,
                    density_x100=int(row["density"]) if row["density"] else 100,
                    is_staple=row["staple"] == "1",
                    source="seed",
                ))

    db.session.commit()
