from __future__ import annotations

from pathlib import Path

import pytest

from groovy_runner import readonly
from groovy_runner.errors import friendly_error
from groovy_runner.scripts_registry import discover


def rules(src: str) -> set[str]:
    return {v.rule for v in readonly.check(src)}


@pytest.mark.parametrize("src, rule", [
    ("session.save()", "persist"),
    ("resourceResolver.commit()", "persist"),
    ('activate "/content/x"', "console-write"),
    ('delete("/content/x")', "console-write"),
    ("node.setProperty('a', 1)", "jcr-write"),
    ("node.addNode('x')", "jcr-write"),
    ("node.remove()", "jcr-remove"),
    ("node.set('jcr:title', 'x')", "node-set"),
    ("resourceResolver.create(parent, 'x', [:])", "sling-write"),
    ("pageManager.delete(page, false)", "sling-write"),
    ("pageManager.createPage('/a', 't', 'n', 'T')", "create-x"),
    ("def m = r.adaptTo(ModifiableValueMap)", "writable-types"),
    ("getService(Replicator).replicate(session, type, path)", "replication"),
    ("getService(JobManager)", "workflow-jobs"),
    ("session.getWorkspace().copy('/a', '/b')", "workspace"),
    ("getService(ResourceResolverFactory)", "privileged"),
    ("session.impersonate(creds)", "privileged"),
    ('obj."$name"()', "dynamic-call"),
    ("obj.'save'()", "dynamic-call"),
    ("def f = session.&save", "method-pointer"),
    ("session.metaClass.save = {}", "metaprogramming"),
    ("Class.forName('x')", "reflection"),
    ("new GroovyShell().evaluate(code)", "eval"),
    ("@groovy.transform.CompileStatic\ndef x() {}", "static-compile"),
    ("@Grab('x:y:1')", "static-compile"),
    ("binding.setVariable('session', null)", "guard-tamper"),
    ("'ls'.execute()", "os"),
    ("new ProcessBuilder('ls')", "os"),
    ("new File('/tmp/x').text", "filesystem"),
    ("new URL('http://x').text", "network"),
    ('println "${session.save()}"', "persist"),  # code inside GString interpolation is checked
])
def test_blocks_writes_and_escape_hatches(src, rule):
    assert rule in rules(src)


@pytest.mark.parametrize("src", [
    "// session.save() in a comment",
    "/* resourceResolver.commit() */",
    'println "please save() and delete(this) — just text"',
    "def s = '''activate \"x\"'''",
    'PredicateGroup.create(predicates)',
    "def rootPath = '/content'; def workspaceName = 1; def deleted = 0",
    "list.remove(0); map.remove('k'); cal.set(Calendar.YEAR, 2020)",
    'resourceResolver.getResource("/content").getChild("jcr:content")?.adaptTo(ValueMap)?.get("x", "")',
    'queryBuilder.createQuery(PredicateGroup.create(p), session).getResult().hits.each { println it.path }',
    '"ERROR: ${e.message}".toString()',
])
def test_allows_read_only_code(src):
    assert readonly.check(src) == []


def test_bundled_and_original_scripts_pass():
    for script in discover():
        assert readonly.check(script.template()) == [], script.id
        assert not [p for p in script.problems if p.startswith("Read-only")]


def test_violation_reports_line_and_snippet():
    [v] = readonly.check("println 1\n\n  session.save()  // persist\n")
    assert (v.line, v.rule) == (3, "persist") and v.snippet == "session.save()  // persist"


def test_prepare_injects_guard_after_imports_and_blocks_violations():
    src = "import a.B\nimport c.D as E\n\nprintln 1\n"
    out = readonly.prepare(src)
    assert out.index("import c.D as E") < out.index(readonly.GUARD_MARKER) < out.index("println 1")
    assert readonly.prepare("println 1").startswith(readonly.GUARD_MARKER)
    with pytest.raises(readonly.ReadOnlyViolationError) as err:
        readonly.prepare("session.save()")
    assert "Blocked by the read-only check" in friendly_error(err.value).title


def test_guard_itself_would_fail_the_check():
    # The guard uses metaprogramming; user scripts can't, so they can't imitate or undo it.
    assert readonly.check(readonly.GUARD)


def test_discovery_flags_write_scripts(tmp_path: Path):
    (tmp_path / "writer.groovy").write_text("session.save()\n")
    [script] = discover(tmp_path)
    assert any(p.startswith("Read-only check, line 1") for p in script.problems)
