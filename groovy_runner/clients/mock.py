from __future__ import annotations

"""Mock Groovy Console: decodes the inputs the real script would receive and
prints the same marker-wrapped JSON, so parsing, formatting, and history all
run the identical code path as a live AEM call. Recognizes the bundled
scripts by their input keys; any other script gets a generic echo result."""

import json
import random
from datetime import datetime, timedelta

from .. import readonly
from ..groovy_script import JSON_END, JSON_START, extract_config
from .groovy_console import GroovyResult

_STATUSES = ["Published", "Published", "Published", "Modified", "Never Published", "Unpublished"]
_USERS = ["jane.doe@example.com", "workflow-process-service", "john.smith@example.com", "admin"]
_FORMATS = ["application/pdf"] * 5 + ["image/jpeg", "image/png"]


class MockGroovyConsoleClient:
    def __init__(self, seed: int = 20261005) -> None:
        self._seed = seed

    def run_script(self, script: str) -> GroovyResult:
        script = readonly.prepare(script)  # same enforcement as the live client
        config = extract_config(script)
        if "root" in config and "kind" in config:
            payload = self._discover(config)
        elif "parentDamPath" in config:
            payload = self._asset_references(config)
        elif "rootPath" in config and "mode" in config:
            payload = self._assets_by_type(config)
        elif "rootPath" in config:
            payload = self._pages(config)
        else:
            rows = [{"input": k, "value": v} for k, v in config.items()] or [{"message": "mock run of a script with no inputs"}]
            payload = {"rowCount": len(rows), "rows": rows}
        output = f"Total rows generated: {payload.get('rowCount', 0)}\n{JSON_START}\n{json.dumps(payload)}\n{JSON_END}\n"
        return GroovyResult(output=output, result="", exception="", running_time=f"00:00:0{random.randint(1, 9)}.{random.randint(100, 999)}")

    def _discover(self, config: dict) -> dict:
        """Shaped like groovy_runner/builtin/discover_roots.groovy output."""
        root = config["root"]
        if config["kind"] == "page":
            names = ["acme", "acme-corporate", "acme-careers", "campaigns", "experience-fragments", "launches", "dam"]
        else:
            names = ["acme", "acme-campaigns", "acme-legal", "projects", "collections", "screens"]
        candidates = [f"{root}/{n}" for n in names]
        excludes = config.get("excludes") or []
        skipped = [c for c in candidates if any(c == ex or c.startswith(ex + "/") for ex in excludes)]
        system = {"experience-fragments", "launches", "dam"} if config["kind"] == "page" else set()
        children = [c for c in candidates if c not in skipped and c.rsplit("/", 1)[-1] not in system]
        # Folder: the entered folder holds assets of its own; page: an entered page has its own row.
        direct = ([{"path": root, "exclude": children}] if config["kind"] == "folder" or root.count("/") > 1 else [])
        return {"root": root, "kind": config["kind"], "levels": config.get("levels", 1), "children": children,
                "direct": direct, "skipped": skipped}

    def _pages(self, config: dict) -> dict:
        """Shaped like scripts/page-report output (same column names)."""
        rng = random.Random(f"{self._seed}:{config['rootPath']}")
        root = config["rootPath"]
        paths = [root] + [f"{root}/{section}" for section in ["products", "resources", "about"]]
        paths += [f"{p}/page-{i}" for p in paths[1:] for i in range(rng.randint(1, 4))]
        rows = []
        for path in sorted(paths):
            if any(path == ex or path.startswith(ex + "/") for ex in config.get("excludedPaths") or []):
                continue
            if path == root and not config.get("includeRoot", True):
                continue
            template = rng.choice(["/conf/acme/settings/wcm/templates/content-page", "/apps/acme/templates/legacy"])
            if config.get("templates") and template not in config["templates"]:
                continue
            rows.append({"Path": path, "Title": path.rsplit("/", 1)[-1].replace("-", " ").title(),
                         "Status": rng.choice(_STATUSES), "Last Modified": "2026-09-1%d 10:00:00" % rng.randint(0, 9),
                         "Last Published": "2026-09-0%d 10:00:00" % rng.randint(1, 9),
                         "Last Modified By": rng.choice(_USERS), "Last Published By": rng.choice(_USERS),
                         "Template": template,
                         "Template Type": "Editable Template" if template.startswith("/conf") else "Legacy Template",
                         "Created": "2025-0%d-01 09:00:00" % rng.randint(1, 9), "Created By": rng.choice(_USERS),
                         "Depth": len(path.strip("/").split("/"))})
        return {"rootPath": root, "pagesVisited": len(paths), "rowCount": len(rows), "rows": rows}

    def _assets_by_type(self, config: dict) -> dict:
        """Shaped like scripts/assets-by-type output."""
        rng = random.Random(f"{self._seed}:{config['rootPath']}")
        types = ["application/pdf", "image/jpeg", "image/png", "video/mp4", "application/zip"]
        wanted = config.get("formats") or []
        keep = [t for t in types if not wanted or any(t == w or (w.endswith("*") and t.startswith(w[:-1])) for w in wanted)]
        assets = [(f"{config['rootPath']}/{rng.choice(['brochures', 'images', 'videos'])}/asset-{i}", rng.choice(keep),
                   rng.randint(10_000, 20_000_000)) for i in range(rng.randint(20, 60))] if keep else []
        if config.get("mode") == "list":
            cap = int(config.get("maxAssets") or 0)
            rows = [{"Asset Path": p, "Title": p.rsplit("/", 1)[-1], "Format": f, "Size (bytes)": size,
                     "Status": rng.choice(_STATUSES), "Folder": p.rsplit("/", 1)[0]}
                    for p, f, size in (assets[:cap] if cap else assets)]
        else:
            summary: dict[str, list[int]] = {}
            for _, f, size in assets:
                entry = summary.setdefault(f, [0, 0])
                entry[0] += 1
                entry[1] += size
            rows = [{"Format": f, "Assets": c, "Total Size (MB)": round(b / 1048576, 2)}
                    for f, (c, b) in sorted(summary.items(), key=lambda kv: -kv[1][0])]
        return {"rootPath": config["rootPath"], "mode": config.get("mode", "summary"), "matchedAssets": len(assets),
                "rowCount": len(rows), "rows": rows}

    def _asset_references(self, config: dict) -> dict:
        root = config["parentDamPath"]
        rng = random.Random(f"{self._seed}:{root}")
        now = datetime(2026, 10, 1, 12, 0, 0)
        stamp = lambda days: (now - timedelta(days=days, minutes=rng.randint(0, 1440))).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
        site = "/content/acme/us/en"
        formats = [f.lower() for f in config.get("assetFormats") or []]
        rows, scanned, skipped_folder, skipped_format = [], 0, 0, 0
        for i in range(rng.randint(12, 30)):
            folder = rng.choice(["", "/brochures", "/ifu", "/archive"])
            asset_path = f"{root}{folder}/document-{i:03d}.pdf"
            if any(asset_path == ex or asset_path.startswith(ex + "/") for ex in config.get("excludedAssetFolders") or []):
                skipped_folder += 1
                continue
            fmt = rng.choice(_FORMATS)
            if formats and fmt not in formats:
                skipped_format += 1
                continue
            scanned += 1
            asset = {
                "assetPath": asset_path, "assetTitle": f"Sample Document {i}", "assetFormat": fmt,
                "assetStatus": rng.choice(_STATUSES), "assetCreatedDate": stamp(400), "assetCreatedBy": rng.choice(_USERS),
                "assetModifiedDate": stamp(90), "assetPublishedDate": stamp(60), "assetModifiedBy": rng.choice(_USERS),
                "assetPublishedBy": rng.choice(_USERS),
            }
            refs = rng.sample(["products/stent-a", "products/stent-b", "resources/library", "campaigns/fall"], k=rng.choice([0, 0, 1, 1, 2]))
            if not refs:
                if config.get("includeUnreferenced", True):
                    rows.append({**asset, "referenceUrl": "", "pageStatus": "No Reference", "pageModifiedDate": "",
                                 "pagePublishedDate": "", "pageModifiedBy": "", "pagePublishedBy": ""})
                continue
            for ref in refs:
                rows.append({**asset, "referenceUrl": f"{site}/{ref}", "pageStatus": rng.choice(_STATUSES),
                             "pageModifiedDate": stamp(30), "pagePublishedDate": stamp(20),
                             "pageModifiedBy": rng.choice(_USERS), "pagePublishedBy": rng.choice(_USERS)})
        return {"parentDamPath": root, "assetsScanned": scanned, "skippedExcludedFolder": skipped_folder,
                "skippedFormat": skipped_format, "rowCount": len(rows), "rows": rows}

    def identity(self) -> str:
        return "mock.user@example.com"

    def test_connection(self) -> tuple[str, str]:
        return "mock.user@example.com", "00:00:00.012"
