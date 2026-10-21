"""Public outcome figures (Phase P). Each figure comes from one SQL statement in QUERIES, which the API also returns, so
anyone can read the definition next to the number. Live invoices only; shadow figures stay in models.shadow_stats and are
never added in. Own and outside businesses and the three origins are never summed together."""
import statistics
import time
from typing import Optional

from backend import models

# A person released the escalation on-chain: the admin site records it as 'approve_escalation' with ref 'invoice <id>'.
_RELEASED = ("EXISTS (SELECT 1 FROM admin_actions a WHERE a.business_id = i.business_id "
             "AND a.action = 'approve_escalation' AND a.ref = 'invoice ' || i.id)")

QUERIES = {
    "invoice_outcomes": (
        "SELECT b.external AS external, i.origin AS origin, COUNT(*) AS processed,\n"
        f"  SUM(i.status = 'paid' AND NOT {_RELEASED}) AS paid_by_agent,\n"
        f"  SUM(i.status = 'paid' AND {_RELEASED}) AS released_by_person,\n"
        "  SUM(i.status = 'held') AS held,\n"
        "  SUM(i.status = 'escalated') AS escalated_waiting,\n"
        "  SUM(i.status = 'rejected') AS rejected_by_person,\n"
        "  COALESCE(SUM(CASE WHEN i.status = 'paid' THEN i.amount_usdc END), 0) AS volume_usdc\n"
        "FROM invoices i JOIN businesses b ON b.id = i.business_id\n"
        "WHERE i.mode = 'live' AND i.status IN ('paid', 'held', 'escalated', 'rejected') {scope}\n"
        "GROUP BY b.external, i.origin"
    ),
    "duplicates_caught": (
        "SELECT b.external AS external, r.origin AS origin, COUNT(*) AS n\n"
        "FROM rejected_submissions r JOIN businesses b ON b.id = r.business_id\n"
        "WHERE r.reason = 'duplicate_invoice' {scope}\n"
        "GROUP BY b.external, r.origin"
    ),
    "seconds_to_payment": (  # the median is taken in code over exactly these rows
        "SELECT b.external AS external, i.origin AS origin, i.settled_at - i.created_at AS seconds\n"
        "FROM invoices i JOIN businesses b ON b.id = i.business_id\n"
        f"WHERE i.mode = 'live' AND i.status = 'paid' AND i.settled_at IS NOT NULL AND NOT {_RELEASED} {{scope}}"
    ),
}
DEFINITIONS = {
    "processed": "Live invoices that reached a decision: paid, held, escalated or rejected.",
    "decided_alone": "Paid by the agent with no person releasing it, plus held by the agent. No person acted on these.",
    "escalated": "Sent to a person: still waiting, released by a person, or rejected by a person.",
    "person_agreement": "Of escalations a person has settled, the share they rejected (the agent's caution stood). A release "
                        "overrides it. Null until a person has settled one.",
    "seconds_to_payment": "Median seconds from the invoice being submitted to the confirmed payment, for payments the agent "
                          "made alone and that were stamped at payment time (v1.3.1 on). Earlier payments are counted in "
                          "without_timing, not guessed.",
    "volume_usdc": "Sum of paid live invoices, in USDC.",
}


def _scope(business_id: Optional[int], alias: str) -> tuple:
    if business_id is not None:
        return f"AND {alias}.business_id = ?", (business_id,)
    return f"AND {alias}.business_id NOT IN (SELECT id FROM businesses WHERE status = 'archived')", ()


def _empty() -> dict:
    return {
        "processed": 0, "paid_by_agent": 0, "released_by_person": 0, "held": 0, "decided_alone": 0,
        "escalated": {"waiting": 0, "released": 0, "rejected": 0, "total": 0},
        "person_agreement": {"confirmed_agent": 0, "overrode_agent": 0, "n": 0, "rate": None},
        "duplicates_caught": 0, "volume_usdc": 0.0,
        "seconds_to_payment": {"median": None, "n": 0, "without_timing": 0},
    }


def outside_statement(counts: dict, one_business: bool) -> str:
    """Written by code, never by a model (rule 18)."""
    if one_business:
        return "Figures for one business."
    ext, total = counts["external"], counts["total"]
    if ext == 0:
        return f"Outside businesses: 0 confirmed. {total} active business{'es' if total != 1 else ''}, none confirmed as an outside party."
    return f"Outside businesses: {ext} of {total} active, confirmed as outside parties by the operator."


async def get_outcomes(business_id: Optional[int] = None, business_slug: Optional[str] = None) -> dict:
    where_i, args = _scope(business_id, "i")
    where_r, _ = _scope(business_id, "r")
    blocks = {"own": {o: _empty() for o in models.ORIGINS}, "outside": {o: _empty() for o in models.ORIGINS}}

    def at(row):
        return blocks["outside" if row["external"] else "own"][row["origin"]]

    for r in await models._all(QUERIES["invoice_outcomes"].format(scope=where_i), args):
        if r["origin"] not in models.ORIGINS:
            continue
        b = at(r)
        b["processed"] = r["processed"]
        b["paid_by_agent"], b["released_by_person"], b["held"] = r["paid_by_agent"], r["released_by_person"], r["held"]
        b["decided_alone"] = b["paid_by_agent"] + b["held"]
        esc = b["escalated"]
        esc["waiting"], esc["released"], esc["rejected"] = r["escalated_waiting"], r["released_by_person"], r["rejected_by_person"]
        esc["total"] = esc["waiting"] + esc["released"] + esc["rejected"]
        n = esc["released"] + esc["rejected"]
        b["person_agreement"] = {"confirmed_agent": esc["rejected"], "overrode_agent": esc["released"], "n": n,
                                 "rate": round(esc["rejected"] / n, 4) if n else None}
        b["volume_usdc"] = round(float(r["volume_usdc"]), 6)
    for r in await models._all(QUERIES["duplicates_caught"].format(scope=where_r), args):
        if r["origin"] in models.ORIGINS:
            at(r)["duplicates_caught"] = r["n"]
    durations: dict = {}
    for r in await models._all(QUERIES["seconds_to_payment"].format(scope=where_i), args):
        if r["origin"] in models.ORIGINS and r["seconds"] is not None and r["seconds"] >= 0:
            durations.setdefault(("outside" if r["external"] else "own", r["origin"]), []).append(float(r["seconds"]))
    for owner, group in blocks.items():
        for origin, b in group.items():
            d = durations.get((owner, origin), [])
            b["seconds_to_payment"] = {"median": round(statistics.median(d), 1) if d else None, "n": len(d),
                                       "without_timing": max(b["paid_by_agent"] - len(d), 0)}
    counts = await models.business_counts()
    return {
        "scope": business_slug or "all",
        "as_of": int(time.time()),
        "outside_businesses": {**counts, "statement": outside_statement(counts, business_id is not None)},
        "by_ownership": blocks,
        "shadow": await models.shadow_stats(business_id),
        "definitions": DEFINITIONS,
        "queries": {k: v.format(scope="<scope>") for k, v in QUERIES.items()},
    }
