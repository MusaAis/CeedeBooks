"""
One-off setup script — NOT part of the running agent. Run this once to create
CeedeBooks' own wallet set + wallet.

Verified against developers.circle.com's dev-controlled-wallets quickstart
(Python SDK: `circle-developer-controlled-wallets`) — this is the real API
shape, not an invented method.

Usage:
    pip install circle-developer-controlled-wallets python-dotenv
    python agent/wallet_setup.py

On success, appends CIRCLE_TREASURY_WALLET_ID and CIRCLE_TREASURY_WALLET_ADDRESS
to .env. Copy the printed address into AGENT_ADDRESS as well — that's the
address BudgetEnforcer.sol's `onlyAgent` will check against.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from circle.web3 import utils, developer_controlled_wallets

load_dotenv()

WALLET_SET_NAME = "CeedeBooks Treasury"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def main():
    api_key = os.getenv("CIRCLE_API_KEY")
    entity_secret = os.getenv("CIRCLE_ENTITY_SECRET")
    if not api_key or not entity_secret:
        raise SystemExit(
            "CIRCLE_API_KEY and CIRCLE_ENTITY_SECRET must be set in .env first.\n"
        )

    client = utils.init_developer_controlled_wallets_client(
        api_key=api_key, entity_secret=entity_secret
    )
    wallet_sets_api = developer_controlled_wallets.WalletSetsApi(client)
    wallets_api = developer_controlled_wallets.WalletsApi(client)

    try:
        wallet_set = wallet_sets_api.create_wallet_set(
            developer_controlled_wallets.CreateWalletSetRequest.from_dict(
                {"name": WALLET_SET_NAME}
            )
        )
        wallet_set_id = wallet_set.data.wallet_set.actual_instance.id

        wallet = wallets_api.create_wallet(
            developer_controlled_wallets.CreateWalletRequest.from_dict(
                {
                    "walletSetId": wallet_set_id,
                    "blockchains": ["ARC-TESTNET"],
                    "count": 1,
                    "accountType": "EOA",
                }
            )
        )
        wallet_data = json.loads(wallet.model_dump_json())["data"]["wallets"][0]

        print(json.dumps({"wallet_set_id": wallet_set_id}, indent=2))
        print(json.dumps(wallet_data, indent=2))

        with open(ENV_PATH, "a") as f:
            f.write(f"\nCIRCLE_TREASURY_WALLET_ID={wallet_data['id']}\n")
            f.write(f"CIRCLE_TREASURY_WALLET_ADDRESS={wallet_data['address']}\n")

        print(f"\nAppended CIRCLE_TREASURY_WALLET_ID and CIRCLE_TREASURY_WALLET_ADDRESS to {ENV_PATH}")
        print(f"Also set AGENT_ADDRESS={wallet_data['address']} — this is what")
        print("BudgetEnforcer.sol's onlyAgent modifier will check against.")
        print("\nNext: fund this address at https://faucet.circle.com (select Arc Testnet).")

    except developer_controlled_wallets.ApiException as e:
        print(f"Circle Wallets API error: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

