#!/usr/bin/env python3
"""Live provider probe: minimal real checks for OpenAI, OpenRouter, and Runway."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any

RUNWAY_ENDPOINT = "https://api.dev.runwayml.com/v1/image_to_video"
RUNWAY_VERSION = "2024-11-06"
RUNWAY_DEFAULT_MODEL = "gen4_turbo"
RUNWAY_DEFAULT_RATIO = "720:1280"
RUNWAY_DEFAULT_DURATION = 2

OPENAI_DEFAULT_MODEL = "gpt-4o-mini"
OPENAI_MIN_OUTPUT_TOKENS = 16

OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL = "openai/gpt-4o-mini"
OPENROUTER_MIN_OUTPUT_TOKENS = 16

ONE_PIXEL_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6pG8kAAAAASUVORK5CYII="
)


@dataclass(slots=True)
class ProbeResult:
    provider: str
    status: str
    reason: str
    pipeline_live_usable_now: bool


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return
    load_dotenv()


def _normalize_ratio(value: str) -> str:
    raw = str(value or "").strip()
    if re.match(r"^\d+:\d+$", raw):
        return raw
    return RUNWAY_DEFAULT_RATIO


def _normalize_duration(value: str) -> int:
    raw = str(value or "").strip()
    if not raw:
        return RUNWAY_DEFAULT_DURATION
    try:
        parsed = int(raw)
    except ValueError:
        return RUNWAY_DEFAULT_DURATION
    return max(2, min(10, parsed))


def _extract_runway_error_message(response: Any) -> str:
    try:
        body = response.json()
    except Exception:
        body = None

    if isinstance(body, dict):
        for key in ("error", "message", "detail"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested_msg = value.get("message") or value.get("detail") or value.get("error")
                if isinstance(nested_msg, str) and nested_msg.strip():
                    return nested_msg.strip()

    text = (getattr(response, "text", "") or "").strip()
    return text[:240] if text else "unknown provider error"


def _extract_runway_error_code(response: Any) -> str:
    try:
        body = response.json()
    except Exception:
        body = None

    if not isinstance(body, dict):
        return ""

    for key in ("code", "type", "error_code"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    error_obj = body.get("error")
    if isinstance(error_obj, dict):
        for key in ("code", "type", "error_code"):
            value = error_obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _extract_runway_task_id(body: Any) -> str | None:
    if isinstance(body, dict):
        for key in ("id", "taskId", "task_id"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _classify_openai_exception(exc: Exception) -> ProbeResult:
    message = str(exc or "").strip()
    low = message.lower()
    status_code = getattr(exc, "status_code", None)
    error_code = str(getattr(exc, "code", "") or "").strip().lower()

    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)

    if (
        "insufficient_quota" in low
        or "insufficient quota" in low
        or error_code == "insufficient_quota"
        or ("quota" in low and "rate limit" not in low)
    ):
        return ProbeResult(
            provider="openai",
            status="quota_blocked",
            reason=message or "OpenAI reports insufficient quota.",
            pipeline_live_usable_now=False,
        )

    if status_code in (401, 403) or any(
        token in low
        for token in (
            "invalid api key",
            "incorrect api key",
            "authentication",
            "unauthorized",
            "forbidden",
            "api key not found",
        )
    ):
        return ProbeResult(
            provider="openai",
            status="auth_error",
            reason=message or "OpenAI authentication failed.",
            pipeline_live_usable_now=False,
        )

    if status_code in (408, 429, 500, 502, 503, 504) or any(
        token in low
        for token in (
            "connection",
            "timeout",
            "timed out",
            "dns",
            "name resolution",
            "temporary failure",
            "network",
            "api connection",
            "service unavailable",
        )
    ):
        return ProbeResult(
            provider="openai",
            status="network_error",
            reason=message or "OpenAI network/provider request failed.",
            pipeline_live_usable_now=False,
        )

    if "max_output_tokens" in low and "expected" in low:
        return ProbeResult(
            provider="openai",
            status="unknown_error",
            reason=f"OpenAI probe request was rejected as malformed: {message}",
            pipeline_live_usable_now=False,
        )

    return ProbeResult(
        provider="openai",
        status="unknown_error",
        reason=message or "OpenAI probe failed with unknown error.",
        pipeline_live_usable_now=False,
    )


def _probe_openai() -> ProbeResult:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return ProbeResult(
            provider="openai",
            status="missing_key",
            reason="OPENAI_API_KEY is missing.",
            pipeline_live_usable_now=False,
        )

    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:
        return ProbeResult(
            provider="openai",
            status="package_missing",
            reason=f"openai package import failed: {exc}",
            pipeline_live_usable_now=False,
        )

    model = os.getenv("OPENAI_PROBE_MODEL", "").strip() or OPENAI_DEFAULT_MODEL

    try:
        client = OpenAI(api_key=key)
        # Minimal practical live request.
        client.responses.create(
            model=model,
            input="ping",
            max_output_tokens=OPENAI_MIN_OUTPUT_TOKENS,
        )
        return ProbeResult(
            provider="openai",
            status="ok",
            reason=f"Live request succeeded using model {model}.",
            pipeline_live_usable_now=True,
        )
    except Exception as exc:
        return _classify_openai_exception(exc)


def _classify_openrouter_exception(exc: Exception) -> ProbeResult:
    message = str(exc or "").strip()
    low = message.lower()
    status_code = getattr(exc, "status_code", None)
    error_code = str(getattr(exc, "code", "") or "").strip().lower()

    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)

    if status_code == 402 or ("credit" in low and ("insufficient" in low or "not enough" in low)):
        return ProbeResult(
            provider="openrouter",
            status="credit_blocked",
            reason=message or "OpenRouter reports insufficient credits.",
            pipeline_live_usable_now=False,
        )

    if (
        "insufficient_quota" in low
        or "insufficient quota" in low
        or error_code == "insufficient_quota"
        or ("quota" in low and "rate limit" not in low)
    ):
        return ProbeResult(
            provider="openrouter",
            status="quota_blocked",
            reason=message or "OpenRouter reports insufficient quota.",
            pipeline_live_usable_now=False,
        )

    if status_code in (401, 403) or any(
        token in low
        for token in (
            "invalid api key",
            "incorrect api key",
            "authentication",
            "unauthorized",
            "forbidden",
            "api key not found",
        )
    ):
        return ProbeResult(
            provider="openrouter",
            status="auth_error",
            reason=message or "OpenRouter authentication failed.",
            pipeline_live_usable_now=False,
        )

    if status_code in (408, 429, 500, 502, 503, 504) or any(
        token in low
        for token in (
            "connection",
            "timeout",
            "timed out",
            "dns",
            "name resolution",
            "temporary failure",
            "network",
            "api connection",
            "service unavailable",
        )
    ):
        return ProbeResult(
            provider="openrouter",
            status="network_error",
            reason=message or "OpenRouter network/provider request failed.",
            pipeline_live_usable_now=False,
        )

    return ProbeResult(
        provider="openrouter",
        status="unknown_error",
        reason=message or "OpenRouter probe failed with unknown error.",
        pipeline_live_usable_now=False,
    )


def _probe_openrouter() -> ProbeResult:
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        return ProbeResult(
            provider="openrouter",
            status="missing_key",
            reason="OPENROUTER_API_KEY is missing.",
            pipeline_live_usable_now=False,
        )

    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:
        return ProbeResult(
            provider="openrouter",
            status="package_missing",
            reason=f"openai package import failed: {exc}",
            pipeline_live_usable_now=False,
        )

    model = (
        os.getenv("OPENROUTER_MODEL", "").strip()
        or os.getenv("OPENROUTER_PROBE_MODEL", "").strip()
        or OPENROUTER_DEFAULT_MODEL
    )
    base_url = os.getenv("OPENROUTER_BASE_URL", "").strip() or OPENROUTER_DEFAULT_BASE_URL

    try:
        client = OpenAI(api_key=key, base_url=base_url)
        client.chat.completions.create(
            model=model,
            max_tokens=OPENROUTER_MIN_OUTPUT_TOKENS,
            temperature=0,
            messages=[
                {"role": "system", "content": "Return exactly one short word: pong."},
                {"role": "user", "content": "ping"},
            ],
        )
        return ProbeResult(
            provider="openrouter",
            status="ok",
            reason=f"Live request succeeded using model {model} @ {base_url}.",
            pipeline_live_usable_now=True,
        )
    except Exception as exc:
        return _classify_openrouter_exception(exc)


def _probe_runway() -> ProbeResult:
    runway_secret = os.getenv("RUNWAYML_API_SECRET", "").strip()
    runway_api_key = os.getenv("RUNWAY_API_KEY", "").strip()
    if not runway_secret:
        reason = "RUNWAYML_API_SECRET is missing."
        if runway_api_key:
            reason += " RUNWAY_API_KEY is set, but pipeline adapter uses RUNWAYML_API_SECRET."
        return ProbeResult(
            provider="runway",
            status="missing_key",
            reason=reason,
            pipeline_live_usable_now=False,
        )

    try:
        import requests
    except Exception as exc:
        return ProbeResult(
            provider="runway",
            status="package_missing",
            reason=f"requests package import failed: {exc}",
            pipeline_live_usable_now=False,
        )

    model = os.getenv("RUNWAY_MODEL", "").strip() or RUNWAY_DEFAULT_MODEL
    ratio = _normalize_ratio(os.getenv("RUNWAY_RATIO", ""))
    duration = _normalize_duration(os.getenv("RUNWAY_DURATION", ""))

    payload = {
        "model": model,
        "promptText": "diagnostic ping",
        "promptImage": f"data:image/png;base64,{ONE_PIXEL_PNG_BASE64}",
        "ratio": ratio,
        "duration": duration,
    }
    headers = {
        "Authorization": f"Bearer {runway_secret}",
        "X-Runway-Version": RUNWAY_VERSION,
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(RUNWAY_ENDPOINT, headers=headers, json=payload, timeout=30)
    except Exception as exc:
        return ProbeResult(
            provider="runway",
            status="network_error",
            reason=f"Runway request failed: {exc}",
            pipeline_live_usable_now=False,
        )

    if response.status_code >= 400:
        detail = _extract_runway_error_message(response)
        error_code = _extract_runway_error_code(response).lower()
        low = detail.lower()

        if (
            response.status_code == 402
            or "not enough credits" in low
            or "do not have enough credits" in low
            or "enough credits" in low
            or ("insufficient" in low and "credit" in low)
            or "insufficient_credits" in low
            or "insufficient_credit" in low
            or "credit" in error_code and "insufficient" in error_code
            or "credit_blocked" in error_code
        ):
            return ProbeResult(
                provider="runway",
                status="credit_blocked",
                reason=f"Runway credit blocked: {detail}",
                pipeline_live_usable_now=False,
            )

        if response.status_code in (401, 403) or any(
            token in low
            for token in ("invalid api key", "unauthorized", "forbidden", "authentication")
        ):
            return ProbeResult(
                provider="runway",
                status="auth_error",
                reason=f"Runway auth error: {detail}",
                pipeline_live_usable_now=False,
            )

        if response.status_code in (408, 429, 500, 502, 503, 504) or any(
            token in low
            for token in ("timeout", "timed out", "connection", "network", "temporary")
        ):
            return ProbeResult(
                provider="runway",
                status="network_error",
                reason=f"Runway network/provider error: {detail}",
                pipeline_live_usable_now=False,
            )

        return ProbeResult(
            provider="runway",
            status="unknown_error",
            reason=f"Runway live submit failed ({response.status_code}): {detail}",
            pipeline_live_usable_now=False,
        )

    try:
        body = response.json()
    except Exception:
        body = {}

    task_id = _extract_runway_task_id(body)
    if not task_id:
        return ProbeResult(
            provider="runway",
            status="unknown_error",
            reason="Runway response did not include a task id.",
            pipeline_live_usable_now=False,
        )

    return ProbeResult(
        provider="runway",
        status="ok",
        reason="Live request accepted and task id returned.",
        pipeline_live_usable_now=True,
    )


def _print_probe_block(title: str, result: ProbeResult) -> None:
    print(title)
    print(f"- status: {result.status}")
    print(f"- reason: {result.reason}")
    print(f"- pipeline live usable now: {'yes' if result.pipeline_live_usable_now else 'no'}")


def _summary(
    openai_result: ProbeResult | None,
    openrouter_result: ProbeResult | None,
    runway_result: ProbeResult | None,
) -> dict[str, Any]:
    real_images_live = bool(openai_result and openai_result.status == "ok")
    real_images_real_video_live = bool(
        openai_result
        and runway_result
        and openai_result.status == "ok"
        and runway_result.status == "ok"
    )
    script_provider_live = bool(
        (openrouter_result and openrouter_result.status == "ok")
        or (openai_result and openai_result.status == "ok")
    )
    preferred_script_provider = "fallback"
    if openrouter_result and openrouter_result.status == "ok":
        preferred_script_provider = "openrouter"
    elif openai_result and openai_result.status == "ok":
        preferred_script_provider = "openai"

    recommended_mode = "offline_safe"
    if real_images_real_video_live:
        recommended_mode = "real_images_real_video"
    elif real_images_live:
        recommended_mode = "real_images"

    return {
        "script_provider_live_usable_now": script_provider_live,
        "preferred_script_provider_now": preferred_script_provider,
        "real_images_live_usable_now": real_images_live,
        "real_images_real_video_live_usable_now": real_images_real_video_live,
        "recommended_media_mode_now": recommended_mode,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe live provider usability with minimal requests.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    parser.add_argument("--skip-openai", action="store_true", help="Skip OpenAI live probe.")
    parser.add_argument("--skip-openrouter", action="store_true", help="Skip OpenRouter live probe.")
    parser.add_argument("--skip-runway", action="store_true", help="Skip Runway live probe.")
    return parser.parse_args()


def main() -> int:
    _load_dotenv_if_available()
    args = _parse_args()

    openai_result: ProbeResult | None = None
    openrouter_result: ProbeResult | None = None
    runway_result: ProbeResult | None = None

    if not args.skip_openai:
        openai_result = _probe_openai()
    if not args.skip_openrouter:
        openrouter_result = _probe_openrouter()
    if not args.skip_runway:
        runway_result = _probe_runway()

    summary = _summary(
        openai_result=openai_result,
        openrouter_result=openrouter_result,
        runway_result=runway_result,
    )

    if args.json:
        payload: dict[str, Any] = {
            "openai": asdict(openai_result) if openai_result else {"status": "skipped"},
            "openrouter": asdict(openrouter_result) if openrouter_result else {"status": "skipped"},
            "runway": asdict(runway_result) if runway_result else {"status": "skipped"},
            "summary": summary,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if openai_result:
        _print_probe_block("OpenAI Probe", openai_result)
    else:
        print("OpenAI Probe")
        print("- status: skipped")

    print()
    if openrouter_result:
        _print_probe_block("OpenRouter Probe", openrouter_result)
    else:
        print("OpenRouter Probe")
        print("- status: skipped")

    print()
    if runway_result:
        _print_probe_block("Runway Probe", runway_result)
    else:
        print("Runway Probe")
        print("- status: skipped")

    print("\nSummary")
    print(
        "- script provider live usable now: "
        + ("yes" if summary["script_provider_live_usable_now"] else "no")
    )
    print(f"- preferred script provider now: {summary['preferred_script_provider_now']}")
    print(
        "- real_images live usable now: "
        + ("yes" if summary["real_images_live_usable_now"] else "no")
    )
    print(
        "- real_images_real_video fully live usable now: "
        + ("yes" if summary["real_images_real_video_live_usable_now"] else "no")
    )
    print(f"- recommended media mode now: {summary['recommended_media_mode_now']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
