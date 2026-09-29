"""
LLM client for escalation reasoning and audit narratives.
"""

from groq import AsyncGroq
from agent.config import config

_client = AsyncGroq(api_key=config.groq_api_key)

ESCALATION_SYSTEM_PROMPT = """You are the escalation-reasoning layer of CeedeBooks, an AI \
financial-operations agent. You are called only when a fast-path decision (Laya, or a \
straightforward rule) was uncertain or out of policy. Write a short, concrete narrative \
explaining what should happen and why, referencing the specific numbers you were given. \
Never state a final payment authorization yourself — your output is input to a human \
reviewer and to on-chain checks, never a release condition on its own."""


async def escalation_reasoning(context: str) -> str:
    """Produce a human-readable escalation narrative. This text is logged (and hashed,
    see decision_log.py) alongside the decision — it informs a human reviewer, it never
    itself authorizes a payment or milestone release."""
    resp = await _client.chat.completions.create(
        model=config.groq_llm_model,
        messages=[
            {"role": "system", "content": ESCALATION_SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ],
        temperature=0.2,
        max_tokens=400,
    )
    return resp.choices[0].message.content


async def ping() -> bool:
    """Cheap reachability check for the Phase 1 checklist item — confirms the model
    string resolves and responds, not just that the API key is set."""
    resp = await _client.chat.completions.create(
        model=config.groq_llm_model,
        messages=[{"role": "user", "content": "Reply with exactly: ok"}],
        max_tokens=5,
    )
    return "ok" in resp.choices[0].message.content.lower()

