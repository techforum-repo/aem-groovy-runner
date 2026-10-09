# AEM Groovy Runner

A Streamlit app that runs Groovy scripts on AEM as a Cloud Service through the
Groovy Console's HTTP API (`POST /bin/groovyconsole/post.json`, the same call
the console UI makes). It replaces the manual routine of editing the path in
the console, copying the JSON, deleting the count line, and running
`format_references_batch.py`.

**Pick a script → fill in its inputs → run (one run per path) → format this
session's results into Excel → download.** Every step is audited.

## Quick start

```bash
./start-unix.sh          # or start-windows.bat: creates .venv, installs deps, copies .env
```

The app starts in **mock mode** with sample data, so you can try the whole
flow straight away. To go live:

1. **Settings**: add your AEM environments (a name such as DEV, QA or PROD,
   and its author URL) and turn **Mock mode** off.
2. **Pick the environment** in the sidebar, then **sign in** to it (sidebar or
   Settings page): in Cloud Manager open that environment's
   **Developer Console** (you sign in with your usual SSO). Then go to
   **Integrations → Local token → Get Local Development Token**, copy it
   (the whole JSON is fine), and paste it into the Sign in box.
3. **Diagnostics → Test connection**: it shows who you're signed in as and
   runs a one-line script.

### Several environments

Add as many AEM environments as you like on the **Settings** page (one row
each: name + author URL). In the sidebar you just **pick the environment**:

- It shows whether you're signed in to it (🟢 / ⚪) and, if not, the sign-in box.
- **Each environment has its own sign-in**: signing in to QA doesn't touch
  your DEV sign-in, and switching back and forth keeps both. Each needs a
  token from *that* environment's Developer Console. "Remember" stores each
  one separately in the OS keychain (keyed by the environment's host), and
  signing out of one leaves the others signed in.
- The **Run button names the environment** (**▶️ Run on QA (2 runs)**), and
  the progress bar, History (`aem_host`) and audit trail record it.
- A run that's in progress keeps using the environment it started on, even if
  you switch in the sidebar. **Retry** always goes back to the run's own
  environment: if you've switched since, it asks you to switch back.
- If an environment's author URL is changed, its sign-in is dropped (a token
  is never sent to a different server).
- The sidebar preselects the environment you used last on this computer.
- Upgrading from a single author URL: it becomes an environment named
  **Default**; rename it or add more on the Settings page.

### Authentication: your own access only

Every script runs **as you**. The Local Development Token is issued for your
own SSO login, so AEM applies your permissions and Groovy Console access and
records your name, and the audit trail shows it too. There's deliberately no
technical account, service credentials, or basic auth.

- The token is valid for **24 hours**. The sidebar shows the time left and
  asks you to sign in again when it expires. Adobe gives no way to refresh
  it automatically, so expect to paste a new one about once a day.
