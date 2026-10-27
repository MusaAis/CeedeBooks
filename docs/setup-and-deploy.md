# Setup and deploy

Running the API locally is in the [README](../README.md#setup); server upgrades, one section per release, are in [`deploy/DEPLOY.md`](../deploy/DEPLOY.md).

## Circle treasury wallet

```bash
python agent/wallet_setup.py
```

This creates the original business's wallet; other businesses' wallets are created by the server when the operator accepts them. It reuses an already-registered Circle entity secret (don't generate a new one for an account that already has one; that's a rotation flow). Creates a wallet set and one Arc Testnet wallet and appends its ID and address to `.env`. Fund it at [faucet.circle.com](https://faucet.circle.com).

## Deploy

```bash
forge create contracts/BudgetEnforcer.sol:BudgetEnforcer \
  --rpc-url $ARC_TESTNET_RPC_URL \
  --private-key $DEPLOYER_PRIVATE_KEY \
  --broadcast \
  --verify \
  --verifier blockscout \
  --verifier-url https://explorer.testnet.arc.io/api/ \
  --constructor-args $AGENT_ADDRESS $APPROVER_ADDRESS 0x3600000000000000000000000000000000000000
```

Then, from the approver key:

```bash
cast send $BUDGET_ENFORCER_ADDRESS "setBudget(uint256,uint256)" 100000000 20000000 \
  --rpc-url $ARC_TESTNET_RPC_URL --private-key $DEPLOYER_PRIVATE_KEY

cast send $BUDGET_ENFORCER_ADDRESS "setCategoryDailyLimit(uint8,uint256)" 0 50000000 \
  --rpc-url $ARC_TESTNET_RPC_URL --private-key $DEPLOYER_PRIVATE_KEY

cast send $BUDGET_ENFORCER_ADDRESS "setVendor(address,bool)" <VENDOR_ADDRESS> true \
  --rpc-url $ARC_TESTNET_RPC_URL --private-key $DEPLOYER_PRIVATE_KEY
```

Amounts are USDC in its 6-decimal ERC-20 form (`100000000` = $100.00).

**Multi-business contracts (v1.2.8).** Neither has an owner, so the deployer key has no power over them. Deployed Oct 4, 2026:

```bash
forge create contracts/LedgerAnchor.sol:LedgerAnchor \
  --rpc-url $ARC_TESTNET_RPC_URL --private-key $DEPLOYER_PRIVATE_KEY --broadcast \
  --verify --verifier blockscout --verifier-url https://explorer.testnet.arc.io/api/

forge create contracts/BudgetFactory.sol:BudgetFactory \
  --rpc-url $ARC_TESTNET_RPC_URL --private-key $DEPLOYER_PRIVATE_KEY --broadcast \
  --verify --verifier blockscout --verifier-url https://explorer.testnet.arc.io/api/ \
  --constructor-args 0x3600000000000000000000000000000000000000
```

Deployment transactions: [LedgerAnchor `0x45c031cd...9f9f4d`](https://explorer.testnet.arc.io/tx/0x45c031cda95d0e688b967e8f2a52abd4d00b801bb1240f4a314477497a9f9f4d), [BudgetFactory `0x4e69b06e...b61fbb`](https://explorer.testnet.arc.io/tx/0x4e69b06eac8b349a320ca29276e3739177409db018af46a84659f32237b61fbb); both verified on the explorer. A business owner then calls `createBusiness(agent, daily, perTx, weekly)` from their own wallet (Phase L3); each business's enforcer address is listed in the Contracts table.
