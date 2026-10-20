import csv
from pathlib import Path

from typer.testing import CliRunner

from resolver.cli import app
from resolver.merge import read_audit_log

COLUMNS = [
    "record_id", "source", "first_name", "last_name", "email", "email_verified",
    "phone", "address", "city", "state", "zip", "dob", "updated_at",
]  # fmt: skip


def test_run_writes_outputs(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    rows = [
        ["R1", "crm", "Maria", "Garcia", "maria.g@example.com", "true",
         "310 555 0100", "", "", "", "", "", "2023-01-01"],
        ["R2", "web", "Maria", "Garcia", "maria.g@example.com", "false",
         "213 555 0199", "", "", "", "", "", "2024-01-01"],
        ["R3", "ads", "Maria", "Garcia", "mgarcia@example.org", "false",
         "213-555-0199", "", "", "", "", "", "2025-01-01"],
        ["R4", "ads", "Jane", "Doe", "jd@example.com", "false",
         "", "", "", "", "", "", "2025-01-01"],
    ]  # fmt: skip
    with (sources / "crm.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        writer.writerows(rows)

    out = tmp_path / "out"
    result = CliRunner().invoke(
        app, ["run", "--sources", str(sources), "--out", str(out)]
    )

    assert result.exit_code == 0, result.output
    assert "1 components split by the conflict check" in result.output
    with (out / "clusters.csv").open() as fh:
        clusters = {r["record_id"]: r["cluster_id"] for r in csv.DictReader(fh)}
    assert clusters["R1"] != clusters["R3"]  # drift-chain ends stay apart
    assert len(set(clusters.values())) == 3
    assert len(read_audit_log(out / "audit.jsonl")) == 1
    assert (out / "golden.csv").read_text().count("\n") == 4  # header + 3 clusters
    assert (out / "splits.jsonl").read_text().count("\n") == 1