- **Remember on this computer until it expires** (ticked by default when
  available): the token is stored in your **OS keychain** (GNOME Keyring,
  macOS Keychain, or Windows Credential Manager), so page refreshes and app
  restarts don't ask again. **Sign out** removes it, and an expired token
  is deleted automatically. If no secure keychain exists (e.g. a headless
  server, or `keyring` not installed because the start script wasn't re-run),
  the box says so and refreshes will ask again; there is no plaintext fallback.
- **When you're asked to sign in again:** only when there's no token, the
  token has expired, or AEM rejects it. A remembered token is checked with
  AEM once when it's restored; if AEM rejects it, or rejects it during a run,
  it's forgotten and the sign-in box explains why. If AEM simply can't be
  reached (VPN off), you stay signed in.
- Otherwise the token lives only in the browser session's memory. It is
  never written to `.env`, the database, or the logs; audit events identify
  it by a 12-character SHA-256 fingerprint.
- Requirements: access to Developer Console for the environment, and
  membership of a group in the Groovy Console's `allowedGroups`. If you can
  already run scripts in the Groovy Console UI, you have both.

## Pages

| Page | What it does |
|---|---|
| Run | Choose a script, fill its form (with per-script presets), optionally tick **Batch** for large paths, run it, then format/preview/download **this session's** results (one entry, and one file, per entered path): all Excel files individually in one click, a zip, or a single file |
| Scripts | Every script found under `scripts/`, with its read-only check result, inputs, whether Batch is available for it, source, and any manifest problems; also how to add one |
| Formatters | The formatter catalogue and the script ↔ formatter **links** grid |
| History | All runs from every session; re-download earlier files, including combined files (one per entered path for batches) |
| Audit | Append-only audit trail (filter + CSV export) and per-run traceability with a file-integrity check |
| Settings | AEM environments (name + author URL), mock mode, Groovy Console endpoint, timeout; how to sign in |
| Diagnostics | Connection test, log download |

## Scripts

```
scripts/<script-id>/script.groovy   # reads its inputs from CONFIG
scripts/<script-id>/manifest.json   # name, description, inputs, formatters
scripts/<anything>.groovy           # plain script: no inputs, generic formatting
```

Bundled scripts:
- `asset-reference-report`: the asset → referencing-page report, same logic
  as the hand-run `AssetReferenceReport.groovy`. Its format filter, folder
  exclusions, and other options are now form inputs.
- `page-report`: every page under a root (tree walk): status, last modified and
  published dates and users, template and template type, created, depth. It's
  the team's TSV page report, converted, with the same 12 columns. The root can
  be a site or a folder such as `/content`.
- `assets-by-type`: DAM assets by type (MIME, wildcards like `image/*`).
  **Summary** mode gives counts and total size per type, optionally per folder;
  **list** mode gives one row per asset, with an optional cap. It's built for
  very large repositories: one indexed JCR-SQL2 query streamed lazily, with the
  type filter inside the query and `OPTION(TRAVERSAL FAIL)` so AEM refuses
  rather than crawls if no index fits.

- `audit-events`: the AEM audit log for pages **and** assets under a path:
  who created, modified, moved, deleted or published what, and when (newest
  first). It's the team's console page-audit script, converted and extended.
  - It reads AEM's three audit logs (`cq:AuditEvent` nodes under `/var/audit`):
    page events (`com.day.cq.wcm.core.page`, e.g. `PageModified`), asset events
    (`com.day.cq.dam`, e.g. `METADATA_UPDATED`) and publish events
    (`com.day.cq.replication`: `Activate`/`Deactivate`, for pages and assets).
    A **Log** column says which.
  - Event types are matched by exact name; the default keeps changes and
    publishing and drops noise such as `ASSET_VIEWED` or `RENDITION_UPDATED`.
    Clear the list to see every type recorded.
  - The user is read from `cq:userid`, the property name in Adobe's
    `cq:AuditEvent` definition (the original script read `cq:userId`, so its
    user column was likely empty); `cq:userId` is still read as a fallback.
  - Same query shape as the original (`cq:path` under the entered path, checked
    exactly afterwards, since `_` is a wildcard in `LIKE`), via read-only
    `findResources`. Options: last N days, users, excluded subtrees, exact level.
  - **Scope:** the path and everything under it (default), **only the path
    itself** (one page's or asset's own history, an exact-path query), or only
    what's under it (as the original did).
  - **Retention:** audit logs are purged on a schedule. On AEM as a Cloud
    Service, Adobe's newer default keeps only 7 days (older environments: 7
    years), so an empty result can mean the events are gone. A query that
    hits AEM's read limit on a big audit log fails with a message suggesting
    a days limit.
  - Batch is off for it: events of deleted pages and assets can't be found by
    discovery.

**Inputs** are injected as base64 JSON replacing `__CONFIG_B64__`, so paths
with spaces or quotes can't break the script:

```groovy
def CONFIG = new JsonSlurper().parseText(new String("__CONFIG_B64__".decodeBase64(), "UTF-8"))
```

Input types: `text`, `path`, `path_list`, `string_list`, `bool`, `number`,
`select`. A list input with `"iterate": true` means one run, and one output
file, per line. Other keys: `required`, `default`, `advanced`, `help`,
`placeholder`, `must_start_with`, `options`.

**Output**: print one JSON document between `===GROOVY_RUNNER_JSON_START===`
and `===GROOVY_RUNNER_JSON_END===`. For a `{"rows": [...], ...}` result, the
rows are saved as the result file and the other keys show as run details;
`{"error": "..."}` fails the run. Scripts without markers still work: the
first output line starting with `[` or `{` is parsed, so an unmodified
console script that prints `Total rows generated: N` and then JSON is fine.

**Starting a new script:** copy a sample to `scripts/<your-script-id>/` and
change its report logic: `examples/asset-sample/` (DAM assets under a folder)
or `examples/page-sample/` (pages under a root). Each is a complete, annotated
skeleton (inputs, output markers, error handling) that already meets the Batch
requirements below. The Scripts page shows both under "Adding a script", one
tab each.

**Writing a script that supports Batch.** Batching is done by the app, so a
script needs no batching code. It's offered automatically when the script:

1. has a `path_list` input with `"iterate": true` (one run per entered path),
2. has an excluded-paths input: a `path_list` named in `systemExcludes`, or
   whose key or label contains "exclude" (e.g. `excludedPaths`), and
