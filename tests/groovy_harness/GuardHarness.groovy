// Usage: GuardHarness <script> <groovy|java>
// Runs a script with stub `session`, `resourceResolver` and `pageManager`
// bound like the Groovy Console does, to test the injected read-only guard.
class StubSession {
    List calls = []
    void save() { calls << "save" }
    String getUserID() { calls << "getUserID"; "tech" }
}
class StubResolver {
    List calls = []
    void commit() { calls << "commit" }
    Object create(Object parent, String name, Map props) { calls << "create"; null }
    Object getResource(String path) { calls << "getResource"; path }
}
class StubPageManager {
    List calls = []
    Object getPage(String p) { calls << "getPage"; p }
    void delete(Object page, boolean shallow) { calls << "delete" }
}

def java = args.length > 1 && args[1] == "java"
def stubs = java ? [new JavaStubs.Session(), new JavaStubs.Resolver(), new JavaStubs.PageManager()]
                 : [new StubSession(), new StubResolver(), new StubPageManager()]
def binding = new Binding(session: stubs[0], resourceResolver: stubs[1], pageManager: stubs[2])
def outcome
try {
    new GroovyShell(binding).evaluate(new File(args[0]).text)
    outcome = "completed"
} catch (SecurityException e) {
    outcome = "blocked: " + e.message
}
println "OUTCOME=" + outcome
println "CALLS=" + (java ? JavaStubs.CALLS : stubs.collectMany { it.calls })
