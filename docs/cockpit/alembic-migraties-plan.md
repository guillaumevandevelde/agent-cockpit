---
title: "Alembic-migraties voor beide stores — implementatieplan (subsysteem 1 van de kernharding)"
type: plan
status: proposed
---

# Alembic-migraties Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Beide SQLite-stores krijgen versiebeheerde schemamigraties, zodat een schemawijziging niet langer betekent dat het bord gewist wordt.

**Architecture:** Twee alembic-omgevingen naast elkaar — één per `DeclarativeBase` (`Base` voor `claude_registry.db`, `KanbanBase` voor `kanban.db`). Bestaande, gevulde databases worden gestempeld in plaats van opnieuw opgebouwd, maar alleen nadat een driftcontrole heeft bevestigd dat hun werkelijke schema overeenkomt met de modellen. De vier handgeschreven migratiefuncties worden één voor één vervangen door revisies en daarna verwijderd.

**Tech Stack:** alembic, SQLAlchemy 2.x (async), aiosqlite, pytest + pytest-asyncio, ruff.

**Spec:** [`kernharding-design.md`](./kernharding-design.md) §1

## Global Constraints

- Python `>=3.11`; ruff `target-version = "py311"`, `line-length = 100`.
- Beide stores zijn SQLite. SQLite kan nauwelijks `ALTER TABLE`: **elke revisie die een kolom wijzigt of verwijdert gebruikt `op.batch_alter_table`**.
- **Roep alembic altijd aan als `sys.executable -m alembic`, nooit als bare `alembic`.** De testrunner start de venv-interpreter zonder `venv/bin` op `PATH`, dus het console-script is daar niet vindbaar — een bare aanroep faalt met `FileNotFoundError: 'alembic'`. Via de interpreter draait het subproces bovendien gegarandeerd in dezelfde omgeving. Dit geldt in taak 2, 3 en 6.
- Twee gescheiden bases: `app.database.Base` (registry) en `app.kanban.db.KanbanBase` (bord). Ze delen géén metadata en krijgen elk een eigen revisiegeschiedenis.
- De live bord-database staat op `~/.claude-registry/kanban.db` en bevat productiegegevens. **Geen enkele taak mag daartegen schrijven.** Alle tests werken op `tmp_path`-bestanden.
- Volledige `pytest` draait in CI, niet lokaal. Losse tests lokaal via `bash scripts/run-single-test.sh tests/test_x.py::test_y`.
- `rm` is geblokkeerd in deze repo; verwijderen gaat via `git rm` of `mv`.

---

## File Structure

| Bestand | Verantwoordelijkheid |
|---|---|
| `backend/app/db_schema_drift.py` | Pure vergelijking: werkelijk DB-schema versus modelmetadata. Geen alembic-afhankelijkheid. |
| `backend/alembic.ini` | Twee genoemde secties: `registry` en `kanban`. |
| `backend/migrations/registry/` | `env.py`, `script.py.mako`, `versions/` voor `Base`. |
| `backend/migrations/kanban/` | `env.py`, `script.py.mako`, `versions/` voor `KanbanBase`. |
| `backend/app/db_bootstrap.py` | `ensure_versioned()`: stempelen of migreren, met driftweigering en voorafgaande momentopname. |
| `backend/app/sqlite_snapshot.py` | Module-level `snapshot_sqlite_db(src, dest)`, geëxtraheerd uit `BackupService._snapshot_kanban_db`. |
| `backend/tests/test_db_schema_drift.py` | Tests voor taak 1. |
| `backend/tests/test_migrations_roundtrip.py` | Tests voor taak 2 en 5. |
| `backend/tests/test_db_bootstrap.py` | Tests voor taak 3. |
| `backend/tests/test_sqlite_snapshot.py` | Tests voor taak 4. |

---

### Task 1: Driftdetectie als pure functie

Dit is het fundament: taak 3 weigert te stempelen op basis van deze functie, en taak 2 en 6 gebruiken hem als assertie.

**Files:**
- Create: `backend/app/db_schema_drift.py`
- Test: `backend/tests/test_db_schema_drift.py`

**Interfaces:**
- Consumes: niets.
- Produces: `schema_differences(sync_connection, metadata: MetaData) -> list[str]` — geeft een lege lijst als het schema van de verbinding exact overeenkomt met `metadata`, anders één leesbare regel per verschil. Regels hebben de vorm `missing-table: <naam>`, `extra-table: <naam>`, `missing-column: <tabel>.<kolom>` en `extra-column: <tabel>.<kolom>`.

- [ ] **Step 1: Write the failing test**

Maak `backend/tests/test_db_schema_drift.py`:

