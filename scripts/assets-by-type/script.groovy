import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import java.text.SimpleDateFormat
import org.apache.sling.api.resource.Resource
import org.apache.sling.api.resource.ValueMap

// Assets by type, built for very large DAMs:
// - ONE indexed JCR-SQL2 query, streamed lazily (findResources), instead of
//   QueryBuilder p.limit=-1, which loads every hit up front.
// - The type filter runs inside the query (dc:format, which the DAM index
//   covers), so non-matching assets are never loaded.
// - OPTION(TRAVERSAL FAIL): if no index can serve the query, AEM refuses at
//   once instead of crawling the repository.
// - Summary mode returns counts/sizes per type (optionally per folder), so the
//   result stays small no matter how many assets match.

// INPUT: injected by AEM Groovy Runner from manifest.json inputs.
def CONFIG = new JsonSlurper().parseText(new String("__CONFIG_B64__".decodeBase64(), "UTF-8"))

def rootPath = CONFIG.rootPath as String
def formats = ((CONFIG.formats ?: []) as List<String>).collect { it.trim() }.findAll { it }
def excludedFolders = (CONFIG.excludedFolders ?: []) as List<String>
def mode = (CONFIG.mode ?: "summary") as String
def groupByFolderLevels = (CONFIG.groupByFolderLevels ?: 0) as int
def maxAssets = (CONFIG.maxAssets ?: 0) as int  // list mode only; 0 = no limit
// One query per top-level folder: keeps each query under Oak's per-query read
// limit (commonly 100,000 nodes) when running on a whole DAM.
def queryPerFolder = CONFIG.queryPerFolder as boolean

def emit = { Map payload ->
    println "===GROOVY_RUNNER_JSON_START==="
    println JsonOutput.toJson(payload)
    println "===GROOVY_RUNNER_JSON_END==="
}

if (!resourceResolver.getResource(rootPath)) {
    emit([error: "Path not found: ${rootPath}".toString(), rows: []])
    return
}

def q = { String s -> "'" + s.replace("'", "''") + "'" }  // JCR-SQL2 string literal

def isExcludedPath = { String path -> excludedFolders.any { ex -> path == ex || path.startsWith(ex + "/") } }
def matchesFormat = { String fmt ->
    !formats || formats.any { f -> f.endsWith("*") ? fmt.startsWith(f[0..-2]) : fmt == f }
}

def filters = []
if (formats) {
    filters << "(" + formats.collect { f ->
        f.endsWith("*") ? "a.[jcr:content/metadata/dc:format] LIKE ${q(f[0..-2] + '%')}"
                        : "a.[jcr:content/metadata/dc:format] = ${q(f)}"
    }.join(" OR ") + ")"
}
excludedFolders.each { ex -> filters << "NOT ISDESCENDANTNODE(a, ${q(ex)})" }
def sqlUnder = { String scope ->
    def where = ["ISDESCENDANTNODE(a, ${q(scope)})"] + filters
    "SELECT a.[jcr:path] FROM [dam:Asset] AS a WHERE ${where.join(' AND ')} OPTION(TRAVERSAL FAIL)".toString()
}

def dateFormat = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss")
def formatDate = { v -> v instanceof Calendar ? dateFormat.format(v.time) : (v ?: "").toString() }

def status = { ValueMap vm ->
    if (!vm?.get("cq:lastReplicated")) return "Never Published"
    def action = vm.get("cq:lastReplicationAction", "")
    if (action == "Deactivate") return "Unpublished"
    def modified = vm.get("cq:lastModified", Calendar) ?: vm.get("jcr:lastModified", Calendar)
    def replicated = vm.get("cq:lastReplicated", Calendar)
    if (action == "Activate") return (modified && replicated && modified.after(replicated)) ? "Modified" : "Published"
    return "Unknown"
}

def rootDepth = rootPath.tokenize("/").size()
def folderKey = { String assetPath ->
    def parts = assetPath.tokenize("/")
    def keep = Math.min(rootDepth + groupByFolderLevels, parts.size() - 1)
    "/" + parts[0..<keep].join("/")
}

def rows = []
def summary = [:]  // key -> [count, bytes]
def matched = 0
def truncated = false

def handle = { Resource asset ->
    matched++
    def content = asset.getChild("jcr:content")?.adaptTo(ValueMap)
    def meta = asset.getChild("jcr:content/metadata")?.adaptTo(ValueMap)
    def format = (meta?.get("dc:format", "") ?: meta?.get("dam:MIMEtype", "") ?: "(unknown)").toString()
    def size = (meta?.get("dam:size", 0L) ?: 0L) as long

    if (mode == "list") {
        if (maxAssets > 0 && rows.size() >= maxAssets) {
            truncated = true
            return  // keep counting matches; just stop adding rows
        }
        def assetVm = asset.adaptTo(ValueMap)
        rows << [
            "Asset Path"        : asset.path,
            "Title"             : (meta?.get("dc:title", "") ?: content?.get("jcr:title", "") ?: asset.name).toString(),
            "Format"            : format,
            "Size (bytes)"      : size,
            "Status"            : status(content),
            "Created"           : formatDate(assetVm?.get("jcr:created")),
            "Created By"        : assetVm?.get("jcr:createdBy", "") ?: "",
            "Last Modified"     : formatDate(content?.get("jcr:lastModified")),
            "Last Modified By"  : content?.get("jcr:lastModifiedBy", "") ?: "",
            "Last Published"    : formatDate(content?.get("cq:lastReplicated")),
            "Last Published By" : content?.get("cq:lastReplicatedBy", "") ?: "",
            "Folder"            : asset.path.substring(0, asset.path.lastIndexOf("/"))
        ]
    } else {
        def key = groupByFolderLevels > 0 ? folderKey(asset.path) + "\u0000" + format : format
        def entry = summary.get(key) ?: [0L, 0L]
        summary[key] = [entry[0] + 1, entry[1] + size]
    }
}

def runQuery = { String scope ->
    def results = resourceResolver.findResources(sqlUnder(scope), "JCR-SQL2")
    while (results.hasNext()) {
        handle(results.next())
    }
}

def queriesRun = 0
if (queryPerFolder) {
    // Assets sitting directly in the root are read without a query (usually few);
    // every subfolder gets its own query.
    resourceResolver.getResource(rootPath).listChildren().each { Resource child ->
        if (isExcludedPath(child.path) || child.name.startsWith("jcr:") || child.name.startsWith("rep:")) return
        if (child.resourceType == "dam:Asset") {
            def fmt = (child.getChild("jcr:content/metadata")?.adaptTo(ValueMap)?.get("dc:format", "") ?: "").toString()
            if (matchesFormat(fmt)) handle(child)
        } else {
            runQuery(child.path)
            queriesRun++
        }
    }
} else {
    runQuery(rootPath)
    queriesRun = 1
}

if (mode != "list") {
    rows = summary.collect { key, v ->
        def parts = key.split("\u0000", 2)
        def row = groupByFolderLevels > 0 ? ["Folder": parts[0], "Format": parts[1]] : ["Format": parts[0]]
        row + ["Assets": v[0], "Total Size (MB)": Math.round(v[1] / 1048576.0 * 100) / 100.0]
    }.sort { a, b -> (a["Folder"] ?: "") <=> (b["Folder"] ?: "") ?: b["Assets"] <=> a["Assets"] }
}

println "Matched assets: ${matched}"
emit([rootPath: rootPath, mode: mode, matchedAssets: matched, queriesRun: queriesRun, truncatedAt: truncated ? maxAssets : null,
      rowCount: rows.size(), rows: rows])
