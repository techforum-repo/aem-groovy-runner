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

def isExcluded = { String path -> excludes.any { ex -> path == ex || path.startsWith(ex + "/") } }
// Folder kind: any child that isn't an asset may hold assets (folder node types vary), so it's a part.
def isSystemName = { String name -> name.startsWith("jcr:") || name.startsWith("rep:") || name.startsWith("cq:") }
def matches = { Resource r ->
    kind == "page" ? r.adaptTo(Page) != null : (r.resourceType != "dam:Asset" && !isSystemName(r.name))
}

def found = []    // parts run with their whole subtree
def direct = []   // [path, exclude]: parts run for their OWN direct content only
def skipped = []
// The app never asks a script for "direct content only": it runs the script on
// the path with the listed children added to the script's own excluded paths.
// That way batching never loses what sits above the split level: assets
// directly in the entered/intermediate folders, or those pages' own rows. An
// intermediate folder/page with nothing further to split is simply run whole.
// Returns false for a folder/page below the entered path that has nothing to
// split further: the caller then runs it whole as one part.
def walk
walk = { Resource parent, int depth ->
    def kids = []
    def hasDirectAsset = false
    def skippedHere = []
    parent.listChildren().each { Resource child ->
        if (isSystemName(child.name)) return
        if (isExcluded(child.path)) {
            skippedHere << child.path
            return
        }
        if (matches(child)) kids << child
        else if (child.resourceType == "dam:Asset") hasDirectAsset = true
    }
    if (depth > 1 && kids.isEmpty()) return false
    skipped.addAll(skippedHere)
    def ownContent = kind == "page" ? parent.adaptTo(Page) != null : hasDirectAsset
    if (ownContent) direct << [path: parent.path, exclude: kids*.path.sort()]
    kids.each { Resource child ->
        if (!(depth < levels && walk(child, depth + 1))) found << child.path
    }
    return true
}
walk(start, 1)

emit([root: root, kind: kind, levels: levels, children: found.sort(), direct: direct.sort { it.path }, skipped: skipped.sort()])
