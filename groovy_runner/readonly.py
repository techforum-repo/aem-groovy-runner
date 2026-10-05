from __future__ import annotations

"""Read-only enforcement for every script this app sends to AEM.

Three layers, weakest to strongest:

1. Static check (`check()`): the script text is scanned for write APIs and
   for escape hatches that could hide one (reflection, dynamic method names,
   evaluate/GroovyShell, shell/file/network access, privileged services).
   Comments and string-literal contents are blanked out first, so a word
   like "delete" in a label or comment doesn't trip it; `${...}` inside
   GStrings is kept, because that part is code.
2. Runtime guard (`prepare()`): injected after the imports, it overrides the
   persisting methods on the console's `session`, `resourceResolver` and
   `pageManager` bindings so they throw. Without save()/commit(), JCR changes
   are discarded when the request ends.
3. AEM's own permissions. Scripts run as the signed-in user, so AEM only
   stops writes that user isn't allowed to make. If your account can write,
   layers 1-2 are the protection. They catch mistakes and obvious misuse,
   but Groovy is dynamic and Java code inside a service can write without
   any of the calls above being visible in the script, so they are not a
   guarantee against a deliberate bypass.

There's deliberately no setting to turn this off.
"""

import re
from dataclasses import dataclass

GUARD_MARKER = "// ===== Groovy Runner read-only guard (injected by the app) ====="


class ReadOnlyViolationError(RuntimeError):
    """Raised instead of sending a script that fails the static check."""

    def __init__(self, violations: list["Violation"]) -> None:
        self.violations = violations
        lines = "; ".join(f"line {v.line}: {v.message}" for v in violations[:10])
        more = f" (+{len(violations) - 10} more)" if len(violations) > 10 else ""
        super().__init__(f"Read-only check blocked this script: {lines}{more}")


@dataclass(frozen=True)
class Violation:
    line: int
    rule: str
    message: str
    snippet: str


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern[str]
    message: str


def _r(rule_id: str, pattern: str, message: str) -> Rule:
    return Rule(rule_id, re.compile(pattern), message)


_W = r"(?<![\w$])"  # start of an identifier (not part of a longer name)

