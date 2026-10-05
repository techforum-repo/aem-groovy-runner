from __future__ import annotations

import pytest

from groovy_runner.groovy_script import (JSON_END, JSON_START, PLACEHOLDER, GroovyOutputError, build_script,
                                         extract_config, parse_output, split_payload)

TEMPLATE = f'def CONFIG = new JsonSlurper().parseText(new String("{PLACEHOLDER}".decodeBase64(), "UTF-8"))'


def test_config_round_trips_with_awkward_characters():
    path = '/content/dam/a "quoted" $dollar\\back, comma/Product Library'
    script = build_script(TEMPLATE, {"p": path, "flags": [True, None]})
    assert PLACEHOLDER not in script
    assert extract_config(script) == {"p": path, "flags": [True, None]}


def test_plain_script_without_placeholder():
    assert build_script("println 1", {}) == "println 1"
    with pytest.raises(RuntimeError, match="placeholder"):
        build_script("println 1", {"x": 1})


def test_parse_output_markers_win_over_noise():
    out = f"[not json\n{JSON_START}\n{{\"rows\": [{{\"a\": 1}}]}}\n{JSON_END}\n"
    assert parse_output(out) == {"rows": [{"a": 1}]}


def test_parse_output_fallback_handles_hand_written_console_scripts():
    # What the original console script printed: a count line, a blank, then pretty JSON.
    out = 'Total rows generated: 1\n\n[\n    {\n        "assetPath": "/x"\n    }\n]\n'
    assert parse_output(out) == [{"assetPath": "/x"}]


@pytest.mark.parametrize("out", ["", "<html>login</html>", f"{JSON_START}\nnot json\n{JSON_END}"])
def test_parse_output_rejects_bad_output(out):
    with pytest.raises(GroovyOutputError):
        parse_output(out)


def test_split_payload():
    assert split_payload({"rows": [1], "assetsScanned": 3}) == ([1], {"assetsScanned": 3})
    assert split_payload([1, 2]) == ([1, 2], {})
    assert split_payload({"a": 1}) == ({"a": 1}, {})
