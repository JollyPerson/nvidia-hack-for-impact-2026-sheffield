"""A scripted stand-in for the model server, so tests run without the Spark.

It imitates the OpenAI client shape: fake.chat.completions.create(**kwargs).
Every request is recorded in fake.requests so tests can inspect prompts.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace


def response(content=None, tool_calls=None):
    calls = [
        SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))
        for i, (name, args) in enumerate(tool_calls or [])
    ]
    msg = SimpleNamespace(content=content, tool_calls=calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class ScriptedClient:
    """Returns the queued responses in order (for unit tests of llm.py)."""

    def __init__(self, responses):
        self.queue = list(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RuleBasedClient:
    """A tiny 'model' that searches notes then judges with keyword rules.

    It reads only what the tools returned, so it exercises the real tool loop,
    tool scoping and citation shapes end to end.
    """

    def __init__(self):
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        msgs = kwargs["messages"]
        criterion = next(m["content"] for m in msgs if m["role"] == "user")
        oxygen = "oxygen" in criterion.lower()
        tool_results = [json.loads(m["content"]) for m in msgs if m["role"] == "tool"]

        if "tools" in kwargs:
            if not tool_results:
                query = "home oxygen O2 LTOT COPD lung resp" if oxygen else "CXR consolidation"
                return response(tool_calls=[("search_notes", {"query": query})])
            return response(content="done")

        notes = [n for r in tool_results for n in r.get("notes", [])]
        return response(content=json.dumps(self._judge(notes, oxygen)))

    @staticmethod
    def _judge(notes, oxygen):
        def cite(note, pattern):
            m = re.search(pattern, note["NoteText"], flags=re.IGNORECASE)
            return {"table": "ClinicalNotes", "record_id": note["NoteID"], "field": "NoteText", "quote": m.group(0)}

        for n in notes:
            t = n["NoteText"]
            if oxygen:
                if re.search(r"not on home oxygen", t, re.I):
                    return {"verdict": "not_met", "reasoning": "explicitly not on home O2",
                            "citations": [cite(n, r"not on home oxygen")]}
                if re.search(r"home O2", t, re.I):
                    return {"verdict": "met", "reasoning": "on home O2",
                            "citations": [cite(n, r"home O2[^.]*")]}
                if re.search(r"no (known lung disease|other resp hx)", t, re.I):
                    return {"verdict": "not_met", "reasoning": "no lung disease",
                            "citations": [cite(n, r"no (known lung disease|other resp hx)")]}
            else:
                if re.search(r"\?subtle", t):
                    return {"verdict": "insufficient", "reasoning": "X-ray equivocal",
                            "citations": [cite(n, r"\?subtle[^,]*")]}
                if re.search(r"consolidation", t, re.I):
                    return {"verdict": "met", "reasoning": "consolidation on CXR",
                            "citations": [cite(n, r"[^.:]*consolidation[^.]*")]}
        return {"verdict": "insufficient", "reasoning": "nothing relevant in records", "citations": []}
