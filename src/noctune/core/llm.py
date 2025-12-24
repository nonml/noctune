from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


def _extract_text(x: Any) -> str:
    """Best-effort extraction of text from OpenAI-style streaming delta fields."""
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        for k in ("text", "content", "value"):
            v = x.get(k)
            if isinstance(v, str) and v:
                return v
        return ""
    if isinstance(x, list):
        parts = []
        for it in x:
            t = _extract_text(it)
            if t:
                parts.append(t)
        return "".join(parts)
    return ""


def _print_stream_header(tag: str, kind: str) -> None:
    # kind: analysis|response
    t = f" {tag} " if tag else " "
    sys.stdout.write(f"\n[{kind}]{t}\n")
    sys.stdout.flush()


@dataclass
class LLMClient:
    base_url: str
    api_key: str = ""
    model: str = ""
    timeout_s: int = 120
    extra_headers: dict[str, str] | None = None
    request_overrides: dict[str, Any] | None = None
    mode: str = "openai_chat"  # openai_chat

    # Local streaming controls (do not affect server behavior unless stream=True payload is used)
    stream_default: bool = False
    stream_print_reasoning: bool = True
    stream_print_headers: bool = True

    def chat(
        self,
        system: str,
        user: str,
        *,
        stream: bool | None = None,
        verbose: bool = False,
        tag: str = "",
    ) -> tuple[bool, str]:
        if self.mode != "openai_chat":
            return False, f"Unsupported LLM mode: {self.mode}"
        url = self.base_url.rstrip("/") + "/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.extra_headers:
            headers.update(self.extra_headers)

        payload: dict[str, Any] = {
            "model": self.model or None,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # drop None model to let server default
        if payload["model"] is None:
            payload.pop("model", None)

        if self.request_overrides:
            # shallow merge
            payload.update(self.request_overrides)

        do_stream = self.stream_default if stream is None else bool(stream)
        if do_stream:
            payload["stream"] = True

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                content_type = (resp.headers.get("Content-Type") or "").lower()
                if do_stream and "text/event-stream" in content_type:
                    ok, txt = self._read_stream(resp, verbose=verbose, tag=tag)
                    return ok, txt
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return (
                False,
                f"HTTPError {e.code}: {e.read().decode('utf-8', errors='replace')}",
            )
        except Exception as e:
            return False, f"LLM request failed: {e}"

        try:
            obj = json.loads(raw)
            choice = obj["choices"][0]
            content = choice["message"]["content"]
            if do_stream and verbose:
                # Server did not stream; still print the final content for operator visibility.
                if self.stream_print_headers:
                    _print_stream_header(tag, "response")
                sys.stdout.write(content)
                sys.stdout.write("\n")
                sys.stdout.flush()
            return True, content
        except Exception:
            return False, f"Could not parse LLM response as OpenAI chat: {raw[:1000]}"

    def _read_stream(self, resp, *, verbose: bool, tag: str) -> tuple[bool, str]:
        """Parse OpenAI-compatible SSE stream, print deltas if verbose, return collected content."""
        analysis_header_printed = False
        response_header_printed = False

        parts: list[str] = []
        total_chars = 0

        # Read line-by-line; OpenAI streams send `data: <json>` lines separated by blank lines.
        while True:
            line_b = resp.readline()
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if not line.startswith("data:"):
                continue

            data_str = line[len("data:") :].strip()
            if data_str == "[DONE]":
                break

            try:
                chunk = json.loads(data_str)
            except Exception:
                # ignore malformed chunk; keep going
                continue

            if not chunk or not isinstance(chunk, dict):
                continue
            if "error" in chunk:
                return False, str(chunk.get("error"))[:2000]
            if not chunk.get("choices"):
                continue

            try:
                delta = chunk["choices"][0].get("delta") or {}
            except Exception:
                continue

            # Reasoning (if present) -> print only
            reasoning_field = (
                delta.get("reasoning_content") if isinstance(delta, dict) else None
            )
            reasoning_text = _extract_text(reasoning_field)
            if reasoning_text and verbose and self.stream_print_reasoning:
                if self.stream_print_headers and not analysis_header_printed:
                    _print_stream_header(tag, "analysis")
                    analysis_header_printed = True
                sys.stdout.write(reasoning_text)
                sys.stdout.flush()

            # Content -> print + collect
            content_field = delta.get("content") if isinstance(delta, dict) else None
            content_text = _extract_text(content_field)
            if content_text:
                if verbose:
                    if self.stream_print_headers and not response_header_printed:
                        _print_stream_header(tag, "response")
                        response_header_printed = True
                    sys.stdout.write(content_text)
                    sys.stdout.flush()
                parts.append(content_text)
                total_chars += len(content_text)

        # Ensure final newline separation if we printed content
        if verbose and response_header_printed:
            sys.stdout.write("\n")
            sys.stdout.flush()

        return True, "".join(parts)