```python
import sqlalchemy as sa

from app.db_schema_drift import schema_differences


def _metadata_with_person() -> sa.MetaData:
    md = sa.MetaData()
    sa.Table(
        "person",
        md,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(50)),
    )
    return md


def test_no_differences_when_schema_matches(tmp_path):
    md = _metadata_with_person()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    md.create_all(engine)
    with engine.connect() as conn:
        assert schema_differences(conn, md) == []


def test_reports_missing_table(tmp_path):
    md = _metadata_with_person()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    with engine.connect() as conn:
        assert schema_differences(conn, md) == ["missing-table: person"]


def test_reports_missing_column(tmp_path):
    md = _metadata_with_person()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE person (id INTEGER PRIMARY KEY)"))
    with engine.connect() as conn:
        assert schema_differences(conn, md) == ["missing-column: person.name"]


def test_reports_extra_column(tmp_path):
    md = _metadata_with_person()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text("CREATE TABLE person (id INTEGER PRIMARY KEY, name VARCHAR(50), extra TEXT)")
        )
    with engine.connect() as conn:
        assert schema_differences(conn, md) == ["extra-column: person.extra"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash scripts/run-single-test.sh tests/test_db_schema_drift.py`
Expected: FAIL met `ModuleNotFoundError: No module named 'app.db_schema_drift'`

- [ ] **Step 3: Write minimal implementation**

Maak `backend/app/db_schema_drift.py`:

```python
"""Compare a live database's schema against SQLAlchemy model metadata.

Deliberately alembic-free: this is the gate that decides whether an existing,
unversioned database may be stamped as up to date (see ``app/db_bootstrap.py``).
Stamping a database whose real shape has drifted from the models would freeze
that drift in place permanently, so the check must not depend on the migration
machinery it guards.

Only tables and column names are compared. Types are intentionally out of
scope: SQLite's type affinity makes a faithful type comparison noisy (VARCHAR
vs TEXT, BOOLEAN vs INTEGER) without catching a real class of production bug.
"""
import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.schema import MetaData


def schema_differences(sync_connection: Connection, metadata: MetaData) -> list[str]:
    inspector = sa.inspect(sync_connection)
    actual_tables = set(inspector.get_table_names())
    expected_tables = set(metadata.tables)

    differences: list[str] = []
    for name in sorted(expected_tables - actual_tables):
        differences.append(f"missing-table: {name}")
    for name in sorted(actual_tables - expected_tables - {"alembic_version"}):
        differences.append(f"extra-table: {name}")

    for name in sorted(expected_tables & actual_tables):
        actual_columns = {col["name"] for col in inspector.get_columns(name)}
        expected_columns = set(metadata.tables[name].columns.keys())
        for column in sorted(expected_columns - actual_columns):
            differences.append(f"missing-column: {name}.{column}")
        for column in sorted(actual_columns - expected_columns):
            differences.append(f"extra-column: {name}.{column}")

    return differences
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/run-single-test.sh tests/test_db_schema_drift.py`
Expected: 4 passed

- [ ] **Step 5: Lint**

Run: `cd backend && source venv/bin/activate && ruff check app/db_schema_drift.py tests/test_db_schema_drift.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add backend/app/db_schema_drift.py backend/tests/test_db_schema_drift.py
git commit -m "feat(db): voeg schema-driftdetectie toe als pure vergelijking

Fundament voor de alembic-invoering: bepaalt of een bestaande, ongeversioneerde
database gestempeld mag worden. Alembic-vrij gehouden, want dit is de poort die
de migratiemachinerie bewaakt. Vergelijkt tabellen en kolomnamen, niet types --
SQLite's type-affinity maakt typevergelijking ruis zonder echte vangst."
```

---

### Task 2: Alembic-scaffolding en basisrevisies voor beide stores

**Files:**
- Modify: `backend/pyproject.toml:7-20` (dependency toevoegen)
- Create: `backend/alembic.ini`
- Create: `backend/migrations/registry/env.py`, `backend/migrations/registry/script.py.mako`, `backend/migrations/registry/versions/.gitkeep`
- Create: `backend/migrations/kanban/env.py`, `backend/migrations/kanban/script.py.mako`, `backend/migrations/kanban/versions/.gitkeep`
- Test: `backend/tests/test_migrations_roundtrip.py`

**Interfaces:**
- Consumes: `schema_differences` uit taak 1.
- Produces: twee alembic-omgevingen, aanroepbaar als `alembic --name registry <cmd>` en `alembic --name kanban <cmd>` vanuit `backend/`. Elke omgeving leest zijn doel-URL uit de omgevingsvariabele `ALEMBIC_DATABASE_URL` als die gezet is, en anders uit `app.config.settings`.

- [ ] **Step 1: Write the failing test**

Maak `backend/tests/test_migrations_roundtrip.py`:

