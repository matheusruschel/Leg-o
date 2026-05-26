from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any, Optional

import anthropic

from .token_tracker import CallType, TokenTracker

log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class ClaudeClient:
    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        tracker: TokenTracker | None = None,
        sdk_client: Any | None = None,
        rate_limit_attempts: int = 3,
        overload_wait_seconds: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.tracker = tracker or TokenTracker(model=model)
        self._client = sdk_client or anthropic.Anthropic(api_key=api_key)
        self._rate_limit_attempts = rate_limit_attempts
        self._overload_wait_seconds = overload_wait_seconds

    def call(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        call_type: CallType = "other",
        system: Optional[str] = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system is not None:
            kwargs["system"] = system

        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._client.messages.create(**kwargs)
            except anthropic.RateLimitError as e:
                if attempt >= self._rate_limit_attempts:
                    raise
                delay = (2 ** (attempt - 1)) + random.random()
                log.warning("rate limited (attempt %d): sleeping %.2fs", attempt, delay)
                time.sleep(delay)
                continue
            except getattr(anthropic, "APIStatusError", Exception) as e:
                if _is_overloaded(e) and attempt < self._rate_limit_attempts:
                    log.warning("overloaded (attempt %d): sleeping %.1fs", attempt, self._overload_wait_seconds)
                    time.sleep(self._overload_wait_seconds)
                    continue
                raise

            self._record_usage(resp, call_type)
            return _extract_text(resp)

    def call_json(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        call_type: CallType = "analysis",
        system: Optional[str] = None,
    ) -> Any:
        raw = self.call(messages, max_tokens=max_tokens, call_type=call_type, system=system)
        try:
            return _parse_json_lenient(raw)
        except ValueError:
            log.info("JSON parse failed; asking Claude to fix it")
            fix_messages = [
                {
                    "role": "user",
                    "content": (
                        "Your previous response was not valid JSON. "
                        "Return ONLY the corrected JSON, with no markdown fences "
                        "and no preamble. Original response:\n\n" + raw
                    ),
                },
            ]
            fixed = self.call(fix_messages, max_tokens=max_tokens, call_type="fix")
            return _parse_json_lenient(fixed)

    def call_dry_run(self, messages: list[dict], **_: Any) -> None:
        log.info("dry-run prompt: %s", messages)
        return None

    def _record_usage(self, resp: Any, call_type: CallType) -> None:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        self.tracker.record(
            call_type,
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
        )


def _extract_text(resp: Any) -> str:
    content = getattr(resp, "content", None)
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(text)
    return "".join(parts)


def _is_overloaded(err: Exception) -> bool:
    status = getattr(err, "status_code", None)
    if status == 529:
        return True
    msg = str(err).lower()
    return "overloaded" in msg


def _parse_json_lenient(text: str) -> Any:
    candidates = [text, _strip_fences(text), _extract_braced(text)]
    last_err: Exception | None = None
    for cand in candidates:
        if not cand:
            continue
        try:
            return json.loads(cand)
        except json.JSONDecodeError as e:
            last_err = e
            continue
    raise ValueError(f"could not parse JSON: {last_err}")


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if "```" not in stripped:
        return stripped
    parts = stripped.split("```")
    # Pattern: ["preamble", "json\n{...}", "trailing"] — pick the longest middle chunk
    middles = [p for p in parts[1::2]]
    if not middles:
        return stripped
    candidate = max(middles, key=len)
    # Drop optional "json" language tag on the first line
    candidate = re.sub(r"^json\s*\n", "", candidate, count=1, flags=re.IGNORECASE)
    return candidate.strip()


def _extract_braced(text: str) -> str:
    # Find the largest plausible JSON object or array span
    starts = [i for i, c in enumerate(text) if c in "{["]
    ends = [i for i, c in enumerate(text) if c in "}]"]
    if not starts or not ends:
        return ""
    return text[starts[0] : ends[-1] + 1]
