import hashlib
import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from incident_investigator.cli import app
from incident_investigator.demo.generate import generate


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_same_seed_gives_identical_database(tmp_path: Path) -> None:
    first = generate(tmp_path / "a.db", seed=7)
    second = generate(tmp_path / "b.db", seed=7)
    assert _digest(first) == _digest(second)
    assert _digest(generate(tmp_path / "c.db", seed=8)) != _digest(first)


def test_planted_incidents_and_ground_truth(demo_db: Path) -> None:
    con = sqlite3.connect(demo_db)
    truth = {row[0]: json.loads(row[1]) for row in con.execute("SELECT * FROM ground_truth")}
    assert set(truth) == {"payments-pool-exhaustion", "inventory-memory-leak"}

    pool_logs = con.execute(
        "SELECT message FROM logs WHERE service = 'payments' AND message LIKE '%max_connections%'"
        " ORDER BY ts"
    ).fetchall()
    assert [m for (m,) in pool_logs][-1] == "db pool initialised max_connections=5"
    assert any("max_connections=20" in m for (m,) in pool_logs)

    timeouts = con.execute(
        "SELECT COUNT(*) FROM logs WHERE message LIKE '%acquiring db connection timed out%'"
    ).fetchone()[0]
    ooms = con.execute(
        "SELECT COUNT(*) FROM logs WHERE message LIKE '%JavaScript heap out of memory%'"
    ).fetchone()[0]
    assert timeouts > 100
    assert ooms >= 2


def test_cli_generate_data(tmp_path: Path) -> None:
    out = tmp_path / "demo.db"
    result = CliRunner().invoke(app, ["generate-data", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
