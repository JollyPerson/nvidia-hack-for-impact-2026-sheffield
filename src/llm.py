"""Single gateway to the models (see AGENTS.md). Every model call goes through here.

Owns: the model-server client, role -> model mapping, the 8k token budget, and
JSON output enforcement for verdicts.

    llm = LLM()
    msg = llm.chat("screener", messages, tools=TOOL_SCHEMAS)   # one tool-loop step
    v = llm.verdict("verifier", messages)                      # always returns a Verdict
"""

from __future__ import annotations

import copy
import json
import logging
import re

import openai
from pydantic import ValidationError

import config
from src.models import Verdict, VerdictValue

log = logging.getLogger(__name__)

# JSON schema the server must follow for a verdict. Written out flat (no $refs)
# so Ollama's grammar converter handles it. Mirrors Verdict / Citation in models.py.
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": [v.value for v in VerdictValue]},
        "reasoning": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "record_id": {"type": "string"},
                    "field": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["table", "record_id", "field", "quote"],
            },
        },
    },
    "required": ["verdict", "reasoning", "citations"],
}

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "verdict", "schema": VERDICT_SCHEMA},
}

TRUNCATED = " …[truncated]"


class PromptTooLong(Exception):
    """The prompt cannot be cut down to fit the 8k token limit."""


def estimate_tokens(messages: list[dict], tools: list[dict] | None = None) -> int:
    chars = len(json.dumps(messages, ensure_ascii=False))
    if tools:
        chars += len(json.dumps(tools, ensure_ascii=False))
    return int(chars / config.CHARS_PER_TOKEN) + 1


def fit_to_budget(messages: list[dict], tools: list[dict] | None = None) -> list[dict]:
    """Return a copy of `messages` that fits the per-request token limit.

    Only tool results are shortened (largest first); the system prompt,
    criterion and the model's own turns are never cut.
    """
    budget = config.CONTEXT_TOKENS - config.MAX_OUTPUT_TOKENS
    msgs = copy.deepcopy(messages)
    while estimate_tokens(msgs, tools) > budget:
        tool_msgs = [m for m in msgs if m.get("role") == "tool" and len(m["content"]) > 200]
        if not tool_msgs:
            raise PromptTooLong(f"~{estimate_tokens(msgs, tools)} tokens, budget {budget}")
        biggest = max(tool_msgs, key=lambda m: len(m["content"]))
        text = biggest["content"].removesuffix(TRUNCATED)
        biggest["content"] = text[: len(text) // 2] + TRUNCATED
    return msgs


def parse_verdict(text: str | None) -> Verdict:
    """Parse model text into a Verdict. Raises ValueError / ValidationError."""
    if not text:
        raise ValueError("empty response")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)  # stray reasoning
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in response")
    return Verdict.model_validate_json(text[start : end + 1])


class LLM:
    def __init__(self, client=None):
        # `client` lets tests pass a fake with .chat.completions.create().
        self.client = client or openai.OpenAI(
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            timeout=config.LLM_TIMEOUT_SECONDS,
        )
        # Optional request fields we drop for good if the server rejects them.
        self._send_reasoning_effort = bool(config.REASONING_EFFORT)
        self._schema_supported = True

    # -- low level -------------------------------------------------------------

    def _create(self, role: str, messages: list[dict], **extra):
        kwargs = dict(
            model=config.MODELS[role],
            messages=messages,
            temperature=config.TEMPERATURE,
            seed=config.SEED,
            max_tokens=config.MAX_OUTPUT_TOKENS,
            **extra,
        )
        if self._send_reasoning_effort:
            kwargs["reasoning_effort"] = config.REASONING_EFFORT
        if "response_format" in kwargs and not self._schema_supported:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            return self.client.chat.completions.create(**kwargs)
        except openai.BadRequestError as e:
            msg = str(e).lower()
            if self._send_reasoning_effort and "reasoning" in msg:
                log.warning("server rejected reasoning_effort; not sending it again")
                self._send_reasoning_effort = False
                return self._create(role, messages, **extra)
            if self._schema_supported and ("response_format" in msg or "schema" in msg):
                log.warning("server rejected json_schema; falling back to json_object")
                self._schema_supported = False
                return self._create(role, messages, **extra)
            raise

    # -- public ------------------------------------------------------------------

    def chat(self, role: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """One tool-loop step. Returns the assistant message as a plain dict:
        {"role": "assistant", "content": str, "tool_calls": [...]}"""
        msgs = fit_to_budget(messages, tools)
        extra = {"tools": tools, "tool_choice": "auto"} if tools else {}
        resp = self._create(role, msgs, **extra)
        m = resp.choices[0].message
        out = {"role": "assistant", "content": m.content or ""}
        if m.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                }
                for tc in m.tool_calls
            ]
        return out

    def verdict(self, role: str, messages: list[dict]) -> Verdict:
        """Ask for a JSON verdict. Retries once on invalid output; on a second
        failure returns `insufficient` (never a guess)."""
        msgs = list(messages)
        error = ""
        for attempt in (1, 2):
            try:
                resp = self._create(role, fit_to_budget(msgs), response_format=RESPONSE_FORMAT)
            except PromptTooLong as e:
                return _fallback(f"prompt too long: {e}")
            text = resp.choices[0].message.content
            try:
                return parse_verdict(text)
            except (ValueError, ValidationError) as e:
                error = str(e).splitlines()[0][:200]
                log.info("invalid verdict from %s (attempt %d): %s", role, attempt, error)
                msgs = msgs + [
                    {"role": "assistant", "content": (text or "")[:1500]},
                    {
                        "role": "user",
                        "content": f"That was not a valid verdict ({error}). Reply with ONLY the JSON "
                        "object. A met or not_met verdict must have at least one citation.",
                    },
                ]
        return _fallback(f"model output invalid twice: {error}")


def _fallback(reason: str) -> Verdict:
    return Verdict(verdict=VerdictValue.insufficient, reasoning=reason, citations=[])
