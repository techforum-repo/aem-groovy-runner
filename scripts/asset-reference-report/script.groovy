import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import javax.jcr.Session
import org.apache.sling.api.resource.Resource
import org.apache.sling.api.resource.ValueMap
import com.day.cq.search.QueryBuilder
import com.day.cq.search.PredicateGroup
import java.text.SimpleDateFormat
import com.day.cq.wcm.commons.ReferenceSearch

// =====================================
// INPUT — injected by AEM Groovy Runner from manifest.json inputs (groovy_runner/groovy_script.py).
// Base64 JSON so DAM paths with spaces/quotes need no Groovy escaping.
// =====================================
def CONFIG = new JsonSlurper().parseText(new String("__CONFIG_B64__".decodeBase64(), "UTF-8"))

def parentDamPath = CONFIG.parentDamPath as String
// Asset folders to skip completely
def excludedAssetFolders = (CONFIG.excludedAssetFolders ?: []) as List<String>
// Reference hits under these roots are ignored (e.g. /content/dam: an asset referenced by another asset)
def excludedRoots = (CONFIG.excludedReferenceRoots ?: []) as List<String>
// Empty = every asset type; otherwise only assets whose resolved format matches (case-insensitive)
def assetFormats = ((CONFIG.assetFormats ?: []) as List<String>).collect { it.toLowerCase() }
def includeUnreferenced = CONFIG.includeUnreferenced as boolean
def checkEncodedSpaces = CONFIG.checkEncodedSpaces as boolean

// The app parses only what's between these markers, so stray println output can't break it.
def emit = { Map payload ->
    println "===GROOVY_RUNNER_JSON_START==="
    println JsonOutput.toJson(payload)
    println "===GROOVY_RUNNER_JSON_END==="
}

def session = resourceResolver.adaptTo(Session)
def queryBuilder = getService(QueryBuilder)
def dateFormat = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss")

if (!resourceResolver.getResource(parentDamPath)) {
    emit([parentDamPath: parentDamPath, error: "Parent DAM path not found: ${parentDamPath}".toString(), rows: []])
    return
}

def formatDate = { value ->
    if (!value) return ""
    try {
        if (value instanceof Calendar) {
            return dateFormat.format(value.time)
        }
        return value.toString()
    } catch (Exception e) {
        return ""
    }
}

def isExcluded = { String path ->
    excludedRoots.any { ex -> path == ex || path.startsWith(ex + "/") }
}

def isExcludedAsset = { String assetPath ->
    excludedAssetFolders.any { excluded ->
        assetPath == excluded || assetPath.startsWith(excluded + "/")
    }
}

def getReplicationStatus = { ValueMap vm ->
    def action = vm?.get("cq:lastReplicationAction", "")
    def lastModified = vm?.get("cq:lastModified", Calendar)
    def lastReplicated = vm?.get("cq:lastReplicated", Calendar)

    if (!vm?.get("cq:lastReplicated")) {
        return "Never Published"
    }

    if (action == "Deactivate") {
        return "Unpublished"
    }

    if (action == "Activate") {
        if (lastModified && lastReplicated && lastModified.after(lastReplicated)) {
            return "Modified"
        }
        return "Published"
    }

    return "Unknown"
}

def getAssetDetails = { Resource asset ->
    def assetVm = asset.adaptTo(ValueMap)

    def assetContent = asset.getChild("jcr:content")
    def assetContentVm = assetContent?.adaptTo(ValueMap)

    def metadata = asset.getChild("jcr:content/metadata")
    def metadataVm = metadata?.adaptTo(ValueMap)

    def title =
        metadataVm?.get("dc:title", "") ?:
        metadataVm?.get("jcr:title", "") ?:
        assetContentVm?.get("jcr:title", "") ?: ""

    def format =
        metadataVm?.get("dc:format", "") ?:
        metadataVm?.get("dam:MIMEtype", "") ?:
        metadataVm?.get("format", "") ?: ""

    return [
        assetTitle         : title,
        assetFormat        : format,
        assetStatus        : getReplicationStatus(assetContentVm),

        assetCreatedDate   : formatDate(assetVm?.get("jcr:created")),
        assetCreatedBy     : assetVm?.get("jcr:createdBy", "") ?: "",

        assetModifiedDate  : formatDate(assetContentVm?.get("jcr:lastModified")),
        assetPublishedDate : formatDate(assetContentVm?.get("cq:lastReplicated")),
        assetModifiedBy    : assetContentVm?.get("jcr:lastModifiedBy", "") ?: "",
        assetPublishedBy   : assetContentVm?.get("cq:lastReplicatedBy", "") ?: ""
    ]
}

