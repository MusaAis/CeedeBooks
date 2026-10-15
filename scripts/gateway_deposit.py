"""F2: deposit a small amount of USDC from a business's agent wallet into Circle's Gateway Wallet, by hand.

  PYTHONPATH=. python3 scripts/gateway_deposit.py --business ceedebooks --amount 1 --dry-run
  PYTHONPATH=. python3 scripts/gateway_deposit.py --business ceedebooks --amount 1

--dry-run only reads and prints: it writes no audit row and sends nothing. Without it the script pauses for "yes" before each
step: (1) the audit row, written and hashed before any on-chain call; (2) USDC approve(GatewayWallet, amount), the exact
amount and skipped if that allowance already exists; (3) GatewayWallet.deposit(USDC, amount). The USDC leaves the agent
wallet's own balance, not the business pool, and no vendor is paid. A plain USDC transfer to the Gateway Wallet would lose
the funds, so the script never does one. Gateway credits a deposit after finality, so the balance can take a few minutes
to show. The audit row is labelled manual and keeps no chain_tx_hash: the proof page's Verify checks a payment event, and
a deposit has none.
"""
import argparse
import asyncio
import os
import sys
import time
from decimal import Decimal, InvalidOperation

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import contract, decision_log, gateway  # noqa: E402
from agent.config import config  # noqa: E402
from backend import models  # noqa: E402

ACTION = "GATEWAY_DEPOSIT"
MAX_USDC = Decimal("5")  # a hard cap: this is a small proof, not treasury management
GAS_RESERVE_UNITS = 100_000  # 0.1 USDC stays in the wallet for gas (Arc gas is USDC)
EXPLORER = "https://explorer.testnet.arc.io"


class DepositError(Exception):
    """A check failed; nothing was sent."""


def parse_amount(text: str) -> int:
    """USDC text to 6-decimal units. Positive, at most 6 places, at most MAX_USDC."""
    try:
        value = Decimal(str(text).strip())
    except InvalidOperation:
        raise DepositError(f"'{text}' is not a number")
    if not value.is_finite() or value <= 0:
        raise DepositError("The amount must be above zero")
    if value != value.quantize(Decimal("0.000001")):
        raise DepositError("USDC has at most 6 decimal places")
    if value > MAX_USDC:
        raise DepositError(f"The amount is capped at {MAX_USDC} USDC for this script")
    return int(value * 10**6)


def usdc(units: int) -> str:
    return f"{Decimal(units) / 10**6:.6f}"


def _confirm(message: str) -> None:
    if input(f"\n{message}\nType 'yes' to continue: ").strip().lower() != "yes":
        raise SystemExit("Aborted. Nothing further was sent.")


async def _gateway_balance(agent: str):
    gateway.reset()  # never a cached figure
    return await gateway.get_balance(agent)


async def preflight(business: dict, units: int) -> dict:
    """Every read the deposit depends on. Anything unreadable or wrong raises DepositError (fail closed)."""
    agent, wallet_id = business.get("agent_address"), business.get("circle_wallet_id")
    if not agent or not wallet_id:
        raise DepositError("This business has no agent wallet on record")
    spender = config.gateway_wallet_address
    try:
        on_chain_agent = contract.chain_for(business).agent()
        balance = contract.usdc_balance_of(agent)
        allowance = contract.usdc_allowance(agent, spender)
        gateway_has_code, usdc_has_code = contract.has_code(spender), contract.has_code(config.usdc_address)
    except Exception as e:
        raise DepositError(f"Could not read the chain, so nothing was sent: {e}")
    if str(on_chain_agent).lower() != str(agent).lower():
        raise DepositError(f"The contract's agent() is {on_chain_agent}, not the wallet on record {agent}")
    if not gateway_has_code:
        raise DepositError(f"No contract is deployed at the Gateway Wallet address {spender}")
    if not usdc_has_code:
        raise DepositError(f"No contract is deployed at the USDC address {config.usdc_address}")
    if balance < units + GAS_RESERVE_UNITS:
        raise DepositError(f"The agent wallet holds {usdc(balance)} USDC; it needs {usdc(units + GAS_RESERVE_UNITS)} "
                           f"({usdc(units)} to deposit plus {usdc(GAS_RESERVE_UNITS)} kept for gas)")
    return {"agent": agent, "wallet_id": wallet_id, "spender": spender, "balance": balance, "allowance": allowance,
            "gateway_before": await _gateway_balance(agent)}


