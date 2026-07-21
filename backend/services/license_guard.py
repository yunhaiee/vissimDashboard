from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PRODUCT_ID = "VISSIM_PLATFORM_SOUTHST"
DEFAULT_KEYGEN_API_BASE_URL = "https://api.keygen.sh/v1"
DEFAULT_CACHE_SECONDS = 86400

_STATUS_CACHE: dict[str, Any] = {"path": None, "mtime": None, "checked": None, "status": None}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _first_value(data: dict[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        value = data.get(name)
        if value not in (None, ""):
            return value
    return default


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _as_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def keygen_config() -> dict[str, Any]:
    path = _project_root() / "keygen-config.json"
    data: dict[str, Any] = {}
    if path.exists():
        data = _read_json(path)

    account = str(_first_value(data, "account", "accountId", "account_id")).strip()
    product = str(_first_value(data, "product", "productId", "product_id")).strip()
    api_base_url = str(
        _first_value(data, "apiBaseUrl", "api_base_url", "baseUrl", "base_url", default=DEFAULT_KEYGEN_API_BASE_URL)
    ).strip()
    cache_seconds = _as_positive_int(
        _first_value(data, "cacheSeconds", "cache_seconds", "validationCacheSeconds", "validation_cache_seconds", default=DEFAULT_CACHE_SECONDS),
        DEFAULT_CACHE_SECONDS,
    )
    activate_machine = _as_bool(_first_value(data, "activateMachine", "activate_machine", default=True), True)

    account = os.getenv("KEYGEN_ACCOUNT_ID", account).strip()
    product = os.getenv("KEYGEN_PRODUCT_ID", product).strip()
    api_base_url = os.getenv("KEYGEN_API_BASE_URL", api_base_url).strip()
    cache_seconds = _as_positive_int(os.getenv("KEYGEN_LICENSE_CACHE_SECONDS", cache_seconds), cache_seconds)
    activate_machine = _as_bool(os.getenv("KEYGEN_ACTIVATE_MACHINE", activate_machine), activate_machine)

    return {
        "account": account,
        "product": product,
        "api_base_url": api_base_url.rstrip("/"),
        "cache_seconds": cache_seconds,
        "activate_machine": activate_machine,
        "path": str(path),
    }


def keygen_configured(config: dict[str, Any] | None = None) -> bool:
    config = config or keygen_config()
    account = str(config.get("account") or "").strip()
    return bool(account and account not in {"YOUR_KEYGEN_ACCOUNT_ID", "ACCOUNT_ID", "<account>"})


def license_required() -> bool:
    configured = os.getenv("VISSIM_PLATFORM_LICENSE_REQUIRED")
    if configured is not None:
        return configured.strip().lower() not in {"0", "false", "no", "off"}
    return (_project_root() / "runtime" / "license-required.flag").exists()


def _machine_guid() -> str:
    if os.name == "nt":
        try:
            output = subprocess.check_output(
                [
                    "reg",
                    "query",
                    r"HKLM\SOFTWARE\Microsoft\Cryptography",
                    "/v",
                    "MachineGuid",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
            )
            for line in output.splitlines():
                if "MachineGuid" in line:
                    return line.split()[-1].strip()
        except Exception:
            pass
    return f"{platform.node()}|{os.getenv('USERNAME') or os.getenv('USER') or ''}"


def machine_id() -> str:
    digest = hashlib.sha256(f"{PRODUCT_ID}|{_machine_guid()}".encode("utf-8")).hexdigest()
    prefix = digest[:20].upper()
    return "-".join(prefix[index : index + 4] for index in range(0, len(prefix), 4))


def _license_paths() -> list[Path]:
    paths: list[Path] = []
    for env_name in ("ProgramData", "LOCALAPPDATA"):
        value = os.getenv(env_name)
        if value:
            paths.append(Path(value) / "Dochak Prism" / "license.json")
            paths.append(Path(value) / "VISSIM Platform" / "license.json")
    paths.append(_project_root() / "runtime" / "license.json")
    return paths


def _parse_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _cached_license_is_fresh(data: dict[str, Any], config: dict[str, Any]) -> bool:
    validated_at = _parse_datetime(data.get("validated_at"))
    if not validated_at:
        return False
    return validated_at + dt.timedelta(seconds=int(config["cache_seconds"])) > dt.datetime.now(dt.timezone.utc)


def _keygen_error_message(payload: dict[str, Any]) -> str:
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        messages: list[str] = []
        for error in errors:
            if not isinstance(error, dict):
                continue
            parts = [str(error[key]) for key in ("code", "title", "detail") if error.get(key)]
            if parts:
                messages.append(": ".join(parts))
        if messages:
            return "; ".join(messages)

    meta = payload.get("meta")
    if isinstance(meta, dict) and meta.get("detail"):
        return str(meta["detail"])
    return "Keygen did not return a detailed error."


def _keygen_request(path: str, body: dict[str, Any], authorization: str | None = None) -> dict[str, Any]:
    config = keygen_config()
    if not keygen_configured(config):
        raise RuntimeError("Keygen account is not configured. Set account in keygen-config.json before packaging.")

    headers = {
        "Content-Type": "application/vnd.api+json",
        "Accept": "application/vnd.api+json",
    }
    if authorization:
        headers["Authorization"] = authorization

    url = f"{config['api_base_url']}/accounts/{config['account']}/{path}"
    request = Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            raise RuntimeError(_keygen_error_message(payload)) from exc
        except json.JSONDecodeError as json_exc:
            raise RuntimeError(f"Keygen request failed with HTTP {exc.code}. {raw}") from json_exc
    except URLError as exc:
        raise RuntimeError(f"Keygen request failed. {exc.reason}") from exc


def _validation_body(license_key: str) -> dict[str, Any]:
    config = keygen_config()
    scope: dict[str, Any] = {"fingerprint": machine_id()}
    if config["product"]:
        scope["product"] = config["product"]
    return {"meta": {"key": license_key, "scope": scope}}


def validate_keygen_license(license_key: str) -> tuple[bool, str, dict[str, Any] | None]:
    if not keygen_configured():
        return False, "Keygen account is not configured. Edit keygen-config.json and set account before packaging.", None
    try:
        payload = _keygen_request("licenses/actions/validate-key", _validation_body(license_key))
    except Exception as exc:
        return False, f"Keygen validation failed. {exc}", None

    meta = payload.get("meta") if isinstance(payload, dict) else None
    if isinstance(meta, dict) and meta.get("valid") is True:
        return True, "License accepted by Keygen.", payload

    return False, f"Keygen rejected this license key. {_keygen_error_message(payload)}", payload


def _license_key_from_data(data: dict[str, Any]) -> str:
    if data.get("key"):
        return str(data["key"])
    if data.get("provider") == "keygen" and data.get("serial"):
        return str(data["serial"])
    return ""


def _write_refreshed_license(path: Path, data: dict[str, Any], payload: dict[str, Any]) -> None:
    try:
        data["validated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        data["machine_id"] = machine_id()
        license_data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(license_data, dict):
            if license_data.get("id"):
                data["license_id"] = license_data["id"]
            attributes = license_data.get("attributes")
            if isinstance(attributes, dict):
                data["expires"] = attributes.get("expiry")
                data["keygen_status"] = attributes.get("status")
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _status_from_cache(path: Path, mtime: float) -> dict[str, Any] | None:
    checked = _STATUS_CACHE.get("checked")
    if (
        _STATUS_CACHE.get("path") == str(path)
        and _STATUS_CACHE.get("mtime") == mtime
        and isinstance(checked, dt.datetime)
        and checked + dt.timedelta(seconds=30) > dt.datetime.now(dt.timezone.utc)
    ):
        cached = _STATUS_CACHE.get("status")
        return cached.copy() if isinstance(cached, dict) else None
    return None


def _remember_status(path: Path, mtime: float, status: dict[str, Any]) -> dict[str, Any]:
    _STATUS_CACHE.update(
        {
            "path": str(path),
            "mtime": mtime,
            "checked": dt.datetime.now(dt.timezone.utc),
            "status": status.copy(),
        }
    )
    return status


def license_status() -> dict[str, Any]:
    for path in _license_paths():
        if not path.exists():
            continue

        try:
            mtime = path.stat().st_mtime
            cached_status = _status_from_cache(path, mtime)
            if cached_status:
                return cached_status

            data = _read_json(path)
            license_key = _license_key_from_data(data)
            if not license_key:
                return _remember_status(
                    path,
                    mtime,
                    {
                        "valid": False,
                        "message": "The saved license uses the old fixed-key format. Reactivate with a Keygen license key.",
                        "path": str(path),
                        "machine_id": machine_id(),
                        "payload": None,
                    },
                )

            config = keygen_config()
            if _cached_license_is_fresh(data, config):
                return _remember_status(
                    path,
                    mtime,
                    {
                        "valid": True,
                        "message": f"License OK: {path}",
                        "path": str(path),
                        "machine_id": machine_id(),
                        "payload": data,
                    },
                )

            valid, message, payload = validate_keygen_license(license_key)
            if valid and payload:
                _write_refreshed_license(path, data, payload)
            return _remember_status(
                path,
                path.stat().st_mtime if path.exists() else mtime,
                {
                    "valid": valid,
                    "message": message,
                    "path": str(path),
                    "machine_id": machine_id(),
                    "payload": payload,
                },
            )
        except Exception as exc:
            return {
                "valid": False,
                "message": f"The saved license file is unreadable. {exc}",
                "path": str(path),
                "machine_id": machine_id(),
                "payload": None,
            }

    return {
        "valid": False,
        "message": "No license has been activated yet.",
        "path": None,
        "machine_id": machine_id(),
        "payload": None,
    }
