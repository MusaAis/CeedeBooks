// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {BudgetEnforcer} from "../contracts/BudgetEnforcer.sol";
import {MockUSDC} from "./mocks/MockUSDC.sol";

contract BudgetEnforcerTest is Test {
    BudgetEnforcer enforcer;
    MockUSDC usdc;

    address agent = makeAddr("agent");
    address approver = makeAddr("approver");
    address vendor = makeAddr("vendor");
    address otherVendor = makeAddr("otherVendor");

    uint8 constant CAT_DATA = 0;
    uint8 constant CAT_UNSET = 9;

    bytes32 constant INV_1 = keccak256("INV-001");
    bytes32 constant DOC_1 = keccak256("receipt-pdf-v1");
    bytes32 constant DOC_1B = keccak256("receipt-pdf-resent-as-different-file");
    bytes32 constant REASON_1 = keccak256("laya: pay_now conf=0.91");

    // Re-declared with the identical signature so `emit PaymentMade(...)` works with
    // vm.expectEmit — Solidity doesn't let you reference another contract's event by name.
    event PaymentMade(
        bytes32 indexed invoiceKey,
        bytes32 indexed reasoningHash,
        address indexed vendor,
        uint256 amount,
        uint8 category,
        bytes32 docHash,
        uint256 timestamp
    );

    function setUp() public {
        usdc = new MockUSDC();
        enforcer = new BudgetEnforcer(agent, approver, address(usdc));
        usdc.mint(address(enforcer), 1_000_000_000); // 1,000 USDC (6 decimals)

        vm.startPrank(approver);
        enforcer.setBudget(100_000_000, 20_000_000); // 100 daily / 20 per-tx
        enforcer.setCategoryDailyLimit(CAT_DATA, 50_000_000); // 50 in this category/day
        enforcer.setVendor(vendor, true);
        vm.stopPrank();
    }

    // ---- helpers -----------------------------------------------------------

    function _commitAndAdvance(address v, uint256 amount, bytes32 inv, bytes32 doc, uint8 cat, bytes32 reason)
        internal
        returns (bytes32 commitment)
    {
        commitment = enforcer.commitmentFor(v, amount, inv, doc, cat, reason);
        vm.prank(agent);
        enforcer.commitDecision(commitment);
        vm.roll(block.number + 1); // pay() requires the commit be from an EARLIER block
    }

    function _payHappyPath(address v, uint256 amount, bytes32 inv, bytes32 doc, uint8 cat, bytes32 reason) internal {
        _commitAndAdvance(v, amount, inv, doc, cat, reason);
        vm.prank(agent);
        enforcer.pay(v, amount, inv, doc, cat, reason);
    }

    // ---- 1. unregistered vendor ---------------------------------------------

    function test_pay_revertsForUnregisteredVendor() public {
        _commitAndAdvance(otherVendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
        vm.prank(agent);
        vm.expectRevert("Vendor not approved");
        enforcer.pay(otherVendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
    }

    // ---- vendor wallet change: old address stops working the moment it's revoked

    function test_vendorRevocation_blocksFurtherPayment() public {
        vm.prank(approver);
        enforcer.setVendor(vendor, false);

        _commitAndAdvance(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
        vm.prank(agent);
        vm.expectRevert("Vendor not approved");
        enforcer.pay(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
    }

    function test_onlyApprover_canRegisterVendor() public {
        vm.prank(agent);
        vm.expectRevert("Not approver");
        enforcer.setVendor(otherVendor, true);
    }

    // ---- 3. limits: per-tx, daily, category, unset category -----------------

    function test_pay_revertsOverPerTxLimit() public {
        _commitAndAdvance(vendor, 21_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
        vm.prank(agent);
        vm.expectRevert("Exceeds per-tx limit");
        enforcer.pay(vendor, 21_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
    }

    function test_pay_revertsOverDailyLimit_acrossMultiplePayments() public {
        // category 1 has no limit set by default, so raise it high enough that only the
        // overall daily limit binds in this test
        vm.prank(approver);
        enforcer.setCategoryDailyLimit(CAT_DATA + 1, 1_000_000_000);

        // 6 * 20 = 120 > 100 daily limit, and each is <= 20 per-tx limit
        for (uint256 i = 0; i < 5; i++) {
            bytes32 inv = keccak256(abi.encode("INV-DAY-", i));
            _payHappyPath(vendor, 20_000_000, inv, DOC_1, CAT_DATA + 1, REASON_1);
        }

        bytes32 inv6 = keccak256("INV-DAY-6");
        _commitAndAdvance(vendor, 20_000_000, inv6, DOC_1, CAT_DATA + 1, REASON_1);
        vm.prank(agent);
        vm.expectRevert("Exceeds daily limit");
        enforcer.pay(vendor, 20_000_000, inv6, DOC_1, CAT_DATA + 1, REASON_1);
    }

    function test_pay_revertsOverCategoryDailyLimit() public {
        // CAT_DATA daily cap is 50; two payments of 20 = 40, a third of 20 would hit 60 > 50
        _payHappyPath(vendor, 20_000_000, keccak256("INV-C1"), DOC_1, CAT_DATA, REASON_1);
        _payHappyPath(vendor, 20_000_000, keccak256("INV-C2"), DOC_1, CAT_DATA, REASON_1);

        bytes32 inv3 = keccak256("INV-C3");
        _commitAndAdvance(vendor, 20_000_000, inv3, DOC_1, CAT_DATA, REASON_1);
        vm.prank(agent);
        vm.expectRevert("Exceeds category daily limit");
        enforcer.pay(vendor, 20_000_000, inv3, DOC_1, CAT_DATA, REASON_1);
    }

    function test_pay_revertsForUnsetCategory_failClosed() public {
        _commitAndAdvance(vendor, 1_000_000, INV_1, DOC_1, CAT_UNSET, REASON_1);
        vm.prank(agent);
        vm.expectRevert("Exceeds category daily limit");
        enforcer.pay(vendor, 1_000_000, INV_1, DOC_1, CAT_UNSET, REASON_1);
    }

    // ---- 4. same invoice re-sent as a different PDF: still "Already paid" ---

    function test_pay_revertsOnReSubmittedInvoice_evenWithDifferentDocHash() public {
        _payHappyPath(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);

        bytes32 newReason = keccak256("laya: pay_now conf=0.95 (resubmission)");
        _commitAndAdvance(vendor, 5_000_000, INV_1, DOC_1B, CAT_DATA, newReason);
        vm.prank(agent);
        vm.expectRevert("Already paid");
        enforcer.pay(vendor, 5_000_000, INV_1, DOC_1B, CAT_DATA, newReason);
    }

    // ---- 5. missing, reused, or mismatched decision commit -------------------

    function test_pay_revertsWithNoCommit() public {
        vm.prank(agent);
        vm.expectRevert("No prior commit");
        enforcer.pay(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
    }

    function test_pay_revertsWhenCommitAndPaySameBlock() public {
        bytes32 commitment = enforcer.commitmentFor(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
        vm.prank(agent);
        enforcer.commitDecision(commitment);
        // no vm.roll — same block as the commit
        vm.prank(agent);
        vm.expectRevert("No prior commit");
        enforcer.pay(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
    }

    function test_pay_revertsOnReusedCommit_beforeConsumption() public {
        // Committing the exact same decision twice in a row (before it's spent) must revert —
        // otherwise a duplicate commitDecision call could mask which decision actually paid.
        bytes32 commitment = enforcer.commitmentFor(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
        vm.prank(agent);
        enforcer.commitDecision(commitment);

        vm.prank(agent);
        vm.expectRevert("Already committed");
        enforcer.commitDecision(commitment);
    }

    function test_commit_canBeReusedAfterConsumption_butInvoiceStillBlocksReplay() public {
        // A consumed commitment is deleted, so the *hash* could technically be re-committed —
        // but the invoice-level idempotency check is what actually stops a replay, so the net
        // effect is still safe.
        _payHappyPath(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);

        bytes32 commitment = enforcer.commitmentFor(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
        vm.prank(agent);
        enforcer.commitDecision(commitment); // does not revert: slot was cleared on consumption
        vm.roll(block.number + 1);

        vm.prank(agent);
        vm.expectRevert("Already paid");
        enforcer.pay(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
    }

    function test_pay_revertsOnMismatchedCommit_amountChangedAfterCommit() public {
        // agent commits to paying 5, then tries to pay 15 against that commitment (or vice versa
        // — an unrelated hash cannot be reused for different payment parameters)
        _commitAndAdvance(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
        vm.prank(agent);
        vm.expectRevert("No prior commit");
        enforcer.pay(vendor, 15_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
    }

    // ---- 6. crash mid-payment, restart: no double payment --------------------

    function test_pay_noDoublePaymentOnRetryAfterCrash() public {
        _payHappyPath(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
        assertTrue(enforcer.paid(enforcer.invoiceKey(vendor, INV_1)));

        // agent "restarts" and, not knowing the first call succeeded, retries the exact same call
        _commitAndAdvance(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
        vm.prank(agent);
        vm.expectRevert("Already paid");
        enforcer.pay(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
    }

    // ---- 7. injection: contract refuses even if the agent is "fooled" -------

    function test_injectedInstruction_toPayUnregisteredVendor_stillReverts() public {
        // Simulates: vendor-supplied text tricked the LLM/Laya layer into deciding to pay an
        // unregistered address. The off-chain layer is compromised in this scenario; the
        // contract is the backstop and must refuse regardless of what the agent was told.
        address attackerWallet = makeAddr("attackerWallet");
        _commitAndAdvance(attackerWallet, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
        vm.prank(agent);
        vm.expectRevert("Vendor not approved");
        enforcer.pay(attackerWallet, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
    }

    // ---- 9. escalate -> approver approves -> paid exactly once ---------------

    function test_escalation_approveFlow_paysExactlyOnce() public {
        vm.prank(agent);
        enforcer.escalate(vendor, 50_000_000, INV_1, DOC_1, CAT_DATA, REASON_1, "Exceeds per-tx limit");

        bytes32 key = enforcer.invoiceKey(vendor, INV_1);
        (,,, BudgetEnforcer.EscStatus status,,) = enforcer.escalations(key);
        assertEq(uint8(status), uint8(BudgetEnforcer.EscStatus.Pending));

        uint256 vendorBalBefore = usdc.balanceOf(vendor);
        vm.prank(approver);
        enforcer.approveEscalation(key);
        assertEq(usdc.balanceOf(vendor), vendorBalBefore + 50_000_000);

        // approving twice must not pay twice
        vm.prank(approver);
        vm.expectRevert("Not pending");
        enforcer.approveEscalation(key);
    }

    function test_escalation_rejectFlow_blocksApproval() public {
        vm.prank(agent);
        enforcer.escalate(vendor, 50_000_000, INV_1, DOC_1, CAT_DATA, REASON_1, "Vendor wallet changed");

        bytes32 key = enforcer.invoiceKey(vendor, INV_1);
        vm.prank(approver);
        enforcer.rejectEscalation(key);

        vm.prank(approver);
        vm.expectRevert("Not pending");
        enforcer.approveEscalation(key);
    }

    function test_escalatedInvoice_cannotAlsoBePaidDirectly() public {
        vm.prank(agent);
        enforcer.escalate(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1, "manual review");

        _commitAndAdvance(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
        vm.prank(agent);
        vm.expectRevert("Invoice escalated");
        enforcer.pay(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
    }

    // ---- admin: pause / withdraw / rotation ----------------------------------

    function test_pause_blocksPayments() public {
        vm.prank(approver);
        enforcer.setPaused(true);

        _commitAndAdvance(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
        vm.prank(agent);
        vm.expectRevert("Paused");
        enforcer.pay(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
    }

    function test_withdraw_onlyApprover() public {
        vm.prank(agent);
        vm.expectRevert("Not approver");
        enforcer.withdraw(approver, 1_000_000);

        uint256 before = usdc.balanceOf(approver);
        vm.prank(approver);
        enforcer.withdraw(approver, 1_000_000);
        assertEq(usdc.balanceOf(approver), before + 1_000_000);
    }

    function test_setAgent_onlyApprover_andTakesEffect() public {
        address newAgent = makeAddr("newAgent");
        vm.prank(agent);
        vm.expectRevert("Not approver");
        enforcer.setAgent(newAgent);

        vm.prank(approver);
        enforcer.setAgent(newAgent);

        // old agent can no longer act
        vm.prank(agent);
        vm.expectRevert("Not agent");
        enforcer.commitDecision(keccak256("anything"));

        // new agent can
        vm.prank(newAgent);
        enforcer.commitDecision(keccak256("anything"));
    }

    function test_approverRotation_isTwoStep() public {
        address newApprover = makeAddr("newApprover");

        // a stray address cannot just claim the role
        vm.prank(newApprover);
        vm.expectRevert("Not pending approver");
        enforcer.acceptApprover();

        vm.prank(approver);
        enforcer.proposeApprover(newApprover);

        // old approver still has the role until accept() is called
        vm.prank(approver);
        enforcer.setBudget(1, 1);

        vm.prank(newApprover);
        enforcer.acceptApprover();

        vm.prank(approver);
        vm.expectRevert("Not approver");
        enforcer.setBudget(2, 2);
    }

    function test_setBudget_rejectsPerTxAboveDaily() public {
        vm.prank(approver);
        vm.expectRevert("perTx > daily");
        enforcer.setBudget(10_000_000, 20_000_000);
    }

    // ---- reasoningHash is emitted and queryable from the event ---------------

    function test_payment_emitsReasoningHashForRoundTripVerification() public {
        bytes32 key = enforcer.invoiceKey(vendor, INV_1);
        _commitAndAdvance(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);

        vm.expectEmit(true, true, true, true);
        emit PaymentMade(key, REASON_1, vendor, 5_000_000, CAT_DATA, DOC_1, block.timestamp);

        vm.prank(agent);
        enforcer.pay(vendor, 5_000_000, INV_1, DOC_1, CAT_DATA, REASON_1);
    }
}
