import datetime as dt
import json

from services import license_guard


def _write_config(root, account="demo"):
    (root / "keygen-config.json").write_text(
        json.dumps(
            {
                "account": account,
                "apiBaseUrl": "https://api.keygen.sh/v1",
                "validationCacheSeconds": 86400,
                "activateMachine": True,
            }
        ),
        encoding="utf-8",
    )


def test_cached_keygen_license_is_valid_without_network(monkeypatch, tmp_path):
    _write_config(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    license_path = runtime / "license.json"
    license_path.write_text(
        json.dumps(
            {
                "product": "VISSIM_PLATFORM_SOUTHST",
                "provider": "keygen",
                "key": "KEYGEN-KEY",
                "validated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "machine_id": "AAAA-BBBB-CCCC-DDDD-EEEE",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(license_guard, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(license_guard, "machine_id", lambda: "AAAA-BBBB-CCCC-DDDD-EEEE")
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr(
        license_guard,
        "validate_keygen_license",
        lambda _key: (_ for _ in ()).throw(AssertionError("network validation should not run")),
    )
    license_guard._STATUS_CACHE.update({"path": None, "mtime": None, "checked": None, "status": None})

    status = license_guard.license_status()

    assert status["valid"] is True
    assert status["path"] == str(license_path)


def test_legacy_fixed_key_license_is_rejected(monkeypatch, tmp_path):
    _write_config(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "license.json").write_text(
        json.dumps(
            {
                "product": "VISSIM_PLATFORM_SOUTHST",
                "serial": "VPS-OLD-FIXED-KEY",
                "activated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(license_guard, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(license_guard, "machine_id", lambda: "AAAA-BBBB-CCCC-DDDD-EEEE")
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    license_guard._STATUS_CACHE.update({"path": None, "mtime": None, "checked": None, "status": None})

    status = license_guard.license_status()

    assert status["valid"] is False
    assert "old fixed-key format" in status["message"]
