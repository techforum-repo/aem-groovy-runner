from __future__ import annotations

import json

from groovy_runner.scripts_registry import discover, expand_runs, validate


def test_bundled_scripts_are_valid():
    scripts = {s.id: s for s in discover()}
    assert {"asset-reference-report", "page-report", "assets-by-type"} <= set(scripts)
    for script in scripts.values():
        assert script.problems == [], (script.id, script.problems)
    assert not hasattr(scripts["asset-reference-report"], "formatters")  # scripts know nothing about formatting


def test_expand_runs_one_per_iterate_line_and_defaults():
    script = {s.id: s for s in discover()}["asset-reference-report"]
    runs = expand_runs(script, {"parentDamPath": ["/content/dam/a/", "/content/dam/b c"]})
    assert [(label, slug) for label, slug, _ in runs] == [("/content/dam/a", "a"), ("/content/dam/b c", "b-c")]
    config = runs[1][2]
    assert config["parentDamPath"] == "/content/dam/b c"
    assert config["excludedReferenceRoots"] == ["/content/dam"] and config["includeUnreferenced"] is True


def test_validate_required_and_prefix():
    script = {s.id: s for s in discover()}["asset-reference-report"]
    assert any("required" in e for e in validate(script, {"parentDamPath": []}))
    assert any("must start with" in e for e in validate(script, {"parentDamPath": ["/content/site"]}))
    assert validate(script, {"parentDamPath": ["/content/dam/x"]}) == []


def test_discovery_flags_manifest_problems_and_lists_plain_scripts(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "script.groovy").write_text("println 1")
    (bad / "manifest.json").write_text(json.dumps({
        "inputs": [{"key": "x", "type": "path", "iterate": True}], "formatters": ["nope"]}))
    (tmp_path / "plain.groovy").write_text("println '[1]'")
    scripts = {s.id: s for s in discover(tmp_path)}
    problems = " | ".join(scripts["bad"].problems)
    assert "isn't a list type" in problems and "placeholder" in problems  # "formatters" in a manifest is now ignored
    assert scripts["plain"].inputs == [] and scripts["plain"].problems == []
    assert expand_runs(scripts["plain"], {}) == [("plain", "plain", {})]


def test_dam_fields_require_content_dam_and_check_whole_segments():
    scripts = {s.id: s for s in discover()}
    for sid, key, main in (("assets-by-type", "excludedFolders", "rootPath"),
                           ("asset-reference-report", "excludedAssetFolders", "parentDamPath")):
        ok = {main: ["/content/dam/acme"], key: ["/content/dam/acme/archive"]}
        assert validate(scripts[sid], ok) == []
        for bad in ("/content/acme/archive", "/content/damage"):
            errors = validate(scripts[sid], {main: ["/content/dam/acme"], key: [bad]})
            assert any("must start with /content/dam" in e for e in errors), (sid, bad)
        assert validate(scripts[sid], {main: ["/content/damage"]})  # prefix look-alike rejected


def test_batch_skips_follow_the_main_path_prefix():
    from groovy_runner.scripts_registry import validate_batch
    scripts = {s.id: s for s in discover()}
    assert validate_batch(scripts["assets-by-type"], ["/content/dam/projects"]) == []
    assert validate_batch(scripts["assets-by-type"], ["/content/projects"])
    assert validate_batch(scripts["page-report"], ["/content/campaigns"]) == []
    assert validate_batch(scripts["page-report"], ["/etc/x"])
    for script in scripts.values():  # shipped defaults satisfy their own rule
        if script.batch:
            assert validate_batch(script, list(script.batch.default_excludes)) == [], script.id
    assert "/content/dam" not in scripts["page-report"].batch.default_excludes  # never a page: no-op exclude removed