RULES: list[Rule] = [
    # --- persisting / writing ---------------------------------------------
    _r("persist", r"\.\s*(save|commit)\s*\(", "persists changes (save/commit)"),
    _r("console-write", _W + r"(?<!\.)(save|activate|deactivate|delete|copy|move|rename)\s*[(\"']",
       "Groovy Console write helper (save/activate/deactivate/delete/copy/move/rename)"),
    _r("jcr-write", r"\.\s*(setProperty|addNode|removeNode|addMixin|removeMixin|setPrimaryType|orderBefore|"
       r"removeShare|removeSharedSet|removeItem|setValue|importXML|getImportContentHandler|getOrAddNode|"
       r"getOrAddResource|update|merge|checkin|checkout|checkpoint|restore\w*|lock|unlock|doneMerge|cancelMerge)\s*\(",
       "JCR write operation"),
    _r("jcr-remove", r"\.\s*remove\s*\(\s*\)", "Item.remove() deletes a node/property"),
    _r("node-set", r"\.\s*set\s*\(\s*[\"']", "Node.set(name, value) writes a property"),
    _r("sling-write", r"(?<!PredicateGroup)\.\s*(create|delete\w*|move|copy|clone|touch|revert|invalidate)\s*\(",
       "create/delete/move/copy-style write call"),
    _r("create-x", r"\.\s*create(Page|Asset|Revision|Rendition|Folder|Node|Resource|Tag|Version|LiveCopy|"
       r"Blueprint|User|Group|SystemUser|Package|Asset\w*|Workflow\w*)\s*\(", "creates content"),
    _r("security-write", r"\.\s*(addMember|removeMember|setPassword|changePassword|disable|setPolicy|removePolicy|"
       r"addAccessControlEntry|removeAccessControlEntry|addEntry)\s*\(", "user/ACL modification"),
    _r("writable-types", _W + r"(ModifiableValueMap|PersistableValueMap|PersistenceException)\b",
       "writable ValueMap / persistence API"),
    _r("replication", _W + r"(Replicator|ReplicationOptions|ReplicationActionType|Distributor|DistributionRequest|"
       r"replicate)\b", "replication/distribution (publishes content)"),
    _r("workflow-jobs", _W + r"(WorkflowSession|startWorkflow|WorkflowLauncher|JobManager|addJob|Scheduler|"
       r"EventAdmin|postEvent|sendEvent)\b", "starts workflows/jobs/events"),
    _r("packages", _W + r"(JcrPackageManager|PackageManager|Packaging|JcrPackage)\b", "content package operations"),
    _r("workspace", _W + r"(getWorkspace|workspace|VersionManager|LockManager|ObservationManager)\b",
       "Workspace-level operations persist immediately"),
    # --- privilege escalation ----------------------------------------------
    _r("privileged", _W + r"(ResourceResolverFactory|getServiceResourceResolver|getAdministrative\w*|"
       r"loginAdministrative|SlingRepository|impersonate|ConfigurationAdmin|bundleContext|BundleContext|"
       r"getBundleContext|UserManager|AccessControlManager)\b", "privileged service or session"),
    # --- escape hatches that could hide any of the above --------------------
    _r("dynamic-call", r"\.\s*[\"'$(]", "dynamic method/property name (obj.\"$name\"())"),
    _r("method-pointer", r"\.&", "method pointer (.&)"),
    _r("metaprogramming", _W + r"(metaClass|getMetaClass|setMetaClass|ExpandoMetaClass|invokeMethod|"
       r"methodMissing|propertyMissing|InvokerHelper|DefaultGroovyMethods|GroovySystem|MetaClassRegistry)\b",
       "metaprogramming"),
    _r("reflection", _W + r"(forName|getDeclared\w*|getMethod|getMethods|setAccessible|getClassLoader|loadClass|"
       r"ClassLoader|newInstance|MethodHandles|java\.lang\.reflect)\b", "reflection / class loading"),
    _r("eval", _W + r"(GroovyShell|GroovyClassLoader|Eval|evaluate|ScriptEngine\w*|CompilerConfiguration|"
       r"run\s*\(\s*new\s+File)\b", "evaluates other code"),
    _r("static-compile", r"@\s*(groovy\.transform\.)?(CompileStatic|TypeChecked|Grab\w*|GrabConfig)\b",
       "@CompileStatic/@Grab bypass the runtime guard or pull in code"),
    _r("guard-tamper", _W + r"(binding\s*\.\s*(setVariable|variables)|setBinding|__gr_)", "tampering with the read-only guard"),
    # --- outside AEM ---------------------------------------------------------
    _r("os", _W + r"(ProcessBuilder|Runtime|execute\s*\(|System\s*\.\s*(exit|setProperty|getenv))",
       "runs OS commands / touches the JVM"),
    _r("filesystem", _W + r"(File|FileWriter|FileOutputStream|FileInputStream|RandomAccessFile|Files|Paths|Path|"
       r"java\.io\.File\w*|java\.nio\.file)\b", "local filesystem access"),
    _r("network", _W + r"(URL|URI|URLConnection|HttpURLConnection|HttpClient\w*|Socket\w*|toURL|java\.net)\b",
       "network access"),
]


def _blank(text: str) -> str:
    return re.sub(r"[^\n]", " ", text)