```python
import os
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.db_schema_drift import schema_differences

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic(name: str, db_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{db_path}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "--name", name, *args],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "name, metadata_path",
    [
        ("registry", "app.database:Base"),
        ("kanban", "app.kanban.db:KanbanBase"),
    ],
)
def test_fresh_upgrade_matches_model_metadata(tmp_path, name, metadata_path):
    """A fresh DB taken to head must equal what the models describe."""
    module_path, attr = metadata_path.split(":")
    module = __import__(module_path, fromlist=[attr])
    metadata = getattr(module, attr).metadata

    db_path = tmp_path / f"{name}.db"
    result = _alembic(name, db_path, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        assert schema_differences(conn, metadata) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/run-single-test.sh tests/test_migrations_roundtrip.py`
Expected: FAIL — `alembic` is nog geen commando, of `returncode != 0` met "No config file 'alembic.ini' found".

- [ ] **Step 3: Voeg de dependency toe — op twee plekken**

`backend/pyproject.toml` is niet de bron waaruit CI installeert. Alle drie de jobs in `quality.yml` draaien `pip install -r requirements-dev.txt`, en dat bestand trekt `requirements.txt` binnen. Zet je alembic alleen in `pyproject.toml`, dan werkt alles lokaal en faalt CI met `ModuleNotFoundError: No module named 'alembic'` bij het verzamelen van de tests.

In `backend/requirements.txt`, onder `sqlalchemy[asyncio]>=2.0.25`:

```
alembic>=1.13.0
```

En in `backend/pyproject.toml`, binnen de `dependencies`-lijst op dezelfde plek:

```toml
    "alembic>=1.13.0",
```

Installeer: `cd backend && source venv/bin/activate && pip install -r requirements-dev.txt`

- [ ] **Step 4: Maak `backend/alembic.ini`**

```ini
# Two independent migration histories, one per DeclarativeBase. They are kept
# apart on purpose: app/database.py holds device-local registry state and
# app/kanban/db.py holds the portable board, and the two stores are moved,
# backed up and reset independently (see app/config.py).
#
# Usage:  alembic --name registry upgrade head
#         alembic --name kanban   upgrade head

[registry]
script_location = migrations/registry
prepend_sys_path = .

[kanban]
script_location = migrations/kanban
prepend_sys_path = .

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 5: Maak `backend/migrations/registry/env.py`**

```python
"""Alembic environment for the registry store (app.database.Base).

The URL comes from ALEMBIC_DATABASE_URL when set, so tests can point a run at
a tmp_path file without touching the developer's real claude_registry.db.
Falls back to the app's configured URL, with the async driver stripped:
alembic runs synchronously, and the aiosqlite driver would fail to connect.
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  (register every table on Base)
import app.models.database  # noqa: F401  (core tables predate the eager-import convention)
from app.config import settings
from app.database import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("ALEMBIC_DATABASE_URL") or settings.database_url
    return url.replace("+aiosqlite", "")


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        # render_as_batch is mandatory: SQLite cannot ALTER a column, so alembic
        # has to rebuild the table. Without this, every column change fails.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 6: Maak `backend/migrations/kanban/env.py`**

Identiek aan stap 5, met vier verschillen — schrijf het bestand voluit:

```python
"""Alembic environment for the board store (app.kanban.db.KanbanBase).

See migrations/registry/env.py for why ALEMBIC_DATABASE_URL exists and why
render_as_batch is mandatory. This environment targets the portable board DB
(~/.claude-registry/kanban.db), which holds production data — never point it
at that file from a test.
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.kanban.models  # noqa: F401  (register every table on KanbanBase)
from app.config import settings
from app.kanban.db import KanbanBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = KanbanBase.metadata


def _database_url() -> str:
    url = os.environ.get("ALEMBIC_DATABASE_URL") or settings.kanban_database_url
    return url.replace("+aiosqlite", "")


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 7: Maak beide `script.py.mako`-sjablonen**

