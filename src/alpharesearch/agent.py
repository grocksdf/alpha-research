from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openai import OpenAI

from .expression import ExpressionValidator
from .memory import ResearchMemory
from .models import Candidate


class ResearchAgentError(RuntimeError):
    pass


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Read recent successes, failures, and formula families.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_field_catalog",
            "description": "Read the allowed BRAIN data-field identifiers.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_expression",
            "description": "Deterministically parse and validate one FASTEXPR candidate.",
            "parameters": {
                "type": "object",
                "required": ["expression"],
                "properties": {"expression": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_candidates",
            "description": "Finish the research turn with proposed candidates.",
            "parameters": {
                "type": "object",
                "required": ["candidates"],
                "properties": {
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["expression", "hypothesis", "family"],
                            "properties": {
                                "expression": {"type": "string"},
                                "hypothesis": {"type": "string"},
                                "family": {"type": "string"},
                                "rationale": {"type": "string"},
                                "parent_id": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                                "parameters": {"type": "object"},
                            },
                        },
                    }
                },
            },
        },
    },
]


class ResearchAgent:
    def __init__(
        self,
        model: str,
        validator: ExpressionValidator,
        memory: ResearchMemory,
        field_catalog: list[str],
        program_path: str | Path = "program.md",
        client: OpenAI | None = None,
    ):
        self.model = model
        self.validator = validator
        self.memory = memory
        self.field_catalog = sorted(set(field_catalog))
        self.program = Path(program_path).read_text(encoding="utf-8")
        self.client = client or OpenAI()

    def propose(
        self,
        count: int = 5,
        paper_notes: str = "",
        focus: str = "",
        max_tool_rounds: int = 10,
    ) -> list[Candidate]:
        system = (
            self.program
            + "\n\nYou are the only probabilistic component. Validators, simulations, and "
            "evaluation are deterministic external authorities. Use tools before proposing. "
            "Never invent platform results. Finish by calling submit_candidates exactly once."
        )
        user = (
            f"Propose up to {count} diverse candidates. Focus: {focus or 'open exploration'}.\n"
            f"Paper/research notes:\n{paper_notes or '(none supplied)'}"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        final: list[Candidate] | None = None

        handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "search_memory": lambda args: self.memory.recent_memory(
                min(int(args.get("limit", 20)), 50)
            ),
            "get_field_catalog": lambda args: self.field_catalog,
            "validate_expression": lambda args: self.validator.validate(
                str(args["expression"])
            ).to_dict(),
        }

        for _ in range(max_tool_rounds):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
            message = response.choices[0].message
            messages.append(message.model_dump(exclude_none=True))
            if not message.tool_calls:
                messages.append(
                    {
                        "role": "user",
                        "content": "Use the tools and finish with submit_candidates.",
                    }
                )
                continue
            for call in message.tool_calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                    if name == "submit_candidates":
                        final = [
                            Candidate(
                                expression=str(item["expression"]),
                                hypothesis=str(item["hypothesis"]),
                                family=str(item["family"]),
                                rationale=str(item.get("rationale", "")),
                                parent_id=item.get("parent_id"),
                                parameters=dict(item.get("parameters") or {}),
                            )
                            for item in args.get("candidates", [])[:count]
                        ]
                        output: Any = {"accepted": len(final)}
                    else:
                        output = handlers[name](args)
                except Exception as exc:  # noqa: BLE001 - return tool failures to the model
                    output = {"error": f"{type(exc).__name__}: {exc}"}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(output, ensure_ascii=False),
                    }
                )
            if final is not None:
                return final
        raise ResearchAgentError("model did not submit candidates within the tool-round limit")
