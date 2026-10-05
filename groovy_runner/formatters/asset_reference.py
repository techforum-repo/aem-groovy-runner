from __future__ import annotations

"""The asset-reference Excel layout from format_references_batch.py:
fixed column order and headers, illegal-character cleanup, styled sheet."""

from pathlib import Path
from typing import Any

import pandas as pd

from .base import Formatter, clean_excel_value, write_styled_excel

SHEET_NAME = "Asset References"

# Excel header -> key in the Groovy row JSON
COLUMN_MAP: dict[str, str] = {
    "Asset Path": "assetPath",
    "Asset Title": "assetTitle",
    "Asset Format": "assetFormat",
    "Asset Status": "assetStatus",
    "Asset Created Date": "assetCreatedDate",
    "Asset Created By": "assetCreatedBy",
    "Asset Modified Date": "assetModifiedDate",
    "Asset Published Date": "assetPublishedDate",
    "Asset Modified By": "assetModifiedBy",
    "Asset Published By": "assetPublishedBy",
    "Web Page Reference URL": "referenceUrl",
    "Web Page Reference URL Status": "pageStatus",
    "Modified Date (Page)": "pageModifiedDate",
    "Published Date (Page)": "pagePublishedDate",
    "Modified By (Page)": "pageModifiedBy",
    "Published By (Page)": "pagePublishedBy",
}
COLUMNS = list(COLUMN_MAP)


def check(data: Any) -> str | None:
    if not isinstance(data, list):
        return "expects a JSON list of rows"
    if data and not all(isinstance(r, dict) and "assetPath" in r for r in data):
        return "rows don't have the asset-reference fields (assetPath, …)"
    return None


def transform_data(data: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{header: clean_excel_value(item.get(key, "")) for header, key in COLUMN_MAP.items()} for item in data]


def to_dataframe(data: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(transform_data(data), columns=COLUMNS)


def write(data: list[dict[str, Any]], output_file: Path) -> None:
    write_styled_excel(to_dataframe(data), output_file, SHEET_NAME)


FORMATTER = Formatter(
    id="asset-reference-excel",
    name="Asset reference Excel",
    description="Fixed 16-column Asset References sheet (format_references_batch.py layout).",
    extension=".xlsx",
    check=check,
    write=write,
    to_dataframe=to_dataframe,
)