3. **skips the whole subtree** under each excluded path (`path == ex ||
   path.startsWith(ex + "/")`), as the bundled scripts do.

The app may call it with excluded paths you didn't type: for a part that
covers only the content sitting directly in a folder, it excludes that
folder's children. If an option is counted from the entered path (a max depth,
a row cap), list it in `batch.notWith`; if rows are totals (counts per type),
declare `batch.mergeRows`. See [Whole-repository runs](#whole-repository-runs-batching).

## Formatters

| ID | Output |
|---|---|
| `asset-reference-excel` | The 16-column "Asset References" sheet from `format_references_batch.py` |
| `generic-excel` | Any JSON as one sheet: a row per object, nested fields as dotted columns, lists joined with `; ` |

**Scripts and formatters are independent.** A script doesn't know about
formatting, and a formatter doesn't know about scripts. They are connected
only by **links** in `formatter_links.json` at the project root: a small,
reviewable file you can keep in git so a team shares the same links.

```json
{
  "asset-reference-report": {"formatters": ["asset-reference-excel", "generic-excel"], "default": "asset-reference-excel"},
  "page-report":            {"formatters": ["generic-excel"], "default": "generic-excel"}
}
```

- **Formatters page**: the catalogue of formatters (what each produces and
  which scripts link to it), plus a **Links** grid with a row per script, a
  checkbox per formatter, and a **Default** column. Save writes the file, and
  each change is audited with before and after.
- A script with no link (e.g. one you just added) is flagged **"not linked"**
  and falls back to Generic Excel until you link it.
- On first start, if the file doesn't exist, it's created from where links
  used to live: the manifest `formatters` lists and the old Scripts-page
  mapping. After that, `formatters` in a manifest is ignored.

**Per run:** the Run page's **"Format results with"** dropdown offers the
script's linked formatters, starting with its default, plus **None (JSON
only)**, for that run only. The results table shows the **Formatter used** for each
file, and the Format step can apply further formatters afterwards; ones not
linked to the script are marked as such.

Formatting only accepts files generated in the current browser session, and
refuses a result JSON whose hash changed since the run. Each formatter
checks that the data fits first, so for example Asset reference Excel on
Page Inventory results gives a clear message rather than a broken file. Add a
formatter by creating a module in `groovy_runner/formatters/` that exposes
`FORMATTER` and registering it in `formatters/__init__.py` (this needs an
app restart). It then appears in every script's mapping editor.

## Skipped system areas

Page Report, Assets by Type and Asset Reference Report skip AEM system areas
on **every run, batch or not**: **"Skip AEM system areas"** under the inputs,
on by default, with the list editable and a **Restore defaults** button.
The skips are merged into the script's own excluded paths for that run, so:

- **Assets by Type** never queries them: they're excluded inside the query.
- **Asset Reference Report** skips those assets before the expensive
  per-asset reference search.
- **Page Report** doesn't walk those subtrees.

The merged list is what's sent to AEM and recorded in the run's inputs and the
audit trail. Defaults:

- **DAM reports:** `projects`, `collections`, `formsanddocuments`,
  `formsanddocuments-fdmodels`, `screens`, `workflow`, `catalogs`, and the AEM
  sample folders.
- **Page Report:** the system areas that can be pages (`campaigns`, `catalogs`,
  `communities`, `forms`, `screens`, `projects`, `usergenerated`). Non-page
  areas (`/content/dam`, the experience-fragments and launches roots,
  `cq:tags`) are never treated as sites anyway.

Skips must sit under the script's main path (`/content/dam` for the DAM
reports, `/content` for pages), checked by whole path segment. They're set
in the manifest:

```json
"systemExcludes": {"input": "excludedFolders", "paths": ["/content/dam/projects", "..."]}
```

`input` names the script's own excluded-paths input that the skips merge into.

## Whole-repository runs (batching)

Running a report on all of `/content` or `/content/dam` in one request is
risky: a long open request, large memory use in AEM, huge output, and AEM's
per-query read limit (commonly 100,000 nodes). Scripts that take a list of
paths offer **Batch** (a checkbox under their inputs; all three bundled
scripts do):

Batching never changes **what** is reported: every page or asset under the
path you enter is included, whatever the settings. It only splits the work
into smaller requests, and you still get **one file per entered path**.

