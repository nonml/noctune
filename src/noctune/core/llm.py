from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

try:
    # Optional at import time so unit tests (and non-OpenAI backends) can run
    # without requiring the openai package.
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


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


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _as_dict(obj: Any) -> dict[str, Any] | None:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    dump = _get_attr(obj, "model_dump")
    if callable(dump):
        try:
            v = dump()
            return v if isinstance(v, dict) else None
        except Exception:
            return None
    return None


def _extract_completion_content(resp: Any) -> str:
    """
    Extract `choices[0].message.content` from an OpenAI ChatCompletion-like response.
    Supports both SDK objects and dicts.
    """
    d = _as_dict(resp)
    if d is not None:
        choices = d.get("choices") or []
        if choices:
            msg = (choices[0] or {}).get("message") or {}
            return _extract_text(msg.get("content"))
        return ""

    choices = _get_attr(resp, "choices") or []
    if not choices:
        return ""
    msg = _get_attr(choices[0], "message")
    return _extract_text(_get_attr(msg, "content"))


def _iter_stream_deltas(chunk: Any) -> tuple[str, str]:
    """
    Return (reasoning_text, content_text) from an OpenAI ChatCompletionChunk-like chunk.
    Supports both SDK objects and dicts.
    """
    d = _as_dict(chunk)
    if d is not None:
        choices = d.get("choices") or []
        if not choices:
            return "", ""
        delta = (choices[0] or {}).get("delta") or {}
        if not isinstance(delta, dict):
            return "", ""
        reasoning_text = _extract_text(delta.get("reasoning_content"))
        content_text = _extract_text(delta.get("content"))
        return reasoning_text, content_text

    choices = _get_attr(chunk, "choices") or []
    if not choices:
        return "", ""
    delta = _get_attr(choices[0], "delta")
    reasoning_text = _extract_text(_get_attr(delta, "reasoning_content"))
    content_text = _extract_text(_get_attr(delta, "content"))
    return reasoning_text, content_text


@dataclass
class LLMClient:
    base_url: str
    api_key: str
    model: str | None
    timeout_s: int = 180
    extra_headers: dict[str, str] | None = None
    request_overrides: dict[str, Any] | None = None
    stream_default: bool = False
    stream_print_reasoning: bool = True
    stream_print_headers: bool = True

    def __post_init__(self) -> None:
        if OpenAI is None:
            raise ModuleNotFoundError(
                "openai package not installed. Install dependencies or use a compatible backend that provides an OpenAI SDK. "
                "Try: pip install openai"
            )
        self._client = OpenAI(
            api_key=self.api_key or "local",
            base_url=self.base_url,
            timeout=self.timeout_s,
            default_headers=self.extra_headers or None,
        )

    def chat(
        self,
        system: str,
        user: str,
        *,
        stream: bool | None = None,
        verbose: bool = False,
        tag: str = "",
    ) -> tuple[bool, str]:
        do_stream = self.stream_default if stream is None else bool(stream)

        # Always set a model string. Many OpenAI-compatible servers behave poorly
        # if model is omitted.
        model = (self.model or "").strip()
        if not model:
            return False, "LLMClient.model is empty; set [tool.noctune.llm].model"

        payload: dict[str, Any] = {}
        if self.request_overrides:
            # shallow merge
            payload.update(self.request_overrides)

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        try:
            if do_stream:
                stream_iter = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=True,
                    **payload,
                )
                analysis_header_printed = False
                response_header_printed = False
                parts: list[str] = []

                for chunk in stream_iter:
                    reasoning_text, content_text = _iter_stream_deltas(chunk)

                    if reasoning_text and verbose and self.stream_print_reasoning:
                        if self.stream_print_headers and not analysis_header_printed:
                            _print_stream_header(tag, "analysis")
                            analysis_header_printed = True
                        sys.stdout.write(reasoning_text)
                        sys.stdout.flush()

                    if content_text:
                        if verbose:
                            if (
                                self.stream_print_headers
                                and not response_header_printed
                            ):
                                _print_stream_header(tag, "response")
                                response_header_printed = True
                            sys.stdout.write(content_text)
                            sys.stdout.flush()
                        parts.append(content_text)

                if verbose and response_header_printed:
                    sys.stdout.write("\n")
                    sys.stdout.flush()

                return True, "".join(parts)
            resp = self._client.chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
                **payload,
            )
            content = _extract_completion_content(resp)
            if do_stream and verbose:
                # Server did not stream; still print the final content for operator visibility.
                if self.stream_print_headers:
                    _print_stream_header(tag, "response")
                sys.stdout.write(content)
                sys.stdout.write("\n")
                sys.stdout.flush()
            return True, content
        except Exception as e:
            return False, f"LLM request failed (openai sdk): {e}"
