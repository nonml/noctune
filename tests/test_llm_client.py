from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeDelta:
    def __init__(self, content: str = "", reasoning_content: str = "") -> None:
        self.content = content
        self.reasoning_content = reasoning_content


class _FakeChoice:
    def __init__(
        self, message: _FakeMessage | None = None, delta: _FakeDelta | None = None
    ) -> None:
        self.message = message
        self.delta = delta


class _FakeChatCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(message=_FakeMessage(content))]


class _FakeChatCompletionChunk:
    def __init__(self, content: str = "", reasoning: str = "") -> None:
        self.choices = [
            _FakeChoice(
                delta=_FakeDelta(content=content, reasoning_content=reasoning)
            )
        ]


class _FakeOpenAI:
    def __init__(self, *, api_key: str, base_url: str, timeout: int, default_headers=None) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.default_headers = default_headers
        self.chat = self._Chat()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _FakeOpenAI._Completions()

    class _Completions:
        def __init__(self) -> None:
            self._mode = "nonstream"

        def set_mode(self, mode: str) -> None:
            self._mode = mode

        def create(self, *, model: str, messages, stream: bool, **kwargs):
            if stream:
                # Yield multiple chunks; caller should concatenate.
                return iter(
                    [
                        _FakeChatCompletionChunk(reasoning="think"),
                        _FakeChatCompletionChunk(content="hello"),
                        _FakeChatCompletionChunk(content=" world"),
                    ]
                )
            return _FakeChatCompletion("ok")


class TestLLMClient(unittest.TestCase):
    def test_chat_non_stream_extracts_message_content(self) -> None:
        from noctune.core import llm as llm_mod

        with mock.patch.object(llm_mod, "OpenAI", _FakeOpenAI):
            c = llm_mod.LLMClient(
                base_url="http://localhost:1234/v1",
                api_key="local",
                model="fake-model",
                stream_default=False,
            )
            ok, out = c.chat(system="s", user="u", stream=False)
            self.assertTrue(ok)
            self.assertEqual(out, "ok")

    def test_chat_stream_concatenates_chunks(self) -> None:
        from noctune.core import llm as llm_mod

        with mock.patch.object(llm_mod, "OpenAI", _FakeOpenAI):
            c = llm_mod.LLMClient(
                base_url="http://localhost:1234/v1",
                api_key="local",
                model="fake-model",
                stream_default=True,
            )
            ok, out = c.chat(system="s", user="u", stream=True)
            self.assertTrue(ok)
            self.assertEqual(out, "hello world")


if __name__ == "__main__":
    unittest.main()
