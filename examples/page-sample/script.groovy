import com.day.cq.wcm.api.Page
import groovy.json.JsonOutput
import groovy.json.JsonSlurper

// Sample script skeleton: lists every page under a root (the root page included).
// Copy this folder to scripts/<your-script-id>/ and change the report logic.
//
// Batch works with it without any batching code here. The three requirements:
//   1. a path list input with "iterate": true      -> rootPath    (manifest.json)
//   2. an excluded-paths input                     -> excludedPaths (manifest.json)
//   3. skip the WHOLE subtree under each excluded path  -> isExcluded below
// With Batch on, the app calls this script once per part, each time with
// ordinary inputs: rootPath = the part (e.g. one site), and sometimes extra
// excludedPaths (to get only a page's own row, without its child pages).

// INPUT: injected by the app from manifest.json (base64 JSON, so any path is safe).
def CONFIG = new JsonSlurper().parseText(new String("__CONFIG_B64__".decodeBase64(), "UTF-8"))

def rootPath = CONFIG.rootPath as String                                  // requirement 1: ONE path per run
def excludedPaths = (CONFIG.excludedPaths ?: []) as List<String>          // requirement 2

// Requirement 3: an excluded path removes everything under it, not just itself.
def isExcluded = { String path -> excludedPaths.any { ex -> path == ex || path.startsWith(ex + "/") } }

// OUTPUT: one JSON document between these markers; the app ignores any other output.
def emit = { Map payload ->
    println "===GROOVY_RUNNER_JSON_START==="
    println JsonOutput.toJson(payload)
    println "===GROOVY_RUNNER_JSON_END==="
}

def root = resourceResolver.getResource(rootPath)
if (!root) {
    emit([error: "Path not found: ${rootPath}".toString()])   // {error: ...} fails the run with this message
    return
}

def rows = []
def visit
visit = { Page page ->
    if (isExcluded(page.path)) return                         // skip the page and its subtree
    def props = page.contentResource?.valueMap
    // ---- your report logic: one map per row; the keys become the Excel columns ----
    rows << [
        "Path"    : page.path,
        "Title"   : page.title ?: page.name,
        "Template": props?.get("cq:template", "") ?: "",
    ]
    page.listChildren().each { Page child -> visit(child) }
}

// The root can be a page (one site) or a folder such as /content (every site under it).
def rootPage = root.adaptTo(Page)
if (rootPage) {
    visit(rootPage)
} else {
    root.listChildren().each { child -> def page = child.adaptTo(Page); if (page) visit(page) }
}

// "rows" become the result file; any other keys show as run details.
emit([rootPath: rootPath, rowCount: rows.size(), rows: rows])
