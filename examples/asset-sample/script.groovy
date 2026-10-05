import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import org.apache.sling.api.resource.Resource
import org.apache.sling.api.resource.ValueMap

// Sample script skeleton: lists every DAM asset under a folder.
// Copy this folder to scripts/<your-script-id>/ and change the report logic.
//
// Batch works with it without any batching code here. The three requirements:
//   1. a path list input with "iterate": true      -> rootPath    (manifest.json)
//   2. an excluded-paths input                     -> excludedPaths (manifest.json)
//   3. skip the WHOLE subtree under each excluded path  -> isExcluded below
// With Batch on, the app calls this script once per part, each time with
// ordinary inputs: rootPath = the part, and sometimes extra excludedPaths
// (to cover only the assets sitting directly in a folder).

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
def walk
walk = { Resource folder ->
    folder.listChildren().each { Resource child ->
        if (child.name.startsWith("jcr:") || isExcluded(child.path)) return   // skip the subtree
        if (child.resourceType == "dam:Asset") {
            def meta = child.getChild("jcr:content/metadata")?.adaptTo(ValueMap)
            // ---- your report logic: one map per row; the keys become the Excel columns ----
            rows << [
                "Asset Path": child.path,
                "Title"     : (meta?.get("dc:title", "") ?: child.name).toString(),
                "Format"    : (meta?.get("dc:format", "") ?: "").toString(),
            ]
        } else {
            walk(child)
        }
    }
}
walk(root)

// "rows" become the result file; any other keys show as run details.
emit([rootPath: rootPath, rowCount: rows.size(), rows: rows])
