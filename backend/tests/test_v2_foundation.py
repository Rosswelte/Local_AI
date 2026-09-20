from pathlib import Path

from app.database.database import open_connection, migrate
from app.services.catalog import upsert_catalog
from app.services.settings import read_settings, write_settings


MIGRATIONS = Path(__file__).parents[1] / "app" / "database" / "migrations"


def test_catalog_does_not_overwrite_local_state(tmp_path):
    con = open_connection(tmp_path / "catalog.db")
    migrate(con, MIGRATIONS)
    con.execute("INSERT INTO models(name, label, provider, installed, enabled, pinned, config, perf) VALUES ('x', 'old', 'ollama', 1, 0, 1, '{\"temperature\":0.2}', '{\"tokens_per_s\":7}')")
    upsert_catalog(con, [{"name": "x", "label": "new", "ram_min_mb": 42, "perf": {"tokens_per_s": 99}, "config": {"temperature": 0.9}}])
    row = con.execute("SELECT label, ram_min_mb, installed, enabled, pinned, config, perf FROM models WHERE name='x'").fetchone()
    assert tuple(row) == ("new", 42, 1, 0, 1, '{"temperature":0.2}', '{"tokens_per_s":7}')


def test_settings_null_means_automatic(tmp_path):
    con = open_connection(tmp_path / "settings.db")
    migrate(con, MIGRATIONS)
    write_settings(con, {"ram_budget_mb": None, "allow_parallel": True})
    values = read_settings(con)
    assert values["ram_budget_mb"] == {"value": None, "category": "limits"}
    assert values["allow_parallel"] == {"value": True, "category": "limits"}