Schrijf hetzelfde bestand naar `backend/migrations/registry/script.py.mako` én `backend/migrations/kanban/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

SQLite cannot ALTER a column. Any change to an existing column MUST go through
`with op.batch_alter_table("<table>") as batch_op:` — a plain op.alter_column
will fail at runtime on both of this project's stores.
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Maak beide `versions/`-mappen aan met een `.gitkeep`-bestand, zodat git ze meeneemt.

- [ ] **Step 8: Genereer de twee basisrevisies tegen een verse database**

Belangrijk: genereer tegen een leeg tmp-bestand, niet tegen je echte databases. Anders neemt autogenerate bestaande drift over.

```bash
cd backend && source venv/bin/activate
TMP=$(mktemp -d)
ALEMBIC_DATABASE_URL="sqlite:///$TMP/registry.db" alembic --name registry revision --autogenerate -m "baseline registry schema"
ALEMBIC_DATABASE_URL="sqlite:///$TMP/kanban.db" alembic --name kanban revision --autogenerate -m "baseline board schema"
```

Controleer beide gegenereerde bestanden op twee dingen: `down_revision = None`, en `upgrade()` bevat uitsluitend opbouwende operaties.

Scope die tweede controle expliciet op `upgrade()` — een `downgrade()` hoort vol `drop_table` en `drop_index` te staan, en een grep over het hele bestand leest dat ten onrechte als drift:

```bash
for f in migrations/registry/versions/*.py migrations/kanban/versions/*.py; do
  echo "=== $(basename $f) ==="
  grep -E '^down_revision' "$f"
  awk '/^def upgrade/,/^def downgrade/' "$f" | grep -oE 'op\.[a-z_]+' | sort | uniq -c
done
```

Verwacht in `upgrade()`: alleen `op.create_table`, `op.create_index`, `op.f` en `op.batch_alter_table`. Die laatste is geen wijziging maar de context waarin alembic op SQLite een index aanmaakt — die hoort er dus te staan. Zie je `op.drop_*` of `op.alter_column` in `upgrade()`, dán is er tegen een gevulde database gegenereerd: gooi het bestand weg en herhaal met een verse `$TMP`.

- [ ] **Step 9: Run the test to verify it passes**

Run: `bash scripts/run-single-test.sh tests/test_migrations_roundtrip.py`
Expected: 2 passed

- [ ] **Step 10: Commit**

```bash
git add backend/pyproject.toml backend/alembic.ini backend/migrations backend/tests/test_migrations_roundtrip.py
git commit -m "feat(db): voeg alembic toe met twee gescheiden migratiegeschiedenissen

Eén omgeving per DeclarativeBase: registry (claude_registry.db) en kanban
(kanban.db). Ze blijven gescheiden omdat de twee stores onafhankelijk worden
verplaatst, geback-upt en gereset.

render_as_batch staat in beide env.py's aan: SQLite kan geen ALTER COLUMN, dus
zonder batch-modus faalt elke kolomwijziging. Het revisiesjabloon herhaalt die
regel, zodat een revisie-auteur er niet omheen kan lezen.

ALEMBIC_DATABASE_URL laat tests op een tmp_path-bestand draaien in plaats van
op de echte databases. De roundtrip-test bewijst dat een verse database naar
head hetzelfde schema oplevert als de modellen beschrijven."
```

---

### Task 3: Bestaande databases stempelen, met driftweigering

**Files:**
- Create: `backend/app/db_bootstrap.py`
- Test: `backend/tests/test_db_bootstrap.py`

**Interfaces:**
- Consumes: `schema_differences` uit taak 1; de alembic-omgevingen uit taak 2.
- Produces: `ensure_versioned(name: str, db_path: Path, metadata: MetaData) -> str` — geeft `"stamped"`, `"upgraded"` of `"created"` terug, en gooit `SchemaDriftError` als een ongeversioneerde database afwijkt van de modellen.

- [ ] **Step 1: Write the failing test**

Maak `backend/tests/test_db_bootstrap.py`:

```python
import pytest
import sqlalchemy as sa

from app.db_bootstrap import SchemaDriftError, ensure_versioned


def _metadata() -> sa.MetaData:
    md = sa.MetaData()
    sa.Table(
        "person",
        md,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(50)),
    )
    return md


def test_empty_database_is_created(tmp_path):
    db = tmp_path / "empty.db"
    assert ensure_versioned("registry", db, _metadata()) == "created"


def test_matching_unversioned_database_is_stamped(tmp_path):
    db = tmp_path / "existing.db"
    md = _metadata()
    engine = sa.create_engine(f"sqlite:///{db}")
    md.create_all(engine)

    assert ensure_versioned("registry", db, md) == "stamped"

    with engine.connect() as conn:
        version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    assert version is not None


def test_stamping_preserves_existing_rows(tmp_path):
    db = tmp_path / "filled.db"
    md = _metadata()
    engine = sa.create_engine(f"sqlite:///{db}")
    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO person (id, name) VALUES (1, 'kept')"))

    ensure_versioned("registry", db, md)

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT name FROM person")).scalar() == "kept"


def test_drifted_unversioned_database_is_refused(tmp_path):
    db = tmp_path / "drifted.db"
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE person (id INTEGER PRIMARY KEY)"))

    with pytest.raises(SchemaDriftError) as excinfo:
        ensure_versioned("registry", db, _metadata())

    assert "missing-column: person.name" in str(excinfo.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash scripts/run-single-test.sh tests/test_db_bootstrap.py`
Expected: FAIL met `ModuleNotFoundError: No module named 'app.db_bootstrap'`

- [ ] **Step 3: Write the implementation**

Maak `backend/app/db_bootstrap.py`:

```python
"""Bring an existing database under alembic control without rebuilding it.

Both stores predate alembic: their schema was produced by ``create_all`` plus a
set of hand-written ``_ensure_*`` / ``_migrate_*`` functions. They hold live
data (855 cards and 18.834 ops on the board at the time of writing), so the
first alembic run must stamp them as current rather than create anything.

Stamping is only safe when the real schema already matches the models. A
database whose shape drifted -- which is a live possibility given four
hand-written migration functions -- would have that drift frozen in place
permanently by a stamp, and no later revision would ever correct it. So drift
is a hard refusal, not a warning.
"""
import os
import subprocess
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.schema import MetaData

from app.db_schema_drift import schema_differences

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class SchemaDriftError(RuntimeError):
    """An unversioned database's schema does not match the models."""


def _run_alembic(name: str, db_path: Path, *args: str) -> None:
    env = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{db_path}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "--name", name, *args],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic --name {name} {' '.join(args)} failed:\n{result.stderr}")


def ensure_versioned(name: str, db_path: Path, metadata: MetaData) -> str:
    """Return "created", "stamped" or "upgraded" for the database at db_path."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(f"sqlite:///{db_path}")

    with engine.connect() as conn:
        inspector = sa.inspect(conn)
        tables = set(inspector.get_table_names())

    if not tables:
        _run_alembic(name, db_path, "upgrade", "head")
        return "created"

    if "alembic_version" in tables:
        _run_alembic(name, db_path, "upgrade", "head")
        return "upgraded"

    with engine.connect() as conn:
        differences = schema_differences(conn, metadata)
    if differences:
        raise SchemaDriftError(
            f"{db_path} predates alembic and its schema does not match the models, "
            "so it cannot be stamped as up to date. Differences:\n  "
            + "\n  ".join(differences)
        )

    _run_alembic(name, db_path, "stamp", "head")
    return "stamped"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/run-single-test.sh tests/test_db_bootstrap.py`
Expected: 4 passed

- [ ] **Step 5: Lint and commit**

```bash
cd backend && source venv/bin/activate && ruff check app/db_bootstrap.py tests/test_db_bootstrap.py
cd /home/vdvgu/claude-cockpit
git add backend/app/db_bootstrap.py backend/tests/test_db_bootstrap.py
git commit -m "feat(db): stempel bestaande databases in plaats van ze opnieuw te bouwen

Beide stores bestaan al en zijn gevuld, dus de eerste alembic-run mag niets
aanmaken. ensure_versioned stempelt ze als actueel -- maar alleen nadat de
driftcontrole uit taak 1 heeft bevestigd dat het werkelijke schema met de
modellen overeenkomt.

Drift is een harde weigering, geen waarschuwing: een stempel op een afwijkende
database bevriest die afwijking permanent, en geen enkele latere revisie zou
hem nog corrigeren."
```

---

### Task 4: Momentopname vóór migratie, en aansluiting op de opstart

**Files:**
- Create: `backend/app/sqlite_snapshot.py`
- Modify: `backend/app/services/backup_service.py:235-274` (methode delegeert naar de nieuwe helper)
- Modify: `scripts/cockpit.sh:517` (in `cmd_start`, vóór de backend start)
- Test: `backend/tests/test_sqlite_snapshot.py`

**Interfaces:**
- Consumes: `ensure_versioned` uit taak 3.
- Produces: `snapshot_sqlite_db(src: Path, dest: Path) -> Path` — WAL-veilige kopie, geeft `dest` terug.

- [ ] **Step 1: Write the failing test**

Maak `backend/tests/test_sqlite_snapshot.py`:

```python
import sqlalchemy as sa

from app.sqlite_snapshot import snapshot_sqlite_db


def test_snapshot_copies_committed_rows(tmp_path):
    src = tmp_path / "live.db"
    engine = sa.create_engine(f"sqlite:///{src}")
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA journal_mode=WAL"))
        conn.execute(sa.text("CREATE TABLE t (v TEXT)"))
        conn.execute(sa.text("INSERT INTO t (v) VALUES ('kept')"))

    dest = snapshot_sqlite_db(src, tmp_path / "snap.db")

    snap_engine = sa.create_engine(f"sqlite:///{dest}")
    with snap_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT v FROM t")).scalar() == "kept"


def test_snapshot_is_a_separate_file(tmp_path):
    src = tmp_path / "live.db"
    engine = sa.create_engine(f"sqlite:///{src}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE t (v TEXT)"))

    dest = snapshot_sqlite_db(src, tmp_path / "snap.db")
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO t (v) VALUES ('after')"))

    snap_engine = sa.create_engine(f"sqlite:///{dest}")
    with snap_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM t")).scalar() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash scripts/run-single-test.sh tests/test_sqlite_snapshot.py`
Expected: FAIL met `ModuleNotFoundError: No module named 'app.sqlite_snapshot'`

- [ ] **Step 3: Write the implementation**

Maak `backend/app/sqlite_snapshot.py`:

```python
"""WAL-safe snapshot of a SQLite database file.

Copying the file directly is wrong for a WAL-mode database: the committed
frame may live in the -wal sidecar and a plain copy misses it. sqlite3's own
backup API walks the connection instead, which sees the committed state.

Extracted from BackupService._snapshot_kanban_db so the migration runner can
take a pre-upgrade snapshot without constructing a BackupService (which needs
an AsyncSession). That method now delegates here.
"""
import sqlite3
from pathlib import Path


def snapshot_sqlite_db(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return dest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/run-single-test.sh tests/test_sqlite_snapshot.py`
Expected: 2 passed

- [ ] **Step 5: Laat de bestaande methode delegeren**

Lees `backend/app/services/backup_service.py:235-274`, vervang de body van `_snapshot_kanban_db` door een aanroep van `snapshot_sqlite_db(src, <bestaande bestemming>)`, en behoud de docstring plus de bestaande bestemmingsberekening. Laat de handtekening ongemoeid, zodat de aanroepers niet wijzigen.

Run daarna de bestaande backup-tests: `bash scripts/run-single-test.sh tests/test_backup_service.py`
Expected: onveranderd groen. Bestaat dat bestand niet onder die naam, zoek het met `ls backend/tests | grep -i backup` en draai wat er is.

- [ ] **Step 6: Draai de migratie bij het opstarten**

In `scripts/cockpit.sh`, binnen `cmd_start` (regel 517) en vóór de regel die de backend opstart, voeg toe:

```bash
    # Schema first: the backend refuses to serve on a version mismatch, so a
    # failed migration must surface here rather than as a crash loop later.
    echo "==> migrating databases"
    ( cd "$PROJECT_ROOT/backend" && source venv/bin/activate \
        && python -m app.migrate_cli ) || {
        echo "migration failed -- not starting the backend" >&2
        exit 1
    }
```

Maak `backend/app/migrate_cli.py`:

```python
"""Bring both stores to head, taking a snapshot of the board first.

Run by scripts/cockpit.sh before the backend starts. Deliberately loud: a
failed migration stops the start rather than letting the backend come up on a
schema it does not understand.
"""
import sys
from datetime import datetime
from pathlib import Path

from app.database import Base
from app.db_bootstrap import SchemaDriftError, ensure_versioned
from app.kanban.db import KanbanBase
from app.services.backup_service import kanban_db_path
from app.sqlite_snapshot import snapshot_sqlite_db


def main() -> int:
    board = kanban_db_path()
    if board is not None and board.exists():
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        destination = Path.home() / ".claude-registry" / "backups" / f"pre-migrate-{stamp}.db"
        snapshot_sqlite_db(board, destination)
        print(f"snapshot: {destination}")

    from app.config import settings

    registry_path = Path(settings.database_url.replace("sqlite+aiosqlite:///", ""))
    try:
        print("registry:", ensure_versioned("registry", registry_path, Base.metadata))
        if board is not None:
            print("kanban:", ensure_versioned("kanban", board, KanbanBase.metadata))
    except SchemaDriftError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Laat de applicatie weigeren bij een versieverschil**

De spec eist dit expliciet (§1.4): de backend mag niet stil doordraaien op een schema dat hij niet kent. `cockpit.sh` dekt alleen de gewone startroute — een handmatige `uvicorn` of een `docker compose up` omzeilt hem.

Voeg toe aan `backend/app/db_bootstrap.py`:

```python
def assert_at_head(name: str, db_path: Path) -> None:
    """Raise when the database is behind the migrations.

    Called from the app's lifespan. cockpit.sh already migrates before start,
    but a bare `uvicorn app.main:app` or a container start bypasses that, and
    serving on an unknown schema corrupts rather than errors.
    """
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        if "alembic_version" not in set(sa.inspect(conn).get_table_names()):
            raise RuntimeError(
                f"{db_path} is not under alembic control. "
                "Run: cd backend && python -m app.migrate_cli"
            )
        current = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "--name", name, "heads", "--verbose"],
        cwd=BACKEND_ROOT, capture_output=True, text=True,
        env={**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{db_path}"},
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not read alembic heads for {name}:\n{result.stderr}")
    if current not in result.stdout:
        raise RuntimeError(
            f"{db_path} is at revision {current}, which is not head. "
            "Run: cd backend && python -m app.migrate_cli"
        )
```

Roep hem aan in `backend/app/main.py`, in `lifespan` (regel 159), vóór de bestaande `init_db()`-aanroep. Voeg een test toe aan `backend/tests/test_db_bootstrap.py`:

```python
def test_assert_at_head_refuses_unversioned_database(tmp_path):
    from app.db_bootstrap import assert_at_head

    db = tmp_path / "bare.db"
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE t (v TEXT)"))

    with pytest.raises(RuntimeError, match="not under alembic control"):
        assert_at_head("registry", db)
```

Run: `bash scripts/run-single-test.sh tests/test_db_bootstrap.py`
Expected: 5 passed

- [ ] **Step 8: Verify the start path by hand**

Run: `./scripts/cockpit.sh start`, dan `./scripts/cockpit.sh status`.
Expected: de uitvoer toont een `snapshot:`-regel, `registry: stamped` en `kanban: stamped`, en backend plus frontend draaien. Controleer daarna dat het bord nog gevuld is via de UI op `:5173`.

Ruim de momentopname op als je wilt: `mv ~/.claude-registry/backups/pre-migrate-*.db /tmp/`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/sqlite_snapshot.py backend/app/migrate_cli.py backend/tests/test_sqlite_snapshot.py backend/app/services/backup_service.py scripts/cockpit.sh
git commit -m "feat(db): migreer beide stores bij het opstarten, met momentopname vooraf

snapshot_sqlite_db is losgetrokken uit BackupService._snapshot_kanban_db zodat
de migratieloop hem kan gebruiken zonder een AsyncSession te bouwen; de
methode delegeert er nu naartoe. Een directe bestandskopie is fout voor een
WAL-database -- de commit kan in de -wal sidecar zitten.

cockpit.sh draait de migratie vóór de backend start en stopt bij een fout.
Luid falen is hier veiliger dan behulpzaam zijn: dit bord is de enige kopie."
```

---

### Task 5: Vervang de handgeschreven migraties door revisies

Zeven functies verdwijnen, verspreid over drie bestanden. Doe ze één voor één, met een commit per functie, zodat een fout terug te draaien is tot één migratie.

**Files:**
- Modify: `backend/app/database.py:59-219` (verwijder `_migrate_terminology_columns`, `_migrate_project_columns`, `_migrate_subscription_prefs_shape` en hun aanroepen in `init_db`)
- Modify: `backend/app/kanban/db.py:88-355` (verwijder `_ensure_card_columns`, `_ensure_column_table`, `_ensure_work_type_mapping_table` en hun aanroepen in `init_kanban_db`)
- Delete: `backend/app/services/scheduling/schema_guard.py`
- Create: één revisiebestand per verwijderde functie in de bijbehorende `migrations/*/versions/`
- Test: `backend/tests/test_migrations_roundtrip.py` (bestaand, moet groen blijven)

**Interfaces:**
- Consumes: de scaffolding uit taak 2.
- Produces: geen nieuwe publieke functies. `init_db` en `init_kanban_db` behouden hun handtekening maar doen alleen nog `create_all` voor de testsuite.

- [ ] **Step 1: Bepaal per functie wat hij feitelijk doet**

De DDL staat niet in dit plan omdat hij uit zeven bestaande functies gelezen moet worden. Hieronder de exacte leesopdrachten, zodat dat mechanisch is:

```bash
sed -n '70,128p'  backend/app/database.py    # _migrate_subscription_prefs_shape
sed -n '129,151p' backend/app/database.py    # _migrate_project_columns
sed -n '152,219p' backend/app/database.py    # _migrate_terminology_columns
sed -n '107,206p' backend/app/kanban/db.py   # _ensure_card_columns
sed -n '207,272p' backend/app/kanban/db.py   # _ensure_column_table
sed -n '273,355p' backend/app/kanban/db.py   # _ensure_work_type_mapping_table
                                             #   + _has_unique_on_project_work_type (helper, gaat mee)
