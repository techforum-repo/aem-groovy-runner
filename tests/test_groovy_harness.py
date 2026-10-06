"""Runs the real bundled Groovy scripts on a JVM against stub AEM APIs
(tests/groovy_harness). Skipped unless GROOVY_JARS points at groovy +
groovy-json jars, e.g.
GROOVY_JARS=~/jars/groovy-4.0.22.jar:~/jars/groovy-json-4.0.22.jar pytest
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from groovy_runner import readonly
from groovy_runner.groovy_script import build_script, parse_output, split_payload
from groovy_runner.scripts_registry import discover, expand_runs

HARNESS = Path(__file__).parent / "groovy_harness"
JARS = os.environ.get("GROOVY_JARS", "")
pytestmark = pytest.mark.skipif(not JARS or not shutil.which("java"), reason="GROOVY_JARS / java not available")

ROOT = "/content/dam/acme/Inter Cardio"
SCRIPTS = {s.id: s for s in discover()}
SAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples"
SCRIPTS.update({s.id: s for s in discover(SAMPLE_DIR)})  # the skeleton shown on the Scripts page


@pytest.fixture(scope="module")
def classes(tmp_path_factory):
    out = tmp_path_factory.mktemp("classes")
    subprocess.run(["java", "-cp", JARS, "org.codehaus.groovy.tools.FileSystemCompiler", "-d", str(out),
                    *map(str, sorted((HARNESS / "stubs").glob("*.groovy")))], check=True)
    return out


def _run(classes, tmp_path, script_id: str, values: dict, extra: dict | None = None):
    script = SCRIPTS[script_id]
    [(_, _, config)] = expand_runs(script, values)
    config.update(extra or {})  # internal settings such as batching's directOnly
    path = tmp_path / "script.groovy"
    path.write_text(readonly.prepare(build_script(script.template(), config)))  # exactly what is sent
    proc = subprocess.run(["java", "-cp", f"{JARS}:{classes}", "groovy.ui.GroovyMain", str(HARNESS / "Harness.groovy"), str(path)],
                          capture_output=True, text=True, check=True)
    return split_payload(parse_output(proc.stdout))


def test_asset_exclusions_format_filter_and_encoded_fallback(classes, tmp_path):
    rows, meta = _run(classes, tmp_path, "asset-reference-report", {
        "parentDamPath": [ROOT + "/"], "excludedAssetFolders": [ROOT + "/archive"], "assetFormats": ["application/PDF"]})
    assert meta["skippedExcludedFolder"] == 1 and meta["skippedFormat"] == 1
    refs = {(r["assetPath"].rsplit("/", 1)[-1], r["referenceUrl"], r["pageStatus"]) for r in rows}
    # /content/dam/other is dropped by the default excluded reference root; b.pdf only matches via %20.
    assert refs == {("a.pdf", "/content/site/en/page1", "Modified"), ("b.pdf", "/content/site/en/gone", "Inactive")}


def test_asset_unreferenced_toggle_and_no_excluded_roots(classes, tmp_path):
    rows, _ = _run(classes, tmp_path, "asset-reference-report", {
        "parentDamPath": [ROOT], "excludedReferenceRoots": [], "includeUnreferenced": False})
    assert all(r["pageStatus"] != "No Reference" for r in rows)
    assert "/content/dam/other" in {r["referenceUrl"] for r in rows}


def test_asset_missing_path_reports_error(classes, tmp_path):
    _, meta = _run(classes, tmp_path, "asset-reference-report", {"parentDamPath": ["/content/dam/nope"]})
    assert "not found" in meta["error"]


GUARD_CASES = {
    "read": ('println session.getUserID()\nprintln pageManager.getPage("/a")\nprintln resourceResolver.getResource("/b")\n', None),
    "save": ("session.save()\n", "save"),
    "commit": ("resourceResolver.commit()\n", "commit"),
    "create": ('resourceResolver.create(null, "x", [:])\n', "create"),
    "page-delete": ('pageManager.delete(pageManager.getPage("/a"), false)\n', "delete"),
    "aliased": ("def s = session\ns.save()\n", "save"),
    "in-closure": ("[1].each { session.save() }\n", "save"),
}


@pytest.fixture(scope="module")
def java_stubs(tmp_path_factory):
    if not shutil.which("javac"):
        pytest.skip("javac not available")
    out = tmp_path_factory.mktemp("javastubs")
    subprocess.run(["javac", "-d", str(out), str(HARNESS / "java" / "JavaStubs.java")], check=True)
    return out


@pytest.mark.parametrize("mode", ["groovy", "java"])
@pytest.mark.parametrize("case", list(GUARD_CASES))
def test_runtime_guard_blocks_writes_on_groovy_and_java_objects(java_stubs, tmp_path, mode, case):
    """The static check would already reject these; this proves the second
    layer (the injected guard) also stops them inside the script."""
    body, blocked = GUARD_CASES[case]
    path = tmp_path / "g.groovy"
    path.write_text(readonly._insert_after_imports(body, readonly.GUARD))
    proc = subprocess.run(["java", "-cp", f"{JARS}:{java_stubs}", "groovy.ui.GroovyMain",
                           str(HARNESS / "GuardHarness.groovy"), str(path), mode], capture_output=True, text=True, check=True)
    calls = proc.stdout.split("CALLS=")[-1]
    if blocked:
        assert f"OUTCOME=blocked: Groovy Runner read-only mode: {blocked}() is blocked" in proc.stdout
        assert blocked not in calls.replace("getPage", "")
    else:
        assert "OUTCOME=completed" in proc.stdout and "getResource" in calls and "getPage" in calls


def _run_content(classes, tmp_path, script_id, values, extra=None):
    script = SCRIPTS[script_id]
    [(_, _, config)] = expand_runs(script, values)
    config.update(extra or {})
    path = tmp_path / "script.groovy"
    path.write_text(readonly.prepare(build_script(script.template(), config)))
    proc = subprocess.run(["java", "-cp", f"{JARS}:{classes}", "groovy.ui.GroovyMain",
                           str(HARNESS / "ContentHarness.groovy"), str(path)], capture_output=True, text=True, check=True)
    rows, meta = split_payload(parse_output(proc.stdout))
    sql = [line[4:] for line in proc.stdout.splitlines() if line.startswith("SQL=")]
    return rows, meta, sql


def test_page_report_tree_walk_columns_and_filters(classes, tmp_path):
    rows, meta, _ = _run_content(classes, tmp_path, "page-report", {"rootPath": ["/content/site"]})
    assert [r["Path"] for r in rows] == ["/content/site", "/content/site/en", "/content/site/en/products",
                                         "/content/site/en/products/x", "/content/site/en/archive"]
    assert list(rows[0]) == ["Path", "Title", "Status", "Last Modified", "Last Published", "Last Modified By",
                             "Last Published By", "Template", "Template Type", "Created", "Created By", "Depth"]
    en = rows[1]
    assert (en["Status"], en["Template Type"], en["Depth"]) == ("Modified", "Legacy Template", 3)
    assert rows[4]["Title"] == "archive"  # no jcr:title -> page name, like the original script

    rows, _, _ = _run_content(classes, tmp_path, "page-report", {
        "rootPath": ["/content/site"], "excludedPaths": ["/content/site/en/archive"], "includeRoot": False,
        "maxDepth": 2, "templates": ["/conf/s/product"]})
    assert [r["Path"] for r in rows] == ["/content/site/en/products"]


def test_page_report_missing_root(classes, tmp_path):
    _, meta, _ = _run_content(classes, tmp_path, "page-report", {"rootPath": ["/content/nope"]})
    assert "Path not found" in meta["error"]


def test_assets_by_type_summary_list_and_sql(classes, tmp_path):
    rows, meta, sql = _run_content(classes, tmp_path, "assets-by-type", {
        "rootPath": ["/content/dam/b"], "formats": ["application/pdf", "image/*"],
        "excludedFolders": ["/content/dam/b/old"]})
    assert "dc:format] = 'application/pdf'" in sql[0] and "LIKE 'image/%'" in sql[0]
    assert "NOT ISDESCENDANTNODE(a, '/content/dam/b/old')" in sql[0]
    assert meta["matchedAssets"] == 5 and meta["mode"] == "summary"
    assert {r["Format"]: (r["Assets"], r["Total Size (MB)"]) for r in rows} == {
        "application/pdf": (3, 4.0), "image/png": (1, 1.0), "image/jpeg": (1, 0.5)}

    rows, _, _ = _run_content(classes, tmp_path, "assets-by-type", {
        "rootPath": ["/content/dam/b"], "formats": ["application/pdf"], "groupByFolderLevels": 1})
    assert {(r["Folder"], r["Format"], r["Assets"]) for r in rows} == {
        ("/content/dam/b/a", "application/pdf", 2), ("/content/dam/b/old", "application/pdf", 1),
        ("/content/dam/b", "application/pdf", 1)}

    rows, meta, _ = _run_content(classes, tmp_path, "assets-by-type", {
        "rootPath": ["/content/dam/b"], "mode": "list", "maxAssets": 2})
    assert len(rows) == 2 and meta["matchedAssets"] == 6 and meta["truncatedAt"] == 2
    assert rows[0]["Size (bytes)"] == 2_097_152 and rows[0]["Folder"] == "/content/dam/b/a"


def test_assets_by_type_quotes_in_path_are_escaped(classes, tmp_path):
    _, _, sql = _run_content(classes, tmp_path, "assets-by-type", {"rootPath": ["/content/dam/it's"]})
    assert "ISDESCENDANTNODE(a, '/content/dam/it''s')" in sql[0]  # SQL2 quote doubled, not injectable


def test_page_report_on_a_folder_walks_every_site(classes, tmp_path):
    rows, meta, _ = _run_content(classes, tmp_path, "page-report", {"rootPath": ["/content"]})
    paths = [r["Path"] for r in rows]
    assert paths[0] == "/content/site" and "/content/other" in paths and "/content" not in paths
    assert len(paths) == 6 and meta["rootIsFolder"] is True and meta["sitesWalked"] == 2
    # Depth counts from the folder: site roots are level 1 below /content.
    rows, _, _ = _run_content(classes, tmp_path, "page-report", {"rootPath": ["/content"], "maxDepth": 1})
    assert [r["Path"] for r in rows] == ["/content/site", "/content/other"]
    rows, _, _ = _run_content(classes, tmp_path, "page-report", {"rootPath": ["/content"], "maxDepth": 2})
    assert [r["Path"] for r in rows] == ["/content/site", "/content/site/en", "/content/other"]


def test_assets_query_per_folder_matches_single_query(classes, tmp_path):
    values = {"rootPath": ["/content/dam/b"], "formats": ["application/pdf", "image/*"]}
    single, single_meta, single_sql = _run_content(classes, tmp_path, "assets-by-type", values)
    split, split_meta, split_sql = _run_content(classes, tmp_path, "assets-by-type", {**values, "queryPerFolder": True})
    assert sorted(map(str, split)) == sorted(map(str, single)) and split_meta["matchedAssets"] == single_meta["matchedAssets"]
    assert len(single_sql) == 1 and split_meta["queriesRun"] == 3 and len(split_sql) == 3  # a, img, old
    assert all("ISDESCENDANTNODE(a, '/content/dam/b/" in q for q in split_sql)
    _, excl_meta, excl_sql = _run_content(classes, tmp_path, "assets-by-type", {
        **values, "queryPerFolder": True, "excludedFolders": ["/content/dam/b/old"]})
    assert excl_meta["queriesRun"] == 2 and excl_meta["matchedAssets"] == 5


def _discover(classes, tmp_path, config):
    from groovy_runner.runner import DISCOVER_TEMPLATE
    path = tmp_path / "discover.groovy"
    path.write_text(readonly.prepare(build_script(DISCOVER_TEMPLATE.read_text(), config)))
    proc = subprocess.run(["java", "-cp", f"{JARS}:{classes}", "groovy.ui.GroovyMain",
                           str(HARNESS / "ContentHarness.groovy"), str(path)], capture_output=True, text=True, check=True)
    return parse_output(proc.stdout)


def test_discovery_finds_site_pages_and_dam_folders(classes, tmp_path):
    sites = _discover(classes, tmp_path, {"root": "/content", "kind": "page", "excludes": [], "levels": 1})
    assert sites["children"] == ["/content/other", "/content/site"]  # /content/dam is a folder, not a page
    assert sites["direct"] == []  # a folder root has no content of its own

    sites = _discover(classes, tmp_path, {"root": "/content", "kind": "page", "excludes": ["/content/other"], "levels": 1})
    assert sites["children"] == ["/content/site"] and sites["skipped"] == ["/content/other"]

    folders = _discover(classes, tmp_path, {"root": "/content/dam/b", "kind": "folder",
                                            "excludes": ["/content/dam/b/old"], "levels": 1})
    assert folders["children"] == ["/content/dam/b/a", "/content/dam/b/img"]  # jcr:content ignored
    assert folders["skipped"] == ["/content/dam/b/old"]
    # loose.pdf sits directly in the entered folder: a direct part, run with its subfolders excluded
    assert folders["direct"] == [{"path": "/content/dam/b", "exclude": ["/content/dam/b/a", "/content/dam/b/img"]}]

    assert "Path not found" in _discover(classes, tmp_path, {"root": "/content/nope", "kind": "folder",
                                                             "excludes": [], "levels": 1})["error"]


def test_discovery_levels_two_covers_everything_above_the_split(classes, tmp_path):
    # Pages: the site home page gets a direct (own row) part; a site with no child pages is run whole.
    pages = _discover(classes, tmp_path, {"root": "/content", "kind": "page", "excludes": [], "levels": 2})
    assert pages["children"] == ["/content/other", "/content/site/en"]
    assert pages["direct"] == [{"path": "/content/site", "exclude": ["/content/site/en"]}]
    # DAM: folders holding assets above level 2 get a direct part; a level-1 folder without subfolders runs whole.
    dam = _discover(classes, tmp_path, {"root": "/content/dam/b", "kind": "folder", "excludes": [], "levels": 2})
    assert dam["children"] == ["/content/dam/b/a/sub", "/content/dam/b/img", "/content/dam/b/old"]
    assert [d["path"] for d in dam["direct"]] == ["/content/dam/b", "/content/dam/b/a"]
    assert dam["direct"][1]["exclude"] == ["/content/dam/b/a/sub"]


def test_discovery_includes_an_entered_page_root_itself(classes, tmp_path):
    """Batching /content/site runs its child pages; the site page itself gets its own direct part."""
    site = _discover(classes, tmp_path, {"root": "/content/site", "kind": "page", "excludes": [], "levels": 1})
    assert site["children"] == ["/content/site/en"]
    assert site["direct"] == [{"path": "/content/site", "exclude": ["/content/site/en"]}]


def _batched_rows(classes, tmp_path, script_id, values, root, kind, levels, path_column):
    """Discover like the app does, then run the UNCHANGED script once per part with ordinary inputs."""
    from groovy_runner.runner import _batch_runs, discover_roots
    script = SCRIPTS[script_id]

    class Harness:  # discovery through the real built-in script on the JVM
        def run_script(self, text):
            path = tmp_path / "discover.groovy"
            path.write_text(readonly.prepare(text))
            proc = subprocess.run(["java", "-cp", f"{JARS}:{classes}", "groovy.ui.GroovyMain",
                                   str(HARNESS / "ContentHarness.groovy"), str(path)], capture_output=True, text=True,
                                  check=True)
            return type("R", (), {"output": proc.stdout})()
    d = discover_roots(Harness(), root, kind, [], levels)
    assert not d.error
    rows = []
    for _, _, config in _batch_runs(script, values, [d]):
        assert "directOnly" not in config  # plain script inputs only
        part_rows, _, _ = _run_content(classes, tmp_path, script_id, {**values, script.batch.input: [config[script.batch.input]]},
                                       {k: v for k, v in config.items() if k != script.batch.input})
        rows += [r[path_column] for r in part_rows]
    return rows


@pytest.mark.parametrize("levels", [1, 2, 3])
def test_batch_parts_together_cover_exactly_the_unbatched_run_dam(classes, tmp_path, levels):
    """Whatever the Levels, the parts list every asset under the entered path, each exactly once."""
    values = {"rootPath": ["/content/dam/b"], "mode": "list"}
    batched = _batched_rows(classes, tmp_path, "assets-by-type", values, "/content/dam/b", "folder", levels, "Asset Path")
    whole, _, _ = _run_content(classes, tmp_path, "assets-by-type", values)
    assert sorted(batched) == sorted(r["Asset Path"] for r in whole) and len(set(batched)) == len(batched)
    assert "/content/dam/b/loose.pdf" in batched and "/content/dam/b/a/one.pdf" in batched


@pytest.mark.parametrize("levels", [1, 2, 3])
@pytest.mark.parametrize("root", ["/content", "/content/site"])
def test_batch_parts_together_cover_exactly_the_unbatched_run_pages(classes, tmp_path, levels, root):
    values = {"rootPath": [root]}
    batched = _batched_rows(classes, tmp_path, "page-report", values, root, "page", levels, "Path")
    whole, _, _ = _run_content(classes, tmp_path, "page-report", values)
    assert sorted(batched) == sorted(r["Path"] for r in whole) and len(set(batched)) == len(batched)


@pytest.mark.parametrize("levels", [1, 2, 3])
def test_sample_skeleton_is_batch_ready_and_batching_changes_nothing(classes, tmp_path, levels):
    """The skeleton on the Scripts page: no batching code, yet batched == unbatched."""
    values = {"rootPath": ["/content/dam/b"]}
    batched = _batched_rows(classes, tmp_path, "asset-sample", values, "/content/dam/b", "folder", levels,
                            "Asset Path")
    whole, _, _ = _run_content(classes, tmp_path, "asset-sample", values)
    assert len(whole) == 6  # every asset under /content/dam/b (z.pdf is under /content/dam/other)
    assert sorted(batched) == sorted(r["Asset Path"] for r in whole) and len(set(batched)) == len(batched)


@pytest.mark.parametrize("levels", [1, 2, 3])
@pytest.mark.parametrize("root", ["/content", "/content/site"])
def test_page_sample_skeleton_is_batch_ready_and_batching_changes_nothing(classes, tmp_path, levels, root):
    values = {"rootPath": [root]}
    batched = _batched_rows(classes, tmp_path, "page-sample", values, root, "page", levels, "Path")
    whole, _, _ = _run_content(classes, tmp_path, "page-sample", values)
    assert whole and sorted(batched) == sorted(r["Path"] for r in whole) and len(set(batched)) == len(batched)


def _run_audit(classes, tmp_path, values, *harness_args):
    script = SCRIPTS["audit-events"]
    [(_, _, config)] = expand_runs(script, values)
    path = tmp_path / "audit.groovy"
    path.write_text(readonly.prepare(build_script(script.template(), config)))  # exactly what is sent
    proc = subprocess.run(["java", "-cp", f"{JARS}:{classes}", "groovy.ui.GroovyMain",
                           str(HARNESS / "AuditHarness.groovy"), str(path), *harness_args],
                          capture_output=True, text=True, check=True)
    rows, meta = split_payload(parse_output(proc.stdout))
    return rows, meta, [line[4:] for line in proc.stdout.splitlines() if line.startswith("SQL=")]


SITE = "/content/acme/en-us/products"
DAM = "/content/dam/acme/brochures"
ONLY_ITSELF, BELOW = SCRIPTS["audit-events"].inputs[1].options[1:]  # manifest "scope" options


def test_audit_page_events_with_defaults(classes, tmp_path):
    rows, meta, [sql] = _run_audit(classes, tmp_path, {"basePath": [SITE]})
    assert "FROM [cq:AuditEvent]" in sql and "ISDESCENDANTNODE([/var/audit])" in sql and "CAST(" not in sql
    assert f"([cq:path] = '{SITE}' OR [cq:path] LIKE '{SITE}/%')" in sql  # the entered path itself by default
    got = [(r["Log"], r["Path"].replace(SITE, "") or "/", r["Event Type"], r["User"]) for r in rows]
    # Default types only (no VersionCreated); not products_old (LIKE's "_" wildcard); a deleted page is included.
    assert got == [("Page", "/", "PageModified", "root.user"), ("Page", "/archive/old", "PageDeleted", "amy"),
                   ("Page", "/stents", "PageModified", "jane"), ("Replication", "/stents/stent-a", "Activate", "john"),
                   ("Page", "/stents/stent-a", "PageModified", "jane")]  # newest first
    assert rows[0]["Event Time"] == "2026-09-06 08:00:00"
    assert (meta["pageEvents"], meta["replicationEvents"], meta["assetEvents"]) == (4, 1, 0)


def test_audit_asset_events_users_and_old_user_property(classes, tmp_path):
    rows, meta, _ = _run_audit(classes, tmp_path, {"basePath": [DAM]})
    got = [(r["Log"], r["Path"].rsplit("/", 1)[-1], r["Event Type"], r["User"]) for r in rows]
    # Asset log + asset publishing; viewing and rendition noise filtered out; cq:userId read as a fallback.
    assert got == [("Asset", "b.pdf", "ASSET_CREATED", "legacy.user"), ("Replication", "a.pdf", "Activate", "john"),
                   ("Asset", "a.pdf", "METADATA_UPDATED", "jane")]
    rows, _, _ = _run_audit(classes, tmp_path, {"basePath": [f"{DAM}/a.pdf"], "eventTypes": [], "users": ["JANE"]})
    assert [r["Event Type"] for r in rows] == ["METADATA_UPDATED"]  # a single asset; user match is case-insensitive
    rows, _, _ = _run_audit(classes, tmp_path, {"basePath": [DAM], "replicationEvents": False, "assetEvents": True})
    assert {r["Log"] for r in rows} == {"Asset"}


def test_audit_levels_root_excludes_types_and_since(classes, tmp_path):
    rows, _, _ = _run_audit(classes, tmp_path, {"basePath": [SITE], "levels": 2})
    assert {r["Path"] for r in rows} == {f"{SITE}/stents/stent-a", f"{SITE}/archive/old"}
    rows, _, _ = _run_audit(classes, tmp_path, {"basePath": [SITE], "scope": BELOW, "eventTypes": [],
                                                "excludedPaths": [f"{SITE}/archive"]})
    paths = [r["Path"] for r in rows]
    assert SITE not in paths and f"{SITE}/archive/old" not in paths
    assert "VersionCreated" in [r["Event Type"] for r in rows] and len(rows) == 4  # every type now
    _, _, [sql] = _run_audit(classes, tmp_path, {"basePath": [SITE], "sinceDays": 30, "scope": BELOW})
    assert "[cq:time] >= CAST('" in sql and f"[cq:path] LIKE '{SITE}/%'" in sql and "[cq:path] = " not in sql


def test_audit_query_failure_explains_what_to_do(classes, tmp_path):
    rows, meta, _ = _run_audit(classes, tmp_path, {"basePath": [SITE]}, "fail")
    assert rows == [] and "more than 100000 nodes" in meta["error"] and "last N days" in meta["error"]


def test_audit_path_quotes_are_escaped(classes, tmp_path):
    _, _, [sql] = _run_audit(classes, tmp_path, {"basePath": ["/content/it's"]})
    assert "LIKE '/content/it''s/%'" in sql


def test_audit_single_page_or_asset_only(classes, tmp_path):
    """Scope "only this path itself": one page's own history, not its child pages; same for one asset."""
    rows, _, [sql] = _run_audit(classes, tmp_path, {"basePath": [f"{SITE}/stents"], "scope": ONLY_ITSELF})
    assert f"[cq:path] = '{SITE}/stents'" in sql and "LIKE" not in sql  # exact-path query
    assert [(r["Path"], r["Event Type"]) for r in rows] == [(f"{SITE}/stents", "PageModified")]  # not stent-a
    rows, _, _ = _run_audit(classes, tmp_path, {"basePath": [f"{DAM}/a.pdf"], "scope": ONLY_ITSELF})
    assert [r["Event Type"] for r in rows] == ["Activate", "METADATA_UPDATED"]
