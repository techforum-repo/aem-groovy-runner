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
    from groovy_runner.scripts_registry import validate_skips
    scripts = {s.id: s for s in discover()}
    assert validate_skips(scripts["assets-by-type"], ["/content/dam/projects"]) == []
    assert validate_skips(scripts["assets-by-type"], ["/content/projects"])
    assert validate_skips(scripts["page-report"], ["/content/campaigns"]) == []
    assert validate_skips(scripts["page-report"], ["/etc/x"])
    for script in scripts.values():  # shipped defaults satisfy their own rule
        assert script.system_excludes and validate_skips(script, list(script.system_excludes.paths)) == [], script.id
    assert "/content/dam" not in scripts["page-report"].system_excludes.paths  # never a page: no-op exclude removed


def test_system_skips_merge_into_the_scripts_own_exclusions():
    from groovy_runner.scripts_registry import with_skips
    scripts = {s.id: s for s in discover()}
    merged = with_skips(scripts["assets-by-type"], {"rootPath": ["/content/dam"], "excludedFolders": ["/content/dam/x"]},
                        ["/content/dam/projects/", "/content/dam/x"])
    assert merged["excludedFolders"] == ["/content/dam/x", "/content/dam/projects"]  # normalized, de-duplicated
    assert with_skips(scripts["assets-by-type"], {"rootPath": ["/content/dam"]}, []) == {"rootPath": ["/content/dam"]}
    runs = expand_runs(scripts["asset-reference-report"],
                       with_skips(scripts["asset-reference-report"], {"parentDamPath": ["/content/dam"]},
                                  ["/content/dam/collections"]))
    assert runs[0][2]["excludedAssetFolders"] == ["/content/dam/collections"]  # reaches the Groovy CONFIG


def _write_script(tmp_path, manifest):
    folder = tmp_path / "s"
    folder.mkdir(parents=True)
    (folder / "script.groovy").write_text('def CONFIG = "__CONFIG_B64__"\n')
    (folder / "manifest.json").write_text(json.dumps(manifest))
    [script] = discover(tmp_path)
    return script


def test_batching_is_inferred_for_any_path_script_with_an_exclusion_input(tmp_path):
    """A new script needs no batching code and no batch section: the app infers it."""
    script = _write_script(tmp_path, {"inputs": [
        {"key": "folders", "type": "path_list", "iterate": True, "must_start_with": "/content/dam"},
        {"key": "skip", "label": "Excluded folders", "type": "path_list"}]})
    assert script.problems == []
    assert (script.batch.input, script.batch.kind, script.batch.exclude_input) == ("folders", "folder", "skip")


def test_no_batching_without_an_exclusion_input_or_when_switched_off(tmp_path):
    inputs = [{"key": "pages", "type": "path_list", "iterate": True, "must_start_with": "/content"}]
    excl = {"key": "excludedPaths", "type": "path_list"}
    assert _write_script(tmp_path / "a", {"inputs": inputs}).batch is None  # nowhere to put a part's children
    # Review fix: another path list that doesn't say it excludes (e.g. reference roots) is never guessed.
    assert _write_script(tmp_path / "d", {"inputs": [*inputs, {"key": "refRoots", "type": "path_list"}]}).batch is None
    assert _write_script(tmp_path / "b", {"inputs": [*inputs, excl]}).batch.kind == "page"
    assert _write_script(tmp_path / "c", {"inputs": [*inputs, excl], "batch": False}).batch is None


def test_explicit_batch_section_is_validated(tmp_path):
    script = _write_script(tmp_path, {"inputs": [{"key": "p", "type": "path_list", "iterate": True}],
                                      "batch": {"kind": "page"}})
    assert script.batch is None and any("excludeInput" in p for p in script.problems)


def test_sample_skeleton_qualifies_for_batch_and_is_read_only():
    from pathlib import Path
    samples = {s.id: s for s in discover(Path(__file__).resolve().parent.parent / "examples")}
    assert set(samples) == {"asset-sample", "page-sample"}
    for sample in samples.values():
        assert sample.problems == []  # includes the read-only check
        assert (sample.batch.input, sample.batch.exclude_input) == ("rootPath", "excludedPaths")
    assert samples["asset-sample"].batch.kind == "folder" and samples["page-sample"].batch.kind == "page"


def test_scripts_page_shows_the_sample_skeleton():
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    root = Path(__file__).resolve().parent.parent
    at = AppTest.from_file(str(root / "app.py"), default_timeout=60).run()
    at.sidebar.radio[0].set_value("Scripts").run()
    assert not at.exception, at.exception
    shown = [c.value.strip() for c in at.code]
    assert [t.label for t in at.tabs] == ["Asset script", "Page script"]
    for sample in ("asset-sample", "page-sample"):
        for name in ("script.groovy", "manifest.json"):
            assert (root / "examples" / sample / name).read_text().strip() in shown
    text = " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])
    assert str(root) not in text and "`scripts/`" in text  # no machine paths on screen


def test_audit_script_is_valid_read_only_and_not_batched():
    script = {s.id: s for s in discover()}["audit-events"]
    assert script.problems == []  # includes the read-only check (no session.workspace / execute)
    assert script.batch is None  # deleted items' events can't be found by discovery
    assert not validate(script, {"basePath": ["/content/dam/acme/a.pdf"]})  # asset paths are accepted
