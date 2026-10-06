from __future__ import annotations

import io

import openpyxl

from groovy_runner import formatters
from groovy_runner.formatters import asset_reference, generic_excel
from groovy_runner.utils import slug_for_path, split_paths

ROW = {"assetPath": "/content/dam/a.pdf", "assetTitle": "Bad\x07char", "referenceUrl": "/content/site/p", "pageStatus": "Published"}


def _sheet(formatter, data, tmp_path):
    out = tmp_path / "x.xlsx"
    formatter.write(data, out)
    return openpyxl.load_workbook(io.BytesIO(out.read_bytes())).active


def test_asset_reference_layout_matches_batch_script(tmp_path):
    [row] = asset_reference.transform_data([ROW])
    assert list(row) == asset_reference.COLUMNS and row["Asset Title"] == "Badchar"
    ws = _sheet(asset_reference.FORMATTER, [ROW], tmp_path)
    assert ws.title == "Asset References"
    assert [c.value for c in ws[1]] == asset_reference.COLUMNS
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref


def test_asset_reference_rejects_other_shapes():
    assert asset_reference.check({"a": 1})
    assert asset_reference.check([{"pagePath": "/x"}])
    assert asset_reference.check([]) is None


def test_generic_flattens_nested_and_handles_any_shape(tmp_path):
    data = [{"path": "/a", "meta": {"title": "T"}, "tags": ["x", "y"]}, {"path": "/b", "extra": 1}]
    df = generic_excel.to_dataframe(data)
    assert set(df.columns) == {"path", "meta.title", "tags", "extra"}
    assert df.loc[0, "tags"] == "x; y"
    assert list(generic_excel.to_dataframe({"k": 1}).columns) == ["Key", "Value"]
    assert list(generic_excel.to_dataframe([1, 2]).columns) == ["Value"]
    assert len(generic_excel.to_dataframe([])) == 0
    ws = _sheet(generic_excel.FORMATTER, data, tmp_path)
    assert ws.title == "Results" and ws.freeze_panes == "A2"


def test_registry():
    assert set(formatters.FORMATTERS) == {"asset-reference-excel", "generic-excel"}


def test_slug_and_split_paths():
    assert slug_for_path("/content/dam/acme/Product Library/datasheets/") == "Product-Library_datasheets"
    assert slug_for_path("/content/acme/en-us/products") == "en-us_products"  # last two parts only
    assert slug_for_path("/content/dam/acme") == "acme" and slug_for_path("/content/dam") == "dam"
    assert slug_for_path("/content") == "content"
    assert split_paths(" /a, b \n\n/c\n/a, b") == ["/a, b", "/c"]
