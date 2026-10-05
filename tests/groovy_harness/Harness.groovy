import org.apache.sling.api.resource.*
import com.day.cq.search.*
import com.day.cq.wcm.commons.ReferenceSearch

class FakeRes implements Resource {
    String path; String type = "dam:Asset"; Map<String, FakeRes> children = [:]; Map props = [:]
    String getPath() { path }
    Resource getChild(String rel) { def cur = this; for (seg in rel.split("/")) { cur = cur?.children?.get(seg) }; cur }
    def <T> T adaptTo(Class<T> t) { t == ValueMap ? (T) new SimpleValueMap(props) : null }
}
def cal = { String s -> def c = Calendar.instance; c.time = new java.text.SimpleDateFormat("yyyy-MM-dd").parse(s); c }
def mkAsset = { String p, String fmt, Map content ->
    def meta = new FakeRes(path: p + "/jcr:content/metadata", props: ["dc:format": fmt, "dc:title": "T " + p.tokenize('/').last()])
    def jc = new FakeRes(path: p + "/jcr:content", props: content, children: [metadata: meta])
    new FakeRes(path: p, props: ["jcr:created": cal("2024-01-02"), "jcr:createdBy": "admin"], children: ["jcr:content": jc])
}
def root = "/content/dam/acme/Inter Cardio"
def assets = [
    mkAsset("$root/a.pdf", "application/pdf", ["cq:lastReplicated": cal("2025-01-01"), "cq:lastReplicationAction": "Activate", "jcr:lastModified": cal("2024-12-01")]),
    mkAsset("$root/b.pdf", "application/pdf", [:]),
    mkAsset("$root/c.jpg", "image/jpeg", [:]),
    mkAsset("$root/archive/old.pdf", "application/pdf", [:]),
]
def mkPage = { String p, String template, Map extra = [:] ->
    def jc = new FakeRes(path: p + "/jcr:content", props: ["jcr:title": p.tokenize('/').last(), "cq:template": template] + extra)
    new FakeRes(path: p, type: "cq:Page", children: ["jcr:content": jc])
}
assets += [
    mkPage("/content/site/en", "/conf/t/home"),
    mkPage("/content/site/en/products", "/conf/t/product", ["cq:lastReplicated": cal("2025-01-01"), "cq:lastReplicationAction": "Activate"]),
    mkPage("/content/site/en/old/x", "/conf/t/product"),
]
def resources = [(root): new FakeRes(path: root), "/content/site/en": assets.find { it.path == "/content/site/en" },
                 "/content/site/en/page1/jcr:content": new FakeRes(path: "/content/site/en/page1/jcr:content",
                     props: ["cq:lastReplicated": cal("2025-02-01"), "cq:lastReplicationAction": "Activate", "cq:lastModified": cal("2025-03-01"), "cq:lastModifiedBy": "jane"])]
ReferenceSearch.handler = { String p ->
    if (p.endsWith("a.pdf")) return ["/content/site/en/page1": 1, "/content/dam/other": 1]
    if (p.contains("%20") && p.endsWith("b.pdf")) return ["/content/site/en/gone": 1]
    return [:]
}
def resolver = [getResource: { String p -> resources[p] }, adaptTo: { Class c -> null }]
def queryBuilder = [createQuery: { g, s -> [getResult: { -> [hits: assets.findAll { it.type == g.map.type && it.path.startsWith(g.map.path) }.collect { a -> [getResource: { -> a }, getPath: { -> a.path }] }] }] }] as QueryBuilder

def binding = new Binding(resourceResolver: resolver, getService: { Class c -> queryBuilder })
new GroovyShell(binding).evaluate(new File(args[0]).text)