1. **Discover:** one small read-only request splits each entered path into
   parts: child **pages** for Page Report (each site under `/content`), child
   **folders** for the DAM reports (each top-level folder under `/content/dam`).
   Content sitting *above* the split (assets directly in the entered folder or
   in an intermediate folder, or an entered/intermediate page's own row) gets a
   small **direct items** part of its own, so nothing is left out. Your excluded
   paths and the skipped system areas are skipped here too.
2. **Run:** the script runs **once per part**: small requests, live progress,
   Cancel and Retry per part.
3. **One file per entered path:** the parts' results are **combined
   automatically** into one file per path you entered. The results table shows
   one entry per entered path; the parts are listed under **Batch parts** for
   traceability only. After a **Retry** of failed or cancelled parts, that file
   is rebuilt to include them, using only that batch's own earlier results; a
   new run never borrows results from a different run. Combined files are also
   listed on **History**, so they survive a page refresh. If the combined rows
   would exceed Excel's 1,048,575-row limit, each part is formatted separately
   instead, with a message saying so.

**Levels below the entered path** sets only where the work is split: 1 = each
direct child (each site under `/content`, each top-level DAM folder); 2 = each
child of those, for when one top-level folder is itself too big. A folder or
page above that level with nothing further to split is run whole.

Discovery is audited (`run.batch_discovered`), as is combining
(`format.combined`, with the source runs' hashes).

Batching lives entirely in the app: **scripts contain no batching code**.
With Batch on, the app discovers the parts, then calls the unchanged script
once per part with ordinary inputs: the path input set to the part, and for a
"direct items" part, that path's children added to the script's own
excluded-paths input. With Batch off, the script simply runs on what you
entered.

Any script gets Batch automatically when its one-run-per-line input is a
`path_list` and it has an excluded-paths input (a `path_list`: the one named in
`systemExcludes`, or its only other `path_list` input), provided the script
skips whole subtrees under the excluded paths. The kind is inferred from the
path prefix (`/content/dam` = folders, otherwise pages). A `batch` section in
the manifest is only needed to override that, or `"batch": false` to switch it off:

```json
"batch": {
  "input": "rootPath",
  "kind": "page",
  "excludeInput": "excludedPaths",
  "includeRootInput": "includeRoot",
  "label": "..."
}
```

`includeRootInput` (optional) names a bool input that says whether the
entered root's own content is wanted: the app keeps it on for the parts,
whose roots are internal.

Two more optional settings keep a batched file identical to an unbatched run:

- `notWith`: inputs measured from the entered path (a max depth, a row cap,
  folder-grouping levels). Each part has its own root, so they can't mean the
  same thing; while any of them is set, Batch is refused with a message.
- `mergeRows`: for scripts whose rows are already totals (Assets by Type's
  summary), rows with the same `groupBy` values have their `sum` columns added
  up, so the file has one total per type instead of one per part.

```json
"notWith": ["maxAssets", "groupByFolderLevels"],
"mergeRows": {"groupBy": ["Folder", "Format"], "sum": ["Assets", "Total Size (MB)"]}
```

Without batching, Page Report also accepts a folder root (`/content` walks
every site in one run), and Assets by Type can run **one query per top-level
folder** inside a single run. Both are fine for medium sizes; batching is the
safer choice for the whole repository. You can still combine any selected
results by hand in the Format step (**Combine selected into one file**).

## Read-only enforcement

This tool only runs **read-only** scripts. Every script passes through
`groovy_runner/readonly.py` in the Groovy Console client, so there's no path
around it and no setting to turn it off. There are three layers:

1. **Static check, before anything is sent.** The script is scanned (comments
   and string contents ignored; `${...}` code inside GStrings checked) for:
   - writes: `save`/`commit`, JCR/Sling/PageManager writes, `ModifiableValueMap`,
     Groovy Console helpers like `activate`/`delete`/`copy`/`move`,
     replication, workflows/jobs, packages, Workspace operations,
     user/ACL changes
   - escape hatches that could hide a write: dynamic method names
     (`obj."$name"()`), method pointers, metaprogramming, reflection,
     `evaluate`/`GroovyShell`, `@CompileStatic`/`@Grab`, privileged services
     (`ResourceResolverFactory`, `impersonate`, …), shell/file/network access

   A failing script is flagged on the Scripts page with line numbers, can't
   be run from the Run page, and is refused by the runner and client
   (audited as `run.blocked`).
2. **Runtime guard, injected after the imports.** It makes
   `session.save()`, `resourceResolver.commit()/create/delete/move/copy`, and
   the `pageManager` write methods throw inside AEM. Without a save or commit,
   JCR changes are discarded when the request ends. The script exactly as
   sent, guard included, is saved as `<slug>.groovy` and hashed.
3. **AEM permissions.** Scripts run as you, so AEM itself only blocks what
   your account can't do. If your account can write, layers 1–2 are the
   protection. They catch mistakes and obvious misuse, but Groovy is dynamic
   and Java code inside a service can write without any of the above being
   visible in the script, so they are not a guarantee against a deliberate
   bypass.

If a legitimate read-only script trips a rule (for example a variable named
`workspace`), rename the identifier. Scripts that genuinely need to write
belong in the Groovy Console itself, not in this tool.

## Audit & traceability

- **Audit trail** (`audit_events` table, mirrored to `logs/groovy-runner.log`).
  It is append-only: the app has no update or delete path. Events:
  - session started
  - run batch started / run completed / run failed / run blocked (read-only check) / cancel requested / run cancelled
  - format completed / format failed
  - file downloaded: written when the download is actually generated (on click), with the hash of exactly what
    was handed over. For the one-click "download all" button the app logs when it was offered, with each file's
    hash, because that click happens inside the browser.
  - signed in / restored from keychain / signed out / sign-in failed (AEM user, token expiry, token fingerprint; never the token)
  - settings saved / reset
  - preset saved / updated / deleted
  - formatter links changed (before/after)
  - connection tested
  - integrity checked

  Each event records the local actor (OS user@host), your AEM user, and the session ID.
- **Per run**:
  - inputs, AEM host, the AEM user it ran as (you), start/end times, and the console's running time
  - the **exact script sent**, saved as `<slug>.groovy`
  - SHA-256 of the template, executed script, result JSON, and every formatted file
- **Audit → Run traceability → Verify files** recomputes those hashes to show
  whether any file has been modified since it was created.
- Your token is never logged or stored, only its fingerprint.

## Local files

| Path | Contents | Permissions |
|---|---|---|
| `groovy_runner.db` | history, audit trail, presets, settings | 0600 |
| `output/` | results per session/script/run | dir 0700 |
| `logs/` | rotating log | 0600 |

All of these are git-ignored.

## Network access (security)

`.streamlit/config.toml` binds the app to **localhost**, so only the machine
it runs on can open it. Streamlit's default is every network interface. On a
shared server that would let anyone who can reach the port use the app, and
because a remembered sign-in belongs to the OS user running the app, they
would be signed in as **you**, able to run scripts with your AEM permissions
and download everyone's results from History.

**Port:** the start scripts try `GROOVY_RUNNER_PORT` (default 8501, set it in
`.env`). If anything is already running there, they move to the next free port
and print the URL, e.g. `Starting AEM Groovy Runner on http://localhost:8502`.
They check for any existing listener themselves (`groovy_runner/port.py`)
because, on Windows, the localhost-only binding doesn't conflict with another
app listening on all interfaces, so Streamlit's own "port in use" fallback
never triggers.

As a second safeguard, a remembered sign-in is restored, and **Remember** is
offered, only for browsers on the same machine. If you change `address` to
reach the app from elsewhere, don't use **Remember**, and treat the app
as shared.

## Notes

- **Running and cancelling.** A batch runs in the background with live
  progress, and the paths run one at a time to keep load on the author low.
  **⏹ Cancel** skips the remaining paths, which are never sent to AEM, and
  stops waiting for the one in flight. The Groovy Console has no API to stop
  a script already executing in AEM, so that one finishes there (read-only)
  and its result is discarded.
  - Runs finished before the cancel are kept, and formatted if a formatter
    was chosen.
  - Cancelled runs show as **⏹ Cancelled** in the results, History, and the
    audit trail (`run.batch_cancel_requested`, `run.cancelled`).
  - **Retry** re-runs both the failed and the cancelled runs.
- Runs execute one at a time to keep load on the author low. A very large
  folder can exceed the request timeout: tick **Batch**, split it into
  subfolders yourself, or raise the per-run timeout on the Settings page.
- **Batch on a whole DAM.** A "direct items" part (assets sitting directly in
  a folder above the split) runs the script on that folder with its
  subfolders excluded. The bundled DAM scripts still query the whole folder
  before dropping the excluded assets, so on `/content/dam` itself that one
  part can be slow or hit AEM's query limit. It then shows as failed (nothing
  is silently missed). Entering the top-level folders instead avoids it.
- The Groovy Console is often disabled on production. Run against an
  environment where it's enabled.

## Tests

```bash
pip install -r requirements-dev.txt && pytest
# Optional: also execute the bundled Groovy scripts on a JVM against stubbed AEM APIs
GROOVY_JARS=/path/groovy-4.0.22.jar:/path/groovy-json-4.0.22.jar pytest tests/test_groovy_harness.py
```