def strip_comments_and_strings(source: str) -> str:
    """Same length and line layout as `source`; comments and string contents
    become spaces (quote characters are kept), `${...}` in GStrings stays."""
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        if source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end == -1 else end
            out.append(_blank(source[i:end]))
            i = end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = n if end == -1 else end + 2
            out.append(_blank(source[i:end]))
            i = end
        elif c in "\"'":
            quote = source[i:i + 3] if source[i:i + 3] in ('"""', "'''") else c
            interpolate = quote[0] == '"'
            out.append(quote)
            i += len(quote)
            while i < n and not source.startswith(quote, i):
                if source[i] == "\\" and i + 1 < n:
                    out.append(_blank(source[i:i + 2]))
                    i += 2
                elif interpolate and source.startswith("${", i):
                    depth, j = 0, i + 1
                    while j < n:
                        if source[j] == "{":
                            depth += 1
                        elif source[j] == "}":
                            depth -= 1
                            if depth == 0:
                                break
                        j += 1
                    out.append(" " + source[i + 1:j + 1])  # keep the code inside ${ }
                    i = j + 1
                elif len(quote) == 1 and source[i] == "\n":
                    break  # unterminated single-line string: stop blanking at end of line
                else:
                    out.append(_blank(source[i]))
                    i += 1
            if source.startswith(quote, i):
                out.append(quote)
                i += len(quote)
        else:
            out.append(c)
            i += 1
    return "".join(out)


def check(source: str) -> list[Violation]:
    code = strip_comments_and_strings(source)
    original_lines = source.splitlines()
    violations: list[Violation] = []
    seen: set[tuple[int, str]] = set()
    for rule in RULES:
        for match in rule.pattern.finditer(code):
            line = code.count("\n", 0, match.start()) + 1
            if (line, rule.id) in seen:
                continue
            seen.add((line, rule.id))
            snippet = original_lines[line - 1].strip() if line <= len(original_lines) else ""
            violations.append(Violation(line, rule.id, f"{rule.message} — `{match.group(0).strip()}`", snippet[:160]))
    return sorted(violations, key=lambda v: (v.line, v.rule))


GUARD = GUARD_MARKER + '''
def __gr_guard = { ->
    def __gr_block = { Object target, List<String> names ->
        if (target == null) return
        try {
            def __gr_original = org.codehaus.groovy.runtime.InvokerHelper.getMetaClass(target.getClass())
            def __gr_emc = new ExpandoMetaClass(target.getClass(), false, true)
            __gr_emc.invokeMethod = { String name, Object args ->
                if (name in names) {
                    throw new SecurityException("Groovy Runner read-only mode: ${name}() is blocked")
                }
                __gr_original.invokeMethod(delegate, name, args)
            }
            __gr_emc.initialize()
            org.codehaus.groovy.runtime.DefaultGroovyMethods.setMetaClass(target, __gr_emc)
        } catch (Throwable t) {
            println "[groovy-runner] read-only guard could not wrap ${target.getClass().name}: ${t}"
        }
    }
    def __gr_vars = this.binding.variables
    __gr_block(__gr_vars.session, ["save", "move", "importXML", "impersonate", "getWorkspace", "removeItem"])
    __gr_block(__gr_vars.resourceResolver, ["commit", "create", "delete", "move", "copy"])
    __gr_block(__gr_vars.pageManager, ["create", "delete", "move", "copy", "restore", "restoreTree", "createRevision", "touch"])
}
__gr_guard()
// ===== end read-only guard =====
'''

_IMPORT_RE = re.compile(r"^\s*(import\s+[\w.*]+(\s+as\s+\w+)?\s*;?|package\s+[\w.]+\s*;?)\s*$")


def _insert_after_imports(source: str, block: str) -> str:
    code_lines = strip_comments_and_strings(source).splitlines(keepends=True)
    lines = source.splitlines(keepends=True)
    insert_at = 0
    for idx, line in enumerate(code_lines):
        if _IMPORT_RE.match(line):
            insert_at = idx + 1
        elif line.strip():
            break
    if insert_at and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"
    return "".join(lines[:insert_at]) + block + "".join(lines[insert_at:])


def prepare(script: str) -> str:
    """Validate, then return the exact text to send (guard injected).
    Raises ReadOnlyViolationError instead of returning a script that fails."""
    violations = check(script)
    if violations:
        raise ReadOnlyViolationError(violations)
    return _insert_after_imports(script, GUARD)
