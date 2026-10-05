import com.day.cq.wcm.api.Page
import com.day.cq.wcm.api.PageManager
import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import java.text.SimpleDateFormat

// Page report: every page under a root with status, dates, template, depth.
// Based on the team's TSV page report; same columns and tree walk, but rows
// are printed as JSON for AEM Groovy Runner and the root comes from the form.

// INPUT: injected by AEM Groovy Runner from manifest.json inputs.
def CONFIG = new JsonSlurper().parseText(new String("__CONFIG_B64__".decodeBase64(), "UTF-8"))

def rootPath = CONFIG.rootPath as String
def excludedPaths = (CONFIG.excludedPaths ?: []) as List<String>
def templates = (CONFIG.templates ?: []) as List<String>
def maxDepth = (CONFIG.maxDepth ?: 0) as int  // levels below the root; 0 = no limit
def includeRoot = CONFIG.includeRoot as boolean

def emit = { Map payload ->
    println "===GROOVY_RUNNER_JSON_START==="
    println JsonOutput.toJson(payload)
    println "===GROOVY_RUNNER_JSON_END==="
}

def dateFormat = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss")
def pageManager = resourceResolver.adaptTo(PageManager)

def formatDate = { value ->
    if (value == null) return ""
    if (value instanceof Calendar) return dateFormat.format(value.time)
    return value.toString()
}

def getStatus = { content ->
    def action = content?.get("cq:lastReplicationAction", String)
    def lastModified = content?.get("cq:lastModified", Calendar)
    def lastReplicated = content?.get("cq:lastReplicated", Calendar)

    if (lastReplicated == null) {
        return "Never Published"
    }
    if (action == "Deactivate") {
        return "Unpublished"
    }
    if (action == "Activate") {
        if (lastModified != null && lastModified.after(lastReplicated)) {
            return "Modified"
        }
        return "Published"
    }
    return "Unknown"
}

def getDepth = { path -> path.tokenize("/").size() }

def isExcluded = { String path -> excludedPaths.any { ex -> path == ex || path.startsWith(ex + "/") } }

// The root can be a page, or a folder such as /content: then every page
// directly under it (each site root) is walked.
def rootPage = pageManager.getPage(rootPath)
def rootResource = resourceResolver.getResource(rootPath)
if (!rootPage && !rootResource) {
    emit([error: "Path not found: ${rootPath}".toString(), rows: []])
    return
}

def rootDepth = getDepth(rootPath)
def rows = []
def pagesVisited = 0
def skippedTemplate = 0

def exportPage
exportPage = { Page page ->
    if (page == null || isExcluded(page.path)) return
    def level = getDepth(page.path) - rootDepth
    pagesVisited++

    def content = page.contentResource?.valueMap
    def template = content?.get("cq:template", String)

    if (includeRoot || level > 0) {
        if (templates && !templates.contains(template ?: "")) {
            skippedTemplate++
        } else {
            rows << [
                "Path"              : page.path,
                "Title"             : page.title ?: page.name,
                "Status"            : getStatus(content),
                "Last Modified"     : formatDate(content?.get("cq:lastModified")),
                "Last Published"    : formatDate(content?.get("cq:lastReplicated")),
                "Last Modified By"  : content?.get("cq:lastModifiedBy", "") ?: "",
                "Last Published By" : content?.get("cq:lastReplicatedBy", "") ?: "",
                "Template"          : template ?: "",
                "Template Type"     : template?.startsWith("/conf") ? "Editable Template" : "Legacy Template",
                "Created"           : formatDate(content?.get("jcr:created")),
                "Created By"        : content?.get("jcr:createdBy", "") ?: "",
                "Depth"             : getDepth(page.path)
            ]
        }
    }

    if (maxDepth <= 0 || level < maxDepth) {
        page.listChildren().each { child -> exportPage(child) }
    }
}

def sitesWalked = 0
if (rootPage) {
    exportPage(rootPage)
    sitesWalked = 1
} else {
    rootResource.listChildren().each { child ->
        def page = child.adaptTo(Page)
        if (page) {
            sitesWalked++
            exportPage(page)
        }
    }
}
println "Total rows generated: ${rows.size()}"

emit([rootPath: rootPath, rootIsFolder: !rootPage, sitesWalked: sitesWalked, pagesVisited: pagesVisited,
      skippedTemplate: skippedTemplate, rowCount: rows.size(), rows: rows])
