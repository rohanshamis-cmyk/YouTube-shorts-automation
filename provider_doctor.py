#!/usr/bin/env python3
"""Local provider readiness diagnostics for story pipeline media modes."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
from pathlib import Path
from typing import Any

SECRET_VARS = (
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "RUNWAYML_API_SECRET",
    "RUNWAY_API_KEY",
)

RUNWAY_CONFIG_VARS = (
    "RUNWAY_MODEL",
    "RUNWAY_RATIO",
    "RUNWAY_DURATION",
    "RUNWAY_MODE",
)

SCRIPT_CONFIG_VARS = (
    "SCRIPT_PROVIDER",
    "OPENROUTER_MODEL",
    "OPENROUTER_BASE_URL",
)

ALLOWED_RUNWAY_MODES = {"offline", "live", "auto"}


def _load_dotenv_if_available() -> dict[str, Any]:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return {"available": False, "loaded": False}

    loaded = bool(load_dotenv())
    return {"available": True, "loaded": loaded}


def _secret_display(value: str) -> str:
    if not value:
        return "missing"
    return f"set (length={len(value)})"


def _import_check(module_name: str, optional: bool = False) -> dict[str, Any]:
    try:
        importlib.import_module(module_name)
        return {"ok": True, "optional": optional, "error": ""}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "optional": optional, "error": str(exc)}


def _fallback_script_support() -> bool:
    script_path = Path(__file__).resolve().parent / "script_generator.py"
    if not script_path.exists():
        return False
    try:
        text = script_path.read_text(encoding="utf-8", errors="ignore").lower()
    except Exception:
        return False
    return (
        "no live script provider configured" in text
        and "fallback script" in text
        and "emergency fallback script" in text
    )


def _build_readiness(
    env_raw: dict[str, str],
    pkg_checks: dict[str, dict[str, Any]],
    fallback_script_available: bool,
) -> dict[str, dict[str, Any]]:
    openai_key_set = bool(env_raw.get("OPENAI_API_KEY", ""))
    runway_secret_set = bool(env_raw.get("RUNWAYML_API_SECRET", ""))
    openai_pkg_ok = bool(pkg_checks["openai"]["ok"])
    requests_pkg_ok = bool(pkg_checks["requests"]["ok"])

    runway_mode_raw = env_raw.get("RUNWAY_MODE", "").strip().lower()
    runway_mode_valid = (not runway_mode_raw) or runway_mode_raw in ALLOWED_RUNWAY_MODES

    script_path_ready = openai_key_set or fallback_script_available

    readiness: dict[str, dict[str, Any]] = {
        "offline_safe": {
            "status": "fallback_only",
            "reason": [
                "Offline mode is always valid and does not require live provider credentials."
            ],
        }
    }

    if openai_key_set and openai_pkg_ok:
        readiness["real_images"] = {
            "status": "ready_live",
            "reason": ["OPENAI_API_KEY is set and openai package import is available."],
        }
    else:
        reasons = []
        if not openai_key_set:
            reasons.append("OPENAI_API_KEY is missing.")
        if openai_key_set and not openai_pkg_ok:
            reasons.append("openai package import failed.")
        readiness["real_images"] = {
            "status": "fallback_only",
            "reason": reasons or ["Live image prerequisites are not fully met."],
        }

    if not requests_pkg_ok or not runway_mode_valid:
        reasons = []
        if not requests_pkg_ok:
            reasons.append("requests package import failed (Runway adapter dependency).")
        if not runway_mode_valid:
            reasons.append(
                f"RUNWAY_MODE='{env_raw.get('RUNWAY_MODE', '').strip()}' is invalid. "
                "Use offline/live/auto."
            )
        readiness["real_images_real_video"] = {
            "status": "misconfigured",
            "reason": reasons,
        }
    elif runway_secret_set and script_path_ready:
        reasons = [
            "RUNWAYML_API_SECRET is set and local script path is available.",
            "Mode will attempt live Runway first, then use offline fallback if provider errors occur.",
        ]
        if not openai_key_set or not openai_pkg_ok:
            reasons.append("Live image generation may still fallback locally.")
        readiness["real_images_real_video"] = {
            "status": "ready_live",
            "reason": reasons,
        }
    elif script_path_ready:
        readiness["real_images_real_video"] = {
            "status": "fallback_only",
            "reason": [
                "RUNWAYML_API_SECRET is missing; mode will degrade to offline Runway outputs.",
            ],
        }
    else:
        readiness["real_images_real_video"] = {
            "status": "misconfigured",
            "reason": [
                "No OPENAI_API_KEY and no detected fallback-safe script path.",
                "Cannot claim live-ready behavior.",
            ],
        }

    return readiness


def _build_script_provider_readiness(
    env_raw: dict[str, str],
) -> dict[str, dict[str, Any]]:
    openrouter_key_set = bool(env_raw.get("OPENROUTER_API_KEY", ""))
    openrouter_model = env_raw.get("OPENROUTER_MODEL", "").strip()
    openrouter_base_url = env_raw.get("OPENROUTER_BASE_URL", "").strip() or "https://openrouter.ai/api/v1"

    openrouter_reason = "OPENROUTER_API_KEY is set."
    if not openrouter_key_set:
        openrouter_reason = "OPENROUTER_API_KEY is missing."
    elif not openrouter_model:
        openrouter_reason += " OPENROUTER_MODEL is unset; default model will be used."

    return {
        "openrouter": {
            "config_ready": openrouter_key_set,
            "reason": openrouter_reason,
            "model": openrouter_model or "default: openai/gpt-4o-mini",
            "base_url": openrouter_base_url,
        }
    }


def _build_warnings(
    env_raw: dict[str, str],
    pkg_checks: dict[str, dict[str, Any]],
    fallback_script_available: bool,
) -> list[str]:
    warnings: list[str] = []

    runway_mode = env_raw.get("RUNWAY_MODE", "").strip().lower()
    if runway_mode and runway_mode not in ALLOWED_RUNWAY_MODES:
        warnings.append(f"RUNWAY_MODE '{runway_mode}' is invalid; expected offline/live/auto.")

    runway_ratio = env_raw.get("RUNWAY_RATIO", "").strip()
    if runway_ratio and not re.match(r"^\d+:\d+$", runway_ratio):
        warnings.append("RUNWAY_RATIO is set but not in W:H format (example: 720:1280).")

    runway_duration = env_raw.get("RUNWAY_DURATION", "").strip()
    if runway_duration:
        try:
            parsed_duration = int(runway_duration)
            if not 2 <= parsed_duration <= 10:
                warnings.append("RUNWAY_DURATION is outside expected range 2..10.")
        except ValueError:
            warnings.append("RUNWAY_DURATION is not an integer.")

    runway_api_key_set = bool(env_raw.get("RUNWAY_API_KEY", ""))
    runway_secret_set = bool(env_raw.get("RUNWAYML_API_SECRET", ""))
    if runway_api_key_set and not runway_secret_set:
        warnings.append(
            "RUNWAY_API_KEY is set but RUNWAYML_API_SECRET is missing. "
            "Runway adapter uses RUNWAYML_API_SECRET."
        )

    runway_related_set = any(bool(env_raw.get(name, "").strip()) for name in RUNWAY_CONFIG_VARS)
    if runway_related_set and not runway_secret_set and not runway_api_key_set:
        warnings.append(
            "Runway settings are present but no Runway credential is set. "
            "This often means .env values were not exported into the current shell."
        )

    if not env_raw.get("OPENAI_API_KEY", "") and not fallback_script_available:
        warnings.append("OPENAI_API_KEY is missing and fallback-safe script path was not detected.")

    if not pkg_checks["requests"]["ok"]:
        warnings.append("requests package is missing; Runway adapter import path can break.")

    if warnings:
        warnings.append("Python only sees exported env vars from the current shell.")

    return warnings


def _collect_report() -> dict[str, Any]:
    dotenv_info = _load_dotenv_if_available()

    env_raw = {
        name: os.getenv(name, "").strip()
        for name in (*SECRET_VARS, *RUNWAY_CONFIG_VARS, *SCRIPT_CONFIG_VARS)
    }

    env_display = {
        "OPENAI_API_KEY": _secret_display(env_raw["OPENAI_API_KEY"]),
        "OPENROUTER_API_KEY": _secret_display(env_raw["OPENROUTER_API_KEY"]),
        "RUNWAYML_API_SECRET": _secret_display(env_raw["RUNWAYML_API_SECRET"]),
        "RUNWAY_API_KEY": _secret_display(env_raw["RUNWAY_API_KEY"]),
        "RUNWAY_MODEL": env_raw["RUNWAY_MODEL"] or "unset",
        "RUNWAY_RATIO": env_raw["RUNWAY_RATIO"] or "unset",
        "RUNWAY_DURATION": env_raw["RUNWAY_DURATION"] or "unset",
        "RUNWAY_MODE": env_raw["RUNWAY_MODE"] or "unset",
        "SCRIPT_PROVIDER": env_raw["SCRIPT_PROVIDER"] or "unset",
        "OPENROUTER_MODEL": env_raw["OPENROUTER_MODEL"] or "unset",
        "OPENROUTER_BASE_URL": env_raw["OPENROUTER_BASE_URL"] or "unset",
    }

    pkg_checks = {
        "openai": _import_check("openai"),
        "requests": _import_check("requests"),
        "PIL": _import_check("PIL", optional=True),
    }

    fallback_script_available = _fallback_script_support()

    readiness = _build_readiness(
        env_raw=env_raw,
        pkg_checks=pkg_checks,
        fallback_script_available=fallback_script_available,
    )
    script_provider_readiness = _build_script_provider_readiness(env_raw=env_raw)

    warnings = _build_warnings(
        env_raw=env_raw,
        pkg_checks=pkg_checks,
        fallback_script_available=fallback_script_available,
    )

    return {
        "dotenv": dotenv_info,
        "environment": env_display,
        "packages": pkg_checks,
        "script_fallback_support": fallback_script_available,
        "script_provider_readiness": script_provider_readiness,
        "media_mode_readiness": readiness,
        "warnings": warnings,
    }


def _print_human(report: dict[str, Any]) -> None:
    print("Environment Variables")
    for name in (
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "RUNWAYML_API_SECRET",
        "RUNWAY_API_KEY",
        "RUNWAY_MODEL",
        "RUNWAY_RATIO",
        "RUNWAY_DURATION",
        "RUNWAY_MODE",
        "SCRIPT_PROVIDER",
        "OPENROUTER_MODEL",
        "OPENROUTER_BASE_URL",
    ):
        print(f"- {name}: {report['environment'][name]}")

    print("\nPackage Checks")
    for name in ("openai", "requests", "PIL"):
        row = report["packages"][name]
        if row["ok"]:
            suffix = " (optional)" if row.get("optional") else ""
            print(f"- {name}: import_ok{suffix}")
        else:
            suffix = " (optional)" if row.get("optional") else ""
            print(f"- {name}: import_failed{suffix}: {row.get('error', '')}")

    print("\nScript Provider Readiness")
    openrouter = report["script_provider_readiness"]["openrouter"]
    print(f"- openrouter config_ready: {'yes' if openrouter['config_ready'] else 'no'}")
    print(f"  reason: {openrouter['reason']}")
    print(f"  model: {openrouter['model']}")
    print(f"  base_url: {openrouter['base_url']}")

    print("\nMedia Mode Readiness")
    for mode in ("offline_safe", "real_images", "real_images_real_video"):
        row = report["media_mode_readiness"][mode]
        reasons = row.get("reason", [])
        reason_text = "; ".join(reasons) if reasons else ""
        print(f"- {mode}: {row['status']}")
        if reason_text:
            print(f"  reason: {reason_text}")

    print("\nWarnings")
    warnings = report.get("warnings", [])
    if not warnings:
        print("- none")
    else:
        for warning in warnings:
            print(f"- {warning}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose local provider readiness for story pipeline media modes.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = _collect_report()

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
