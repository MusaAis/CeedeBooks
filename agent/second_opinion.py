"""A second opinion from the language model, recorded beside the rules' verdict (Phase E3).

The rules verdict is the one that acts. The model sees only facts computed by code, answers within a short timeout, and its
answer is stored (and hashed) next to the rules' verdict. It never releases, blocks or delays a payment beyond the timeout.
No model, a timeout, an error or an unusable answer all mean model_action = None and agreed = None: never counted as agreement.
"""
import asyncio
import json
import logging
import re
from typing import Optional

from agent import llm
from agent.config import config

log = logging.getLogger(__name__)

ACTIONS = ("pay", "hold", "escalate")
TIMEOUT_SECONDS = 3.0
MAX_REASONING = 300
WITHHELD = "(model text withheld: it contained figures the code did not provide)"

_SYSTEM = (
    "You review one accounts-payable decision for CeedeBooks. You are given facts computed by code. Reply with JSON only: "
    '{"action": "pay" or "hold" or "escalate", "reasoning": "one or two sentences", "confidence": a number from 0 to 1}. '
    "Use only the facts given. Never write amounts, counts, dates or any other figures in your reasoning. "
    "Your answer is recorded next to the rules' verdict and never releases a payment."
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text: str) -> set:
    return set(_NUMBER.findall(text))


def clean_reasoning(text, facts: dict) -> str:
    """Words only (rule 18): text with a figure that is not in the facts is replaced, never stored."""
    one_line = " ".join(str(text or "").split())[:MAX_REASONING]
    if not _numbers(one_line) <= _numbers(json.dumps(facts, sort_keys=True)):
        return WITHHELD
    return one_line


def parse(content: str, facts: dict) -> Optional[dict]:
    """The model's reply as {action, reasoning, confidence}, or None if it is not a usable answer."""
    match = re.search(r"\{.*\}", content or "", re.S)
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
        action = str(raw["action"]).strip().lower()
        confidence = raw["confidence"]
    except (ValueError, KeyError, TypeError):
        return None
    if action not in ACTIONS or isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return None
    return {"action": action, "reasoning": clean_reasoning(raw.get("reasoning"), facts), "confidence": round(float(confidence), 4)}


async def ask_model(facts: dict) -> Optional[dict]:
    """The model's opinion on these facts, or None. Never raises."""
    if not config.model_compare:
        return None
    try:
        content = await asyncio.wait_for(llm.complete(_SYSTEM, json.dumps(facts, sort_keys=True)), TIMEOUT_SECONDS)
    except Exception:
        log.warning("The model gave no second opinion (timeout or error); recorded as not compared")
        return None
    opinion = parse(content, facts)
    if opinion is not None:
        opinion["model"] = config.groq_llm_model
    return opinion


def comparison(rules_action: str, opinion: Optional[dict]) -> dict:
    """What goes into the hash (version 3). agreed is None unless a model answered."""
    if opinion is None:
        return {"rules_action": rules_action, "model_action": None, "model_confidence": None,
                "model_name": None, "model_reasoning": None, "agreed": None}
    return {"rules_action": rules_action, "model_action": opinion["action"], "model_confidence": opinion["confidence"],
            "model_name": opinion["model"], "model_reasoning": opinion["reasoning"], "agreed": opinion["action"] == rules_action}
