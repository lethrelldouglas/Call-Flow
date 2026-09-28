"""Thin wrapper around NVIDIA Nemotron models served by Nebius Token Factory.

Token Factory speaks the OpenAI chat-completions protocol, so the official
`openai` package is all we need. Two things are Nemotron-specific:

* Reasoning comes back in a separate `reasoning` field, never inside the
  content, so the content can be used as-is.
* Nemotron 3 Nano accepts `chat_template_kwargs: {"enable_thinking": false}`,
  which turns reasoning off for cheap, fast classification calls.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict

from openai import OpenAI

import settings

log = logging.getLogger("frontdesk.llm")

_client: OpenAI | None = None
_THINK_TAGS = re.compile(r"<think>.*?</think>\s*", re.S)


class Usage:
    """Token counts per model for this process (demo.py prints them)."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = defaultdict(int)
        self.prompt_tokens: dict[str, int] = defaultdict(int)
        self.completion_tokens: dict[str, int] = defaultdict(int)

    def add(self, model: str, prompt: int, completion: int) -> None:
        self.calls[model] += 1
        self.prompt_tokens[model] += prompt
        self.completion_tokens[model] += completion

    def summary(self) -> str:
        lines = [
            f"{model}: {self.calls[model]} call(s), "
            f"{self.prompt_tokens[model]} tokens in / {self.completion_tokens[model]} out"
            for model in self.calls
        ]
        return "\n".join(lines) or "no model calls made"


USAGE = Usage()


def client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.NEBIUS_API_KEY:
            raise RuntimeError("NEBIUS_API_KEY is missing. Paste it into .env next to settings.py.")
        _client = OpenAI(base_url=settings.TOKEN_FACTORY_URL, api_key=settings.NEBIUS_API_KEY)
    return _client


def chat(
    model: str,
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 1500,
    json_mode: bool = False,
    thinking: bool = True,
) -> str:
    """One chat completion. Returns the text content with any reasoning stripped."""
    kwargs: dict = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if not thinking:
        kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

    response = client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )
    choice = response.choices[0]
    text = _THINK_TAGS.sub("", choice.message.content or "").strip()

    if response.usage:
        USAGE.add(model, response.usage.prompt_tokens, response.usage.completion_tokens)
    if choice.finish_reason == "length":
        log.warning("%s hit max_tokens=%s; the output may be cut off", model, max_tokens)
    if not text:
        raise RuntimeError(f"{model} returned an empty reply (finish_reason={choice.finish_reason})")
    return text


def chat_json(model: str, system: str, user: str, **kwargs) -> dict:
    """Like chat(), but parses the reply as a JSON object."""
    text = chat(model, system, user, json_mode=True, **kwargs)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the first {...} block, in case the model added prose or fences.
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError(f"{model} did not return JSON: {text[:200]!r}")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError(f"{model} returned JSON that is not an object: {text[:200]!r}")
    return data
