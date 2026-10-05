from __future__ import annotations

"""Any JSON result -> one Excel sheet.

- list of objects: one row each; nested objects flattened to dotted columns
  (page.title), lists joined with "; "
- object: one row per key (Key / Value)
- list of scalars: a single Value column
"""

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .base import Formatter, clean_excel_value, write_styled_excel

SHEET_NAME = "Results"


def check(data: Any) -> str | None:
    return None


def _cell(value: Any) -> str:
    if isinstance(value, list):
        return clean_excel_value("; ".join(json.dumps(v) if isinstance(v, (dict, list)) else str(v) for v in value))
    if isinstance(value, dict):
        return clean_excel_value(json.dumps(value, ensure_ascii=False))
    return clean_excel_value(value)


def to_dataframe(data: Any) -> pd.DataFrame:
    if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
        df = pd.json_normalize(data, sep=".")
    elif isinstance(data, list):
        df = pd.DataFrame({"Value": data})
    elif isinstance(data, dict):
        df = pd.DataFrame({"Key": list(data), "Value": list(data.values())})
    else:
        df = pd.DataFrame({"Value": [data]})
    return df.apply(lambda col: col.map(_cell)) if not df.empty else df


def write(data: Any, output_file: Path) -> None:
    write_styled_excel(to_dataframe(data), output_file, SHEET_NAME)


FORMATTER = Formatter(
    id="generic-excel",
    name="Generic Excel",
    description="Any JSON result as a flat sheet: one row per object, nested fields as dotted columns.",
    extension=".xlsx",
    check=check,
    write=write,
    to_dataframe=to_dataframe,
)