def _describe(plan: dict, units: int) -> str:
    before = plan["gateway_before"]
    return (f"Agent wallet:        {plan['agent']}\n"
            f"Gateway Wallet:      {plan['spender']}  (Circle, Arc Testnet; check it at developers.circle.com/gateway/references/contract-addresses)\n"
            f"USDC token:          {config.usdc_address}\n"
            f"Deposit:             {usdc(units)} USDC (cap {MAX_USDC})\n"
            f"Agent USDC balance:  {usdc(plan['balance'])}\n"
            f"Existing allowance:  {usdc(plan['allowance'])} -> approve step {'skipped' if plan['allowance'] >= units else 'needed'}\n"
            f"Gateway balance now: {'unreadable' if before is None else f'{before:.6f} USDC'}")


async def run(business: dict, units: int, confirm=_confirm, dry_run=False, wait_blocks=True, poll=(6, 10)) -> dict:
    plan = await preflight(business, units)
    print(_describe(plan, units))
    if dry_run:
        print("\nDry run: no audit row was written and nothing was sent.")
        return {"dry_run": True, "plan": plan}

    reasoning = (f"Gateway deposit, a manual step and not a payment to a vendor: the agent wallet {plan['agent']} deposits {usdc(units)} USDC "
                 f"into Circle's Gateway Wallet {plan['spender']} on Arc Testnet (domain {gateway.ARC_DOMAIN}), so its Gateway balance can be read "
                 f"and later used. The USDC comes from the agent wallet's own balance; the business pool is not touched and no vendor is paid.")
    confirm("STEP 1: write the audit row (hash computed and stored before any on-chain call).")
    reasoning_hash = await decision_log.log_decision(ACTION, "GATEWAY", reasoning, model_used="manual", amount=float(Decimal(units) / 10**6), business=business)

    if plan["allowance"] >= units:
        print(f"\nSTEP 2 skipped: the Gateway Wallet already has an allowance of {usdc(plan['allowance'])} USDC.")
    else:
        confirm(f"STEP 2: send USDC approve({plan['spender']}, {usdc(units)}) from the agent wallet. The exact amount, not unlimited.")
        contract.wait_for_transaction(contract.approve_usdc(plan["wallet_id"], plan["spender"], units))
        if wait_blocks:
            start = contract._w3.eth.block_number
            while contract._w3.eth.block_number <= start:
                time.sleep(1)
        if contract.usdc_allowance(plan["agent"], plan["spender"]) < units:
            raise DepositError("The approve confirmed but the allowance cannot be read as large enough; the deposit was not sent")

    confirm(f"STEP 3: send GatewayWallet.deposit({config.usdc_address}, {usdc(units)}) from the agent wallet.")
    tx_id = contract.gateway_deposit(plan["wallet_id"], units)
    contract.wait_for_transaction(tx_id)
    tx_hash = contract.chain_tx_hash(tx_id)
    await decision_log.record_onchain_tx(reasoning_hash, tx_id, None)  # no chain_tx_hash on purpose (see the module note)
    print(f"\nDeposit confirmed. Circle transaction id: {tx_id}")
    print(f"Transaction hash: {tx_hash or 'not available yet (find it on the explorer from the agent wallet)'}")
    if tx_hash:
        print(f"Explorer: {EXPLORER}/tx/{tx_hash}")

    after, tries, wait = None, poll[0], poll[1]
    for i in range(tries):
        after = await _gateway_balance(plan["agent"])
        if after is not None and (plan["gateway_before"] is None or after > plan["gateway_before"]):
            break
        if i < tries - 1:
            time.sleep(wait)
    shown = "unreadable" if after is None else f"{after:.6f} USDC"
    print(f"Gateway balance now: {shown}")
    if after is None or (plan["gateway_before"] is not None and after <= plan["gateway_before"]):
        print("Gateway credits a deposit only after finality, so the balance may not show yet. Check the admin Overview card in a few minutes.")
    return {"dry_run": False, "reasoning_hash": reasoning_hash, "circle_tx_id": tx_id, "tx_hash": tx_hash,
            "gateway_before": plan["gateway_before"], "gateway_after": after}


async def main(slug: str, amount: str, dry_run: bool) -> None:
    await models.init_db()
    await decision_log.init_db()
    business = await models.get_business_by_slug(slug)
    if business is None:
        sys.exit(f"No business '{slug}'")
    try:
        result = await run(business, parse_amount(amount), dry_run=dry_run)
    except DepositError as e:
        sys.exit(f"Stopped, nothing was sent: {e}")
    if not result["dry_run"]:
        print(f"\nDone. Reasoning hash {result['reasoning_hash']} is on the public audit feed as {ACTION}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--business", required=True, help="business slug")
    ap.add_argument("--amount", required=True, help=f"USDC to deposit, at most {MAX_USDC}")
    ap.add_argument("--dry-run", action="store_true", help="read and print only")
    args = ap.parse_args()
    asyncio.run(main(args.business, args.amount, args.dry_run))
