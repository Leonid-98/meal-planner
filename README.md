# meal planner

A personal web app for two people: recipes with calories and macros computed
from their ingredients, a week plan that supports batch cooking (cook once,
eat the leftovers tomorrow), and a shopping list for a chosen period. It is
not a calorie tracker: per-portion numbers are a reference for an external
tracking app.

The UI is in Russian on purpose (personal use); code, comments and docs are
in English, and an English UI may follow later.

Stack and deployment mirror `budget-app`: Flask + SQLite + HTMX, an image on
GHCR, Google sign-in via oauth2-proxy in front of Caddy. The app does no
authentication of its own; it trusts the identity header set by the proxy.

## Run locally

With a virtualenv:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
MOCK_AUTH_EMAIL=user1@example.com .venv/bin/flask --app wsgi run --port 8000
```

With Docker (same as budget-app; data lives in `./data-local`):

```sh
docker compose up --build
```

Open <http://127.0.0.1:8000>. The database is created on first start
(`instance/food.db`, or `/data/food.db` in the container) and seeded with a
library of about 70 common ingredients whose per-100 g values are
approximate: check them against the package and correct as you go.

A few sample recipes to play with:

```sh
MOCK_AUTH_EMAIL=user1@example.com .venv/bin/flask --app wsgi seed-demo
```

Tests: `.venv/bin/pytest -q`.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_PATH` | `instance/food.db` | SQLite file |
| `IDENTITY_HEADER` | `X-Forwarded-Email` | header carrying the signed-in email (production: `X-Auth-Request-Email`) |
| `MOCK_AUTH_EMAIL` | empty | stand-in identity when no proxy is present (local development) |
| `FOOD_USER1_EMAIL` / `FOOD_USER1_NAME` | `user1@example.com` / `Лёня` | first user |
| `FOOD_USER2_EMAIL` / `FOOD_USER2_NAME` | `user2@example.com` / `Аня` | second user |

## Layout

- `app/models.py` — tables: users, ingredients, recipes and their lines,
  tags, plan entries (`meal_entries`, leftovers via `leftover_of_id`),
  servings per person, shopping marks, manual shopping lines, settings.
- `app/services.py` — grams and macro math, calendar helpers, shopping list
  aggregation, the "fill the week" randomizer.
- `app/routes.py` — all routes; plain forms, HTMX swaps the page.
- `app/templates/` — one base template plus one page per section.
- `design/` — the design mockup (screen sources and the assembly script).

The schema is created with `db.create_all()`; migrations (Alembic) come
before the first schema change in production, once the data is valuable.

## Deployment

As with budget-app: pushing a tag `vX.Y.Z` makes GitHub Actions build
`ghcr.io/leonid-98/meal-planner:<tag>` and run `deploy-app food <tag>` on
the server over SSH. The server side (Compose service `food`, the
`food.leonid98.eu` site, the `FOOD_TAG=` entry in `.env`, backups) lives in
`homelab-infra`, section "Adding an app". The repository needs the same
Actions secret `DEPLOY_SSH_KEY` and variables `DEPLOY_KNOWN_HOSTS`,
`DEPLOY_USER`, `DEPLOY_HOST` as budget-app. The GHCR package must be public:
the server pulls the image without logging in.