def getPageDetails = { String pagePath ->
    def pageContent = resourceResolver.getResource(pagePath + "/jcr:content")
    def vm = pageContent?.adaptTo(ValueMap)

    if (!vm) {
        return [
            pageStatus        : "Inactive",
            pageModifiedDate  : "",
            pagePublishedDate : "",
            pageModifiedBy    : "",
            pagePublishedBy   : ""
        ]
    }

    return [
        pageStatus        : getReplicationStatus(vm),
        pageModifiedDate  : formatDate(vm.get("cq:lastModified")),
        pagePublishedDate : formatDate(vm.get("cq:lastReplicated")),
        pageModifiedBy    : vm.get("cq:lastModifiedBy", "") ?: "",
        pagePublishedBy   : vm.get("cq:lastReplicatedBy", "") ?: ""
    ]
}

def searchReferences = { String assetPath ->
    def refs = [] as Set

    try {
        def referenceSearch = new ReferenceSearch()
        referenceSearch.setExact(true)
        referenceSearch.setMaxReferencesPerPage(-1)

        // Native AEM ReferenceSearch automatically rolls hits up to the owning cq:Page path
        def results = referenceSearch.search(resourceResolver, assetPath)

        results.keySet().each { hitPath ->
            if (!isExcluded(hitPath)) {
                refs << hitPath
            }
        }

        // Fallback to check for encoded spaces
        def encodedAssetPath = assetPath.replace(" ", "%20")
        if (checkEncodedSpaces && encodedAssetPath != assetPath) {
            def encodedResults = referenceSearch.search(resourceResolver, encodedAssetPath)
            encodedResults.keySet().each { hitPath ->
                if (!isExcluded(hitPath)) {
                    refs << hitPath
                }
            }
        }
    } catch (Exception e) {
        refs << "ERROR: ${e.message}".toString()
    }

    return refs.toList().sort()
}

def emptyPage = [
    pageModifiedDate  : "",
    pagePublishedDate : "",
    pageModifiedBy    : "",
    pagePublishedBy   : ""
]

def buildRow = { String assetPath, Map assetDetails, String referenceUrl, Map pageDetails ->
    [assetPath: assetPath] + assetDetails + [referenceUrl: referenceUrl] + pageDetails
}

def predicates = [
        "type"         : "dam:Asset",
        "path"         : parentDamPath,
        "p.limit"      : "-1",
        "orderby"      : "@jcr:path",
        "orderby.sort" : "asc"
]

def query = queryBuilder.createQuery(PredicateGroup.create(predicates), session)
def result = query.getResult()

def rows = []
def assetsScanned = 0
def skippedExcludedFolder = 0
def skippedFormat = 0

result.hits.each { hit ->
    def hitPath = "UNKNOWN"
    try {
        def asset = hit.getResource()
        if (!asset) return

        def assetPath = asset.path
        hitPath = assetPath
        if (isExcludedAsset(assetPath)) {
            skippedExcludedFolder++
            return
        }
        def assetDetails = getAssetDetails(asset)
        if (assetFormats && !assetFormats.contains((assetDetails.assetFormat ?: "").toString().toLowerCase())) {
            skippedFormat++
            return
        }
        assetsScanned++
        def references = searchReferences(assetPath)

        if (references && !references.isEmpty()) {
            references.each { ref ->
                if (ref.startsWith("ERROR:")) {
                    rows << buildRow(assetPath, assetDetails, ref, [pageStatus: "Error"] + emptyPage)
                } else {
                    rows << buildRow(assetPath, assetDetails, ref, getPageDetails(ref))
                }
            }
        } else if (includeUnreferenced) {
            rows << buildRow(assetPath, assetDetails, "", [pageStatus: "No Reference"] + emptyPage)
        }
    } catch (Exception e) {
        rows << [
            assetPath          : hitPath,
            assetTitle         : "",
            assetFormat        : "",
            assetStatus        : "Error",
            assetCreatedDate   : "",
            assetCreatedBy     : "",
            assetModifiedDate  : "",
            assetPublishedDate : "",
            assetModifiedBy    : "",
            assetPublishedBy   : "",
            referenceUrl       : "ERROR: ${e.message}".toString(),
            pageStatus         : "Error"
        ] + emptyPage
    }
}

println "Total rows generated: ${rows.size()}"

emit([
    parentDamPath         : parentDamPath,
    assetsScanned         : assetsScanned,
    skippedExcludedFolder : skippedExcludedFolder,
    skippedFormat         : skippedFormat,
    rowCount              : rows.size(),
    rows                  : rows
])
