// cq:AuditEvent fixture for audit-events: page, DAM and replication logs.
// Usage: AuditHarness <script> [fail]   ("fail" = the query throws, like AEM's read limit)
import org.apache.sling.api.resource.*

class Ev implements Resource {
    String path; Map props = [:]
    String getPath() { path }
    Resource getChild(String rel) { null }
    def <T> T adaptTo(Class<T> t) { t == ValueMap ? (T) new SimpleValueMap(props) : null }
}
def cal = { String s -> def c = Calendar.instance; c.time = new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm").parse(s); c }
def site = "/content/acme/en-us/products"
def dam = "/content/dam/acme/brochures"
def n = 0
def ev = { String category, String path, String type, String user, String time, String userKey = "cq:userid" ->
    new Ev(path: "/var/audit/${category}${path}/${n++}",
           props: ["cq:category": category, "cq:path": path, "cq:type": type, (userKey): user, "cq:time": cal(time)])
}
def PAGE = "com.day.cq.wcm.core.page", DAM = "com.day.cq.dam", REPL = "com.day.cq.replication"
def events = [
    ev(PAGE, "$site/stents/stent-a", "PageModified", "jane", "2026-09-01 10:00"),
    ev(REPL, "$site/stents/stent-a", "Activate", "john", "2026-09-02 11:00"),
    ev(PAGE, "$site/stents/stent-a", "VersionCreated", "jane", "2026-08-01 09:00"),   // not a default type
    ev(PAGE, "$site/stents", "PageModified", "jane", "2026-09-03 12:00"),             // level 1
    ev(PAGE, "$site/archive/old", "PageDeleted", "amy", "2026-09-05 08:00"),          // a deleted page
    ev(PAGE, site, "PageModified", "root.user", "2026-09-06 08:00"),                  // the entered path itself
    ev(PAGE, "/content/acme/en-us/products_old/x", "PageModified", "amy", "2026-09-07 08:00"),  // LIKE '_' match
    ev(DAM, "$dam/a.pdf", "METADATA_UPDATED", "jane", "2026-09-08 09:00"),
    ev(DAM, "$dam/a.pdf", "ASSET_VIEWED", "bob", "2026-09-08 09:30"),                 // noise, not a default type
    ev(DAM, "$dam/a.pdf", "RENDITION_UPDATED", "workflow-process-service", "2026-09-08 09:01"),  // noise
    ev(REPL, "$dam/a.pdf", "Activate", "john", "2026-09-09 10:00"),
    ev(DAM, "$dam/b.pdf", "ASSET_CREATED", "legacy.user", "2026-09-10 10:00", "cq:userId"),  // older property name
]
def findResources = { String sql, String lang ->
    println "SQL=" + sql
    assert lang == "JCR-SQL2"
    if (args.length > 1 && args[1] == "fail") {
        return [hasNext: { -> throw new RuntimeException("The query read or traversed more than 100000 nodes.") },
                next: { -> null }] as Iterator
    }
    events.iterator()  // the script's own exact checks must do the filtering
}
def resolver = [getResource: { String p -> p == "/var/audit" ? new Ev(path: p) : null }, findResources: findResources]
new GroovyShell(new Binding(resourceResolver: resolver)).evaluate(new File(args[0]).text)
