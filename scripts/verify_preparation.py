"""Focused checks for source recovery and material preflight, not business tests."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    downloader = load_script("download_data")
    preflight = load_script("check_submission")
    manifest_bytes = (ROOT / "evidence/data/download_manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    original_hash = downloader.sha256(ROOT / "data/raw/online_retail_II.xlsx")
    # Windows normal mkdir avoids tempfile's restrictive 0700 ACL on some sandboxes.
    temporary_base = Path(tempfile.gettempdir()).resolve()
    scratch = (temporary_base / f"vc-r03-preparation-check-{uuid.uuid4().hex}").resolve()
    if scratch.parent != temporary_base:
        raise RuntimeError("Unexpected temporary directory boundary")
    scratch.mkdir()
    outcomes = []
    try:
        recovery_root = scratch / "clone_like_recovery"
        (recovery_root / "evidence/data").mkdir(parents=True)
        (recovery_root / "evidence/data/download_manifest.json").write_bytes(manifest_bytes)
        # A Git checkout has the manifest but neither raw file. Exercise this exact case.
        downloader.ROOT = recovery_root
        with contextlib.redirect_stdout(io.StringIO()):
            downloader.main()
        recovered = all(downloader.sha256(recovery_root / item["path"]) == item["sha256"] for item in manifest["files"])
        unchanged_manifest = (recovery_root / "evidence/data/download_manifest.json").read_bytes() == manifest_bytes
        assert recovered and unchanged_manifest
        outcomes.append({"name": "fresh_checkout_recovers_official_files_without_rewriting_first_manifest", "passed": True})
        with contextlib.redirect_stdout(io.StringIO()):
            downloader.main()
        outcomes.append({"name": "repeat_recovery_is_read_only_and_idempotent", "passed": True})
        broken_manifest = json.loads(manifest_bytes)
        broken_manifest["files"][0]["sha256"] = "0" * 64
        (recovery_root / "evidence/data/download_manifest.json").write_text(json.dumps(broken_manifest), encoding="utf-8")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                downloader.main()
        except SystemExit as exc:
            assert "integrity check failed" in str(exc)
        else:
            raise AssertionError("Changed integrity expectation was silently accepted")
        assert downloader.sha256(recovery_root / "data/raw/online_retail_II.xlsx") == original_hash
        outcomes.append({"name": "mismatched_integrity_record_refuses_to_overwrite_existing_source", "passed": True})

        preflight_root = scratch / "preflight_fixtures"
        daily = preflight_root / "每日工作记录"
        today = datetime.now(timezone(timedelta(hours=8))).date()
        for day in (today.isoformat(), "2026-99-99", (today + timedelta(days=1)).isoformat()):
            (daily / day / "截图").mkdir(parents=True)
        (daily / today.isoformat() / "截图/fake.png").mkdir()
        preflight.ROOT = preflight_root
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            try:
                preflight.main()
            except SystemExit as exc:
                assert exc.code == 1
        report = json.loads(output.getvalue())
        assert report["date_folder_count"] == 1 and len(report["invalid_or_future_dates"]) == 2
        assert report["daily_records"][0]["screenshot_files"] == []
        outcomes.append({"name": "invalid_and_future_dates_do_not_count", "passed": True})
        outcomes.append({"name": "directory_with_image_extension_is_not_a_screenshot", "passed": True})
    finally:
        # Only remove the isolated UUID directory created above; verify the boundary again.
        if scratch.resolve().parent != temporary_base or not scratch.name.startswith("vc-r03-preparation-check-"):
            raise RuntimeError("Refusing cleanup outside the isolated verification directory")
        shutil.rmtree(scratch)
    assert downloader.sha256(ROOT / "data/raw/online_retail_II.xlsx") == original_hash
    evidence = {
        "checked_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "scope": "Preparation tooling only; isolated fixtures, no business acceptance or student manual replay.",
        "original_source_unchanged": True,
        "total": len(outcomes), "passed": sum(item["passed"] for item in outcomes), "cases": outcomes,
    }
    destination = ROOT / "evidence/tests/preparation_checks.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
