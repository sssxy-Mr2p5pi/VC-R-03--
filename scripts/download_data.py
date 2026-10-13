"""Download the unmodified course dataset from UCI; preserve source evidence."""
from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://archive.ics.uci.edu/static/public/502/online%2Bretail%2Bii.zip"
PAGE_URL = "https://archive.ics.uci.edu/dataset/502/online+retail+ii"
WORKBOOK_NAME = "online_retail_II.xlsx"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    raw = ROOT / "data" / "raw"
    evidence = ROOT / "evidence" / "data"
    raw.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    archive_path = raw / "online_retail_II_uci.zip"
    workbook = raw / WORKBOOK_NAME
    manifest_path = evidence / "download_manifest.json"
    previous_manifest = None
    if manifest_path.exists():
        previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_paths = {"data/raw/online_retail_II_uci.zip", "data/raw/online_retail_II.xlsx"}
        if {item["path"] for item in previous_manifest["files"]} != expected_paths:
            raise SystemExit("Unexpected paths in source manifest; inspect it before recovery.")
        missing = []
        for item in previous_manifest["files"]:
            file_path = ROOT / item["path"]
            if not file_path.exists():
                missing.append(file_path)
            elif not file_path.is_file() or sha256(file_path) != item["sha256"]:
                raise SystemExit(f"Source integrity check failed: {file_path}")
        if not missing:
            print("Existing download verified against its recorded SHA-256 hashes.")
            return
        print("Recovering absent source files against the existing manifest; existing files are read-only.", flush=True)
    elif workbook.exists() or archive_path.exists():
        raise SystemExit("Unrecorded source files exist. Inspect them before downloading again.")
    partial = archive_path.with_suffix(".zip.part")
    if archive_path.exists():
        archive_candidate = archive_path
        final_url = SOURCE_URL
        headers = {}
    else:
        request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "VC-R-03-Course-Data-Preparation/1.0"})
        print(f"Downloading official UCI source: {SOURCE_URL}", flush=True)
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as out:
            final_url = response.geturl()
            length = response.headers.get("Content-Length")
            headers = {key: response.headers.get(key) for key in ("Content-Type", "Last-Modified", "ETag")}
            shutil.copyfileobj(response, out, length=1024 * 1024)
        if length and partial.stat().st_size != int(length):
            raise SystemExit("Download byte count differs from the HTTP Content-Length.")
        archive_candidate = partial
    with ZipFile(archive_candidate) as archive:
        broken = archive.testzip()
        if broken:
            raise SystemExit(f"ZIP integrity failure: {broken}")
        matches = [name for name in archive.namelist() if Path(name).name == WORKBOOK_NAME]
        if len(matches) != 1:
            raise SystemExit(f"Expected exactly one {WORKBOOK_NAME}; found {matches!r}")
        workbook_part = workbook.with_suffix(".xlsx.part")
        # Extract this one known member through streams; never trust ZIP member paths.
        with archive.open(matches[0]) as source, workbook_part.open("wb") as out:
            shutil.copyfileobj(source, out)
    with ZipFile(workbook_part) as xlsx:
        if "xl/workbook.xml" not in xlsx.namelist() or xlsx.testzip():
            raise SystemExit("Downloaded workbook is not a valid XLSX archive.")
    if previous_manifest:
        expected_hashes = {item["path"]: item["sha256"] for item in previous_manifest["files"]}
        for candidate, target in ((archive_candidate, archive_path), (workbook_part, workbook)):
            relative = str(target.relative_to(ROOT)).replace("\\", "/")
            if sha256(candidate) != expected_hashes[relative]:
                raise SystemExit(f"Recovered data differs from the recorded source; no original overwritten: {target}")
        recovered = []
        for candidate, target in ((archive_candidate, archive_path), (workbook_part, workbook)):
            if not target.exists():
                candidate.replace(target)
                recovered.append(str(target.relative_to(ROOT)).replace("\\", "/"))
        workbook_part.unlink(missing_ok=True)
        recovery_path = evidence / "source_recovery_history.json"
        history = json.loads(recovery_path.read_text(encoding="utf-8")) if recovery_path.exists() else []
        history.append({"recovered_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"), "files": recovered, "source": final_url, "verified_against_original_manifest": True})
        recovery_path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(history[-1], ensure_ascii=False, indent=2))
        return
    partial.replace(archive_path)
    workbook_part.replace(workbook)
    manifest = {
        "dataset": "UCI Online Retail II",
        "official_page": PAGE_URL,
        "download_url": SOURCE_URL,
        "final_download_url": final_url,
        "downloaded_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "citation": "Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5CG6D",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "official_expected_total_rows": 1067371,
        "http_headers": headers,
        "transformations": [],
        "hash_note": "Locally calculated integrity hashes, not an independently published UCI checksum.",
        "files": [
            {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in (archive_path, workbook)
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
