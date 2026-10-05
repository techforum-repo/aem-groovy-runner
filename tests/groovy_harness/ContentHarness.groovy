// Pages + assets fixture for page-report and assets-by-type.
// Usage: ContentHarness <script>
import com.day.cq.wcm.api.*
import org.apache.sling.api.resource.*

class Res implements Resource {
    String path; Map props = [:]; Map<String, Res> children = [:]; String type = "sling:Folder"; Object page
    String getPath() { path }
    String getName() { path.tokenize('/').last() }
    String getResourceType() { type }
    Iterator<Resource> listChildren() { children.values().iterator() }
    Resource getChild(String rel) { def cur = this; for (seg in rel.split("/")) { cur = cur?.children?.get(seg) }; cur }
    def <T> T adaptTo(Class<T> t) { t == ValueMap ? (T) new SimpleValueMap(props) : (t == Page ? (T) page : null) }
    ValueMap getValueMap() { new SimpleValueMap(props) }
}
class FakePage implements Page {
    String path; Res content; List<FakePage> kids = []
    String getPath() { path }
    String getName() { path.tokenize('/').last() }
    String getTitle() { content?.props?.get("jcr:title") }
    Resource getContentResource() { content }
    Iterator<Page> listChildren() { kids.iterator() }
}
def cal = { String s -> def c = Calendar.instance; c.time = new java.text.SimpleDateFormat("yyyy-MM-dd").parse(s); c }
def page = { String p, Map props -> new FakePage(path: p, content: new Res(path: p + "/jcr:content", props: props)) }
def root = page("/content/site", ["jcr:title": "Site", "cq:template": "/conf/s/home"])
def en = page("/content/site/en", ["jcr:title": "EN", "cq:template": "/apps/s/legacy",
        "cq:lastReplicated": cal("2025-01-01"), "cq:lastReplicationAction": "Activate", "cq:lastModified": cal("2025-02-01")])
def prod = page("/content/site/en/products", ["jcr:title": "Products", "cq:template": "/conf/s/product"])
def arch = page("/content/site/en/archive", ["cq:template": "/conf/s/product"])
def deep = page("/content/site/en/products/x", ["jcr:title": "X", "cq:template": "/conf/s/product"])
root.kids = [en]; en.kids = [prod, arch]; prod.kids = [deep]
def other = page("/content/other", ["jcr:title": "Other", "cq:template": "/conf/o/home"])
def pages = [root, en, prod, arch, deep, other].collectEntries { [(it.path): it] }
// /content is a plain folder holding two sites and the DAM (not a page)
def contentFolder = new Res(path: "/content", children: [
        site : new Res(path: "/content/site", page: root, children: [en: new Res(path: "/content/site/en", page: en)]),
        other: new Res(path: "/content/other", page: other),
        dam  : new Res(path: "/content/dam")])

def asset = { String p, String fmt, long size ->
    def meta = new Res(path: p + "/jcr:content/metadata", props: ["dc:format": fmt, "dam:size": size])
    def jc = new Res(path: p + "/jcr:content", props: [:], children: [metadata: meta])
    new Res(path: p, props: ["jcr:created": cal("2024-01-01")], children: ["jcr:content": jc])
}
def assets = [
    asset("/content/dam/b/a/one.pdf", "application/pdf", 2_097_152L),
    asset("/content/dam/b/a/two.pdf", "application/pdf", 1_048_576L),
    asset("/content/dam/b/img/p.png", "image/png", 1_048_576L),
    asset("/content/dam/b/img/j.jpg", "image/jpeg", 524_288L),
    asset("/content/dam/b/old/x.pdf", "application/pdf", 10L),
    asset("/content/dam/other/z.pdf", "application/pdf", 10L),
    asset("/content/dam/b/loose.pdf", "application/pdf", 1_048_576L),  // directly in the root folder
]
assets.each { it.type = "dam:Asset" }
def damB = new Res(path: "/content/dam/b", children: [
        a  : new Res(path: "/content/dam/b/a", children: [
                sub: new Res(path: "/content/dam/b/a/sub"),
                "one.pdf": assets.find { it.path == "/content/dam/b/a/one.pdf" }]),
        img: new Res(path: "/content/dam/b/img"),
        old: new Res(path: "/content/dam/b/old"), "loose.pdf": assets.find { it.path.endsWith("loose.pdf") },
        "jcr:content": new Res(path: "/content/dam/b/jcr:content", type: "nt:unstructured")])
def resources = ["/content/site": contentFolder.children.site, "/content": contentFolder, "/content/dam/b": damB, "/content/dam/it's": new Res(path: "/content/dam/it's")]

// Minimal JCR-SQL2 interpreter for the shapes the script generates.
def findResources = { String sql, String lang ->
    println "SQL=" + sql
    assert lang == "JCR-SQL2" && sql.contains("OPTION(TRAVERSAL FAIL)")
    def under = (sql =~ /(?<!NOT )ISDESCENDANTNODE\(a, '([^']*)'\)/).collect { it[1] }
    def notUnder = (sql =~ /NOT ISDESCENDANTNODE\(a, '([^']*)'\)/).collect { it[1] }
    def exact = (sql =~ /dc:format\] = '([^']*)'/).collect { it[1] }
    def like = (sql =~ /dc:format\] LIKE '([^']*)%'/).collect { it[1] }
    assets.findAll { a ->
        def fmt = a.getChild("jcr:content/metadata").props["dc:format"]
        under.every { a.path.startsWith(it + "/") } && !notUnder.any { a.path.startsWith(it + "/") } &&
            ((!exact && !like) || fmt in exact || like.any { fmt.startsWith(it) })
    }.iterator()
}
def pm = [getPage: { String p -> pages[p] }] as PageManager
def resolver = [getResource: { String p -> resources[p] ?: pages[p]?.content }, findResources: findResources,
                adaptTo: { Class c -> c == PageManager ? pm : null }]
new GroovyShell(new Binding(resourceResolver: resolver)).evaluate(new File(args[0]).text)
