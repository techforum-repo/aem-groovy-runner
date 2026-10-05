"""Regression tests for the issues found in the final review."""
from __future__ import annotations

import io
import sqlite3
import zipfile
from pathlib import Path

import openpyxl
import pytest

from groovy_runner import database, runner
from groovy_runner.formatters import asset_reference, generic_excel


def test_zip_keeps_same_named_files_from_different_runs(tmp_path):
    a, b = tmp_path / "20261001-100000" / "acme_products.json", tmp_path / "20261001-110000" / "acme_products.json"
    for path, text in ((a, "first"), (b, "second")):
        path.parent.mkdir()
        path.write_text(text)
    names = zipfile.ZipFile(io.BytesIO(runner.zip_files([str(a), str(b), str(a)]))).namelist()
    assert names == ["acme_products.json", "20261001-110000_acme_products.json"]


@pytest.mark.parametrize("formatter", [asset_reference, generic_excel])
def test_formula_like_values_are_written_as_text(tmp_path, formatter):
    out = tmp_path / "x.xlsx"
    formatter.write([{"assetPath": "/x.pdf", "assetTitle": '=HYPERLINK("http://evil.example","Click")'}], out)
    cells = [c for row in openpyxl.load_workbook(out).active.iter_rows(min_row=2) for c in row]
    assert any(str(c.value).startswith("=HYPERLINK") for c in cells)
    assert all(c.data_type != "f" for c in cells)
    assert b"<f>" not in zipfile.ZipFile(out).read("xl/worksheets/sheet1.xml")


def test_database_connections_are_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "t.db")
    database.initialize()
    with database._connect() as conn:
        conn.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")  # closed, not just committed
    with sqlite3.connect(tmp_path / "t.db") as check:
        assert check.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_streamlit_bound_to_localhost():
    config = (Path(__file__).resolve().parent.parent / ".streamlit" / "config.toml").read_text()
    assert 'address = "localhost"' in config