```

**Laat `_migrate_legacy_sqlite` (`backend/app/kanban/db.py:53-79`) staan.** Ondanks de naam is dat geen schemamigratie maar een bestandsverplaatsing van een oude bord-locatie naar de huidige. Alembic vervangt hem niet.

Noteer per functie de kolommen en tabellen die hij aanmaakt, met hun types.

- [ ] **Step 2: Schrijf de revisie voor de eerste functie**

```bash
cd backend && source venv/bin/activate
ALEMBIC_DATABASE_URL="sqlite:///$(mktemp -d)/x.db" alembic --name registry revision -m "terminology columns"
```

Vul `upgrade()` met de DDL uit stap 1, in batch-vorm:

```python
def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("<kolomnaam>", sa.String(), nullable=True))
```

`downgrade()` doet het omgekeerde met `batch_op.drop_column("<kolomnaam>")`.

- [ ] **Step 3: Verwijder de functie en haar aanroep**

Haal de functiedefinitie weg én de regel in `init_db` die hem aanroept.

- [ ] **Step 4: Verify**

Run: `bash scripts/run-single-test.sh tests/test_migrations_roundtrip.py`
Expected: 2 passed — de verse database bereikt nog steeds hetzelfde schema als de modellen.

Run daarnaast de testbestanden die de geraakte tabellen gebruiken. Zoek ze met `grep -rln "projects" backend/tests | head`.

- [ ] **Step 5: Commit, en herhaal stap 2 tot en met 5 voor elke resterende functie**

```bash
git add backend/app/database.py backend/migrations/registry/versions
git commit -m "refactor(db): vervang _migrate_terminology_columns door een revisie"
```

Volgorde: `_migrate_terminology_columns`, `_migrate_project_columns`, `_migrate_subscription_prefs_shape`, `_ensure_card_columns`, `_ensure_column_table`, `_ensure_work_type_mapping_table`.

- [ ] **Step 6: Verwijder `schema_guard.py` als laatste**

Deze mag pas weg als alle zes de voorgaande functies zijn vervangen — hij is het vangnet eronder.

```bash
grep -rn "schema_guard" backend/app backend/tests
```

Verwijder elke aanroepplek, daarna het bestand:

```bash
git rm backend/app/services/scheduling/schema_guard.py
git commit -m "refactor(db): verwijder schema_guard

