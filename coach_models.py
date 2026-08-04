"""Validated coach model catalog shared by intake, runtime, and the UI."""

from copy import deepcopy


OPENAI_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")

MODEL_CATALOG = (
    {
        "provider": "openai",
        "providerLabel": "OpenAI",
        "id": "gpt-5.6-sol",
        "label": "GPT-5.6 Sol",
        "description": "Frontier capability for the hardest planning and analysis.",
        "efforts": OPENAI_EFFORTS,
        "defaultEffort": "high",
    },
    {
        "provider": "openai",
        "providerLabel": "OpenAI",
        "id": "gpt-5.6-terra",
        "label": "GPT-5.6 Terra",
        "description": "Strong quality with lower latency and cost.",
        "efforts": OPENAI_EFFORTS,
        "defaultEffort": "medium",
    },
    {
        "provider": "openai",
        "providerLabel": "OpenAI",
        "id": "gpt-5.6-luna",
        "label": "GPT-5.6 Luna",
        "description": "Fastest GPT-5.6 option for routine coach turns.",
        "efforts": OPENAI_EFFORTS,
        "defaultEffort": "low",
    },
    {
        "provider": "anthropic",
        "providerLabel": "Anthropic",
        "id": "claude-fable-5",
        "label": "Claude Fable 5",
        "description": "Highest-capability Claude for long, difficult work.",
        "efforts": CLAUDE_EFFORTS,
        "defaultEffort": "high",
    },
    {
        "provider": "anthropic",
        "providerLabel": "Anthropic",
        "id": "claude-opus-5",
        "label": "Claude Opus 5",
        "description": "Deep agentic planning with measured latency.",
        "efforts": CLAUDE_EFFORTS,
        "defaultEffort": "medium",
    },
    {
        "provider": "anthropic",
        "providerLabel": "Anthropic",
        "id": "claude-sonnet-5",
        "label": "Claude Sonnet 5",
        "description": "Fast, capable Claude for everyday coaching.",
        "efforts": CLAUDE_EFFORTS,
        "defaultEffort": "medium",
    },
    {
        "provider": "anthropic",
        "providerLabel": "Anthropic",
        "id": "claude-haiku-4-5",
        "label": "Claude Haiku 4.5",
        "description": "Lowest-latency Claude for simple questions.",
        "efforts": CLAUDE_EFFORTS,
        "defaultEffort": "low",
    },
)

DEFAULT_SELECTION = {
    "provider": "anthropic",
    "model": "claude-opus-5",
    "effort": "medium",
}

LEGACY_SELECTION = {
    "provider": "anthropic",
    "model": "claude-opus-5",
    "effort": "medium",
}


def public_catalog():
    return [
        {
            **{key: value for key, value in item.items() if key != "efforts"},
            "efforts": list(item["efforts"]),
        }
        for item in MODEL_CATALOG
    ]


def normalize_selection(value=None, *, default=None):
    fallback = deepcopy(default or DEFAULT_SELECTION)
    if value is None:
        value = fallback
    if not isinstance(value, dict):
        raise ValueError("model selection must be an object")

    provider = str(value.get("provider") or "").strip().lower()
    model = str(value.get("model") or "").strip().lower()
    effort = str(value.get("effort") or "").strip().lower()
    match = next(
        (
            item
            for item in MODEL_CATALOG
            if item["provider"] == provider and item["id"] == model
        ),
        None,
    )
    if not match:
        raise ValueError("unsupported coach model")
    if not effort:
        effort = match["defaultEffort"]
    if effort not in match["efforts"]:
        raise ValueError(f"unsupported reasoning level for {match['label']}")
    return {"provider": provider, "model": model, "effort": effort}


def selection_label(selection):
    selected = normalize_selection(selection)
    item = next(
        entry
        for entry in MODEL_CATALOG
        if entry["provider"] == selected["provider"]
        and entry["id"] == selected["model"]
    )
    return f"{item['label']} · {selected['effort']}"
