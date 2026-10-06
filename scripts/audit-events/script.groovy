import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import java.text.SimpleDateFormat
import org.apache.sling.api.resource.Resource
import org.apache.sling.api.resource.ValueMap

// Audit events (cq:AuditEvent) for pages AND assets under a path: who created,
// modified, moved, deleted or published what, and when. Converted from the team's
// console page-audit script (same query shape: cq:AuditEvent under /var/audit,
// filtered on cq:path), extended to the three audit logs AEM writes:
//   /var/audit/com.day.cq.wcm.core.page   page events    (PageModified, ...)
//   /var/audit/com.day.cq.dam             asset events   (METADATA_UPDATED, ...)
//   /var/audit/com.day.cq.replication     publish events (Activate, Deactivate, ...) for pages and assets
// Each event names its log in cq:category. Read-only: resourceResolver.findResources
// instead of session.workspace.queryManager.

// INPUT: injected by AEM Groovy Runner from manifest.json inputs.
def CONFIG = new JsonSlurper().parseText(new String("__CONFIG_B64__".decodeBase64(), "UTF-8"))

def basePath = CONFIG.basePath as String
def auditRoot = (CONFIG.auditRoot ?: "/var/audit") as String
def excludedPaths = (CONFIG.excludedPaths ?: []) as List<String>
// Exact event types (case-insensitive); empty = every type.
def eventTypes = ((CONFIG.eventTypes ?: []) as List<String>).collect { it.trim().toLowerCase() }.findAll { it } as Set
def users = ((CONFIG.users ?: []) as List<String>).collect { it.trim().toLowerCase() }.findAll { it } as Set
def levels = (CONFIG.levels ?: 0) as int          // only items exactly N levels below basePath; 0 = any level
def sinceDays = (CONFIG.sinceDays ?: 0) as int    // only events from the last N days; 0 = all
// Scope (manifest "scope" options, in order): the path and everything under it | only the path
// itself (one page or asset) | only what's under it.
def scopeOptions = ["This path and everything under it", "Only this path itself (a single page or asset)",
                    "Only what's under this path"]
def scope = Math.max(0, scopeOptions.indexOf((CONFIG.scope ?: scopeOptions[0]) as String))
def includeRoot = scope != 2
def includeBelow = scope != 1

// cq:category -> the log shown in the report; which logs to include
def LOGS = ["com.day.cq.wcm.core.page": "Page", "com.day.cq.dam": "Asset", "com.day.cq.replication": "Replication"]
def wanted = [Page: CONFIG.pageEvents as boolean, Asset: CONFIG.assetEvents as boolean,
              Replication: CONFIG.replicationEvents as boolean]

def emit = { Map payload ->
    println "===GROOVY_RUNNER_JSON_START==="
    println JsonOutput.toJson(payload)
    println "===GROOVY_RUNNER_JSON_END==="
}

if (!resourceResolver.getResource(auditRoot)) {
    emit([error: "Audit root not found: ${auditRoot}".toString(), rows: []])
    return
}

def q = { String s -> "'" + s.replace("'", "''") + "'" }  // JCR-SQL2 string literal
def isExcluded = { String path -> excludedPaths.any { ex -> path == ex || path.startsWith(ex + "/") } }

def where = ["ISDESCENDANTNODE([${auditRoot.replace(']', '')}])".toString()]
where << (!includeBelow ? "[cq:path] = ${q(basePath)}"
          : includeRoot ? "([cq:path] = ${q(basePath)} OR [cq:path] LIKE ${q(basePath + '/%')})"
          : "[cq:path] LIKE ${q(basePath + '/%')}").toString()
if (sinceDays > 0) {
    def since = Calendar.instance
    since.add(Calendar.DAY_OF_MONTH, -sinceDays)
    def iso = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX").format(since.time)
    where << "[cq:time] >= CAST(${q(iso)} AS DATE)".toString()
}
def sql = "SELECT * FROM [cq:AuditEvent] WHERE ${where.join(' AND ')}".toString()

def dateFormat = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss")
def rows = []
def eventsRead = 0
def perLog = [:].withDefault { 0 }
try {
    def results = resourceResolver.findResources(sql, "JCR-SQL2")
    while (results.hasNext()) {
        Resource event = results.next()
        eventsRead++
        def vm = event.adaptTo(ValueMap)
        def path = (vm?.get("cq:path", "") ?: "") as String

        // LIKE also treats "_" as a wildcard, so the path is checked exactly here.
        def isRoot = path == basePath
        if (!(isRoot ? includeRoot : includeBelow && path.startsWith(basePath + "/"))) continue
        if (isExcluded(path)) continue
        if (levels > 0) {
            def level = isRoot ? 0 : path.substring(basePath.length() + 1).tokenize("/").size()
            if (level != levels) continue
        }

        def category = (vm.get("cq:category", "") ?: "") as String
        def log = LOGS[category] ?: (category ?: "Other")
        if (LOGS[category] && !wanted[log]) continue

        def eventType = (vm.get("cq:type", "") ?: "") as String
        if (eventTypes && !eventTypes.contains(eventType.toLowerCase())) continue
        // AEM stores the user as cq:userid; cq:userId is read too in case an older/custom writer used it.
        def user = (vm.get("cq:userid", "") ?: vm.get("cq:userId", "") ?: "") as String
        if (users && !users.contains(user.toLowerCase())) continue

        def time = vm.get("cq:time", Calendar)
        perLog[log]++
        rows << [
            "Log"       : log,
            "Path"      : path,
            "Event Type": eventType,
            "User"      : user,
            "Event Time": time ? dateFormat.format(time.time) : "",
        ]
    }
} catch (Exception e) {
    // Typically AEM's query read limit on a large /var/audit without an index for cq:AuditEvent.
    emit([error: ("Audit query failed after reading ${eventsRead} event(s): ${e.message}. On a large audit log, set " +
                  "'Only events from the last N days', or enter a smaller path.").toString(), rows: []])
    return
}
rows.sort { a, b -> b["Event Time"] <=> a["Event Time"] ?: a["Path"] <=> b["Path"] }  // newest first

println "Total rows generated: ${rows.size()}"
emit([basePath: basePath, eventsRead: eventsRead, pageEvents: perLog["Page"], assetEvents: perLog["Asset"],
      replicationEvents: perLog["Replication"], rowCount: rows.size(), rows: rows])