Zijn bestaansreden was het opvangen van een ontbrekende kolom tijdens bedrijf,
en die vervalt zodra het schema versiebegrip heeft."
```

---

### Task 6: CI-poort tegen schemadrift

**Files:**
- Modify: `.github/workflows/quality.yml` (nieuwe stap in de `backend-lint`-job)
- Create: `backend/scripts/check_migrations_current.py`

**Interfaces:**
- Consumes: `schema_differences` uit taak 1, de alembic-omgevingen uit taak 2.
- Produces: een script dat exit 1 geeft als `head` niet gelijk is aan de modelmetadata.

- [ ] **Step 1: Write the script**

Maak `backend/scripts/check_migrations_current.py`:

```python
"""Fail when the migrations no longer describe the models.

The test suite builds its schema with drop_all/create_all (37 test files
depend on that, and migrating per test would only make the suite slower), so
nothing else notices when a model changes without a matching revision. This
gate closes that hole: it takes a fresh database to head and compares.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import sqlalchemy as sa

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.db_schema_drift import schema_differences  # noqa: E402

TARGETS = [
    ("registry", "app.database", "Base"),
    ("kanban", "app.kanban.db", "KanbanBase"),
]


def main() -> int:
    failures = 0
    for name, module_path, attr in TARGETS:
        module = __import__(module_path, fromlist=[attr])
        metadata = getattr(module, attr).metadata

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / f"{name}.db"
            env = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{db_path}"}
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "--name", name, "upgrade", "head"],
                cwd=BACKEND_ROOT, env=env, capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"{name}: alembic upgrade head failed\n{result.stderr}", file=sys.stderr)
                failures += 1
                continue

            engine = sa.create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                differences = schema_differences(conn, metadata)
            if differences:
                print(f"{name}: models and migrations disagree:", file=sys.stderr)
                for line in differences:
                    print(f"  {line}", file=sys.stderr)
                print(
                    f"\nGenerate the missing revision:\n"
                    f"  cd backend && alembic --name {name} revision --autogenerate -m '<what changed>'",
                    file=sys.stderr,
                )
                failures += 1

    if failures:
        return 1
    print("OK: migrations match the models for both stores.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify it passes now**

Run: `cd backend && source venv/bin/activate && python scripts/check_migrations_current.py`
Expected: `OK: migrations match the models for both stores.`

- [ ] **Step 3: Verify it actually catches drift**

Voeg tijdelijk een kolom toe aan een model — bijvoorbeeld in `backend/app/kanban/models.py`, aan `KanbanCard`: `drift_probe = Column(String, nullable=True)`. Draai het script opnieuw.
Expected: exit 1 met `kanban: models and migrations disagree:` en `missing-column: kanban_cards.drift_probe`.

Haal de kolom daarna weg en bevestig dat het script weer `OK` geeft. Deze stap is niet optioneel: een poort die je nooit hebt zien falen, bewaakt niets — precies de val uit kaart `e5136a3f`.

- [ ] **Step 4: Voeg de stap toe aan de CI**

In `.github/workflows/quality.yml`, in de `backend-lint`-job, direct na de bestaande `check_openapi_snapshot.py`-stap (regel 77-78):

```yaml
      - run: python scripts/check_migrations_current.py
        if: ${{ !cancelled() }}
        working-directory: backend
```

De `if: ${{ !cancelled() }}`-vorm is verplicht in deze job, zodat één run álle falende poorten toont in plaats van alleen de eerste.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/check_migrations_current.py .github/workflows/quality.yml
git commit -m "feat(ci): poort die migraties en modellen uit elkaar laat lopen betrapt

De testsuite bouwt haar schema met drop_all/create_all, dus niets merkt het
wanneer een model verandert zonder bijbehorende revisie. Deze poort neemt een
verse database naar head en vergelijkt.

Handmatig geverifieerd dat de poort faalt op een toegevoegde modelkolom, niet
alleen dat hij slaagt op de huidige toestand."
```

---

## Volgende plannen

Dit plan dekt subsysteem 1. De andere twee uit `kernharding-design.md` krijgen elk hun eigen plan, en beide bouwen op de migraties uit dit plan:

- **Duurzame toestand** (§2) — de reconciler, het omgekeerde schrijfpad, en de poort op `_sched.add_job`. Heeft alembic nodig voor de kolommen die nog ontbreken.
- **Architectuurgrens** (§3) — import-linter met drie contracten en de omvangsratel op bestanden boven 800 regels.
