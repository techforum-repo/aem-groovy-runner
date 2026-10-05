from __future__ import annotations

"""Formatter contract + shared Excel styling.

A formatter turns one saved run result (the JSON file) into an output file.
`check()` returns None when the data fits the formatter, else a short
reason it doesn't — the Run page shows that instead of failing mid-batch.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ILLEGAL_CHARACTERS_RE = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")


@dataclass(frozen=True)
class Formatter:
    id: str
    name: str
    description: str
    extension: str
    check: Callable[[Any], str | None]
    write: Callable[[Any, Path], None]
    to_dataframe: Callable[[Any], pd.DataFrame]


def clean_excel_value(value: Any) -> str:
    if value is None:
        return ""
    return ILLEGAL_CHARACTERS_RE.sub("", str(value))


EXCEL_MAX_DATA_ROWS = 1_048_575  # 1,048,576 rows per sheet, minus the header


def write_styled_excel(df: pd.DataFrame, target: Any, sheet_name: str) -> None:
    """Same sheet treatment as format_references_batch.py: frozen header,
    autofilter, column widths fitted to content (capped at 120)."""
    if len(df) > EXCEL_MAX_DATA_ROWS:
        raise ValueError(f"{len(df):,} rows is more than one Excel sheet can hold ({EXCEL_MAX_DATA_ROWS:,}). "
                         "Use the JSON, or format / combine fewer results.")
    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        ws = writer.sheets[sheet_name]

        # openpyxl stores any string starting with "=" as a live formula, so an
        # AEM title like '=HYPERLINK("http://…")' would become an active link or
        # formula in the report. Everything written here is data: force text.
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        for column_cells in ws.columns:
            max_length = 0
            column_letter = column_cells[0].column_letter
            for cell in column_cells:
                cell_value = str(cell.value) if cell.value is not None else ""
                max_length = max(max_length, len(cell_value))
            ws.column_dimensions[column_letter].width = min(max_length + 2, 120)
