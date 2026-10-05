import com.day.cq.wcm.api.Page
import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import org.apache.sling.api.resource.Resource

// Built-in batch discovery for AEM Groovy Runner (read-only).
// Lists the roots under a path so a big report can run as one request per
// root: child pages ("page"), or child folders ("folder", e.g. DAM folders).

def CONFIG = new JsonSlurper().parseText(new String("__CONFIG_B64__".decodeBase64(), "UTF-8"))

def root = CONFIG.root as String
def kind = CONFIG.kind as String               // "page" | "folder"
def excludes = (CONFIG.excludes ?: []) as List<String>
def levels = Math.max(1, (CONFIG.levels ?: 1) as int)

def emit = { Map payload ->
    println "===GROOVY_RUNNER_JSON_START==="
    println JsonOutput.toJson(payload)
    println "===GROOVY_RUNNER_JSON_END==="
}

def start = resourceResolver.getResource(root)
if (!start) {
    emit([error: "Path not found: ${root}".toString(), children: []])
    return
}

def folderTypes = ["sling:Folder", "sling:OrderedFolder", "nt:folder"]
def isExcluded = { String path -> excludes.any { ex -> path == ex || path.startsWith(ex + "/") } }
def matches = { Resource r ->
    kind == "page" ? r.adaptTo(Page) != null : folderTypes.contains(r.resourceType)
}

def found = []
def skipped = []
def looseItems = 0  // non-matching content directly under the root, not covered by any batch
def walk
walk = { Resource parent, int depth ->
    parent.listChildren().each { Resource child ->
        def name = child.name
        if (name.startsWith("jcr:") || name.startsWith("rep:") || name.startsWith("cq:")) return
        if (isExcluded(child.path)) {
            skipped << child.path
            return
        }
        if (!matches(child)) {
            if (depth == 1 && child.resourceType == "dam:Asset") looseItems++
            return
        }
        if (depth < levels) {
            walk(child, depth + 1)
        } else {
            found << child.path
        }
    }
}
walk(start, 1)

emit([root: root, kind: kind, levels: levels, children: found.sort(), skipped: skipped.sort(), looseItems: looseItems])
