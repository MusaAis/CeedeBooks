// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {MilestoneEscrow} from "../contracts/MilestoneEscrow.sol";
import {MockUSDC} from "./mocks/MockUSDC.sol";

contract MilestoneEscrowTest is Test {
    MilestoneEscrow escrow;
    MockUSDC usdc;

    address agent = makeAddr("agent");
    address approver = makeAddr("approver");
    address contractor = makeAddr("contractor");
    address other = makeAddr("other");

    uint256 constant M0 = 10_000_000; // 10 USDC
    uint256 constant M1 = 20_000_000; // 20 USDC
    uint256 constant M2 = 50_000_000; // 50 USDC, above the agent limit
    uint256 constant LIMIT = 25_000_000;

    bytes32 constant REQ0 = keccak256("requirements-0");
    bytes32 constant REQ1 = keccak256("requirements-1");
    bytes32 constant REQ2 = keccak256("requirements-2");
    bytes32 constant EVID = keccak256("github-pr-merged:owner/repo#12");
    bytes32 constant REASON = keccak256("rules: release 0");

    event MilestoneReleased(uint256 indexed index, bytes32 indexed reasoningHash, bytes32 evidenceHash, uint256 amount);

    function _deploy(address a, address ap, address c, uint256 limit) internal returns (MilestoneEscrow) {
        uint256[] memory amounts = new uint256[](3);
        amounts[0] = M0;
        amounts[1] = M1;
        amounts[2] = M2;
        bytes32[] memory reqs = new bytes32[](3);
        reqs[0] = REQ0;
        reqs[1] = REQ1;
        reqs[2] = REQ2;
        return new MilestoneEscrow(a, ap, c, address(usdc), limit, amounts, reqs);
    }

    function setUp() public {
        usdc = new MockUSDC();
        escrow = _deploy(agent, approver, contractor, LIMIT);
        usdc.mint(address(escrow), 100_000_000); // funded by a plain transfer in production
    }

    function _commit(uint256 i, bytes32 ev, bytes32 re) internal {
        bytes32 c = escrow.commitmentFor(i, ev, re);
        vm.prank(agent);
        escrow.commitDecision(c);
        vm.roll(block.number + 1);
    }

    function _release(uint256 i) internal {
        _commit(i, EVID, REASON);
        vm.prank(agent);
        escrow.releaseMilestone(i, EVID, REASON);
    }

    // ---- happy path

    function test_happyPath_commitThenRelease_paysContractor() public {
        _commit(0, EVID, REASON);
        vm.expectEmit(true, true, false, true, address(escrow));
        emit MilestoneReleased(0, REASON, EVID, M0);
        vm.prank(agent);
        escrow.releaseMilestone(0, EVID, REASON);

        assertEq(usdc.balanceOf(contractor), M0);
        (,, MilestoneEscrow.Status s) = escrow.milestones(0);
        assertEq(uint8(s), uint8(MilestoneEscrow.Status.Released));
        assertEq(escrow.pendingTotal(), M1 + M2);
    }

    function test_constructor_storesMilestones() public view {
        assertEq(escrow.milestoneCount(), 3);
        (uint256 amt, bytes32 req,) = escrow.milestones(1);
        assertEq(amt, M1);
        assertEq(req, REQ1);
        assertEq(escrow.pendingTotal(), M0 + M1 + M2);
    }

    // ---- release refusals

    function test_doubleRelease_reverts() public {
        _release(0);
        _commit(0, EVID, REASON);
        vm.prank(agent);
        vm.expectRevert("Not pending");
        escrow.releaseMilestone(0, EVID, REASON);
        assertEq(usdc.balanceOf(contractor), M0);
    }

    function test_wrongAgent_reverts() public {
        _commit(0, EVID, REASON);
        vm.prank(other);
        vm.expectRevert("Not agent");
        escrow.releaseMilestone(0, EVID, REASON);

        vm.prank(approver);
        vm.expectRevert("Not agent");
        escrow.releaseMilestone(0, EVID, REASON);
    }

    function test_noPriorCommit_reverts() public {
        vm.prank(agent);
        vm.expectRevert("No prior commit");
        escrow.releaseMilestone(0, EVID, REASON);
    }

    function test_sameBlockCommitAndRelease_reverts() public {
        bytes32 c = escrow.commitmentFor(0, EVID, REASON);
        vm.startPrank(agent);
        escrow.commitDecision(c);
        vm.expectRevert("No prior commit");
        escrow.releaseMilestone(0, EVID, REASON);
        vm.stopPrank();
    }

    function test_commitmentMismatch_reverts() public {
        _commit(0, EVID, REASON);
        vm.prank(agent);
        vm.expectRevert("No prior commit");
        escrow.releaseMilestone(0, keccak256("other evidence"), REASON);
    }

    function test_commitmentIsSingleUse() public {
        _commit(0, EVID, REASON);
        bytes32 c = escrow.commitmentFor(0, EVID, REASON);
        vm.prank(agent);
        vm.expectRevert("Already committed");
        escrow.commitDecision(c);
    }

    function test_emptyEvidenceOrReasoning_reverts() public {
        _commit(0, bytes32(0), REASON);
        vm.prank(agent);
        vm.expectRevert("No evidence hash");
        escrow.releaseMilestone(0, bytes32(0), REASON);

        _commit(0, EVID, bytes32(0));
        vm.prank(agent);
        vm.expectRevert("No reasoning hash");
        escrow.releaseMilestone(0, EVID, bytes32(0));
    }

    function test_outOfRangeIndex_reverts() public {
        _commit(3, EVID, REASON);
        vm.prank(agent);
        vm.expectRevert("Bad index");
        escrow.releaseMilestone(3, EVID, REASON);

        vm.prank(approver);
        vm.expectRevert("Bad index");
        escrow.overrideRelease(3, EVID);

        vm.prank(agent);
        vm.expectRevert("Bad index");
        escrow.rejectMilestone(3, REASON);
    }

    // ---- approver threshold

    function test_aboveLimit_agentCannotRelease_approverCan() public {
        _commit(2, EVID, REASON);
        vm.prank(agent);
        vm.expectRevert("Needs approver");
        escrow.releaseMilestone(2, EVID, REASON);

        vm.prank(approver);
        escrow.overrideRelease(2, EVID);
        assertEq(usdc.balanceOf(contractor), M2);
        assertEq(escrow.pendingTotal(), M0 + M1);
    }

    function test_atLimit_agentCanRelease() public {
        escrow = _deploy(agent, approver, contractor, M1);
        usdc.mint(address(escrow), 100_000_000);
        _release(1);
        assertEq(usdc.balanceOf(contractor), M1);
    }

    // ---- reject and override

    function test_rejectThenOverride_paysContractor() public {
        vm.prank(agent);
        escrow.rejectMilestone(0, REASON);
        (,, MilestoneEscrow.Status s) = escrow.milestones(0);
        assertEq(uint8(s), uint8(MilestoneEscrow.Status.Rejected));
        assertEq(escrow.pendingTotal(), M1 + M2);

        vm.prank(approver);
        escrow.overrideRelease(0, EVID);
        assertEq(usdc.balanceOf(contractor), M0);
        assertEq(escrow.pendingTotal(), M1 + M2);
    }

    function test_agentCannotReleaseRejected() public {
        vm.prank(agent);
        escrow.rejectMilestone(0, REASON);
        _commit(0, EVID, REASON);
        vm.prank(agent);
        vm.expectRevert("Not pending");
        escrow.releaseMilestone(0, EVID, REASON);
    }

    function test_overrideAlreadyReleased_reverts() public {
        _release(0);
        vm.prank(approver);
        vm.expectRevert("Already released");
        escrow.overrideRelease(0, EVID);
    }

    function test_rejectReleased_reverts() public {
        _release(0);
        vm.prank(agent);
        vm.expectRevert("Not pending");
        escrow.rejectMilestone(0, REASON);
    }

    function test_rejectByStranger_reverts() public {
        vm.prank(other);
        vm.expectRevert("Not agent or approver");
        escrow.rejectMilestone(0, REASON);
    }

    function test_approverCanReject_thenWithdrawFreedFunds() public {
        vm.prank(approver);
        escrow.rejectMilestone(2, REASON);
        assertEq(escrow.withdrawable(), 100_000_000 - M0 - M1);
        vm.prank(approver);
        escrow.withdraw(approver, M2);
        assertEq(usdc.balanceOf(approver), M2);
    }

    function test_overrideByNonApprover_reverts() public {
        vm.prank(agent);
        vm.expectRevert("Not approver");
        escrow.overrideRelease(0, EVID);
    }

    // ---- withdraw

    function test_withdraw_onlyUnfundedBalance() public {
        // balance 100, pending 80, so 20 is free
        assertEq(escrow.withdrawable(), 20_000_000);
        vm.prank(approver);
        escrow.withdraw(approver, 20_000_000);
        assertEq(usdc.balanceOf(approver), 20_000_000);
    }

    function test_withdraw_cannotTouchPending() public {
        vm.prank(approver);
        vm.expectRevert("Exceeds unreserved balance");
        escrow.withdraw(approver, 20_000_001);
    }

    function test_withdraw_afterReleaseKeepsRemainingReserved() public {
        _release(0);
        // balance 90, pending 70, free 20
        assertEq(escrow.withdrawable(), 20_000_000);
        vm.prank(approver);
        vm.expectRevert("Exceeds unreserved balance");
        escrow.withdraw(approver, 20_000_001);
    }

    function test_withdraw_underfundedHasNothingFree() public {
        MilestoneEscrow e = _deploy(agent, approver, contractor, LIMIT);
        usdc.mint(address(e), 5_000_000); // less than the 80 pending
        assertEq(e.withdrawable(), 0);
        vm.prank(approver);
        vm.expectRevert("Exceeds unreserved balance");
        e.withdraw(approver, 1);
    }

    function test_withdraw_zeroAddressAndNonApprover_revert() public {
        vm.prank(approver);
        vm.expectRevert("Zero address");
        escrow.withdraw(address(0), 1);
        vm.prank(agent);
        vm.expectRevert("Not approver");
        escrow.withdraw(agent, 1);
    }

    function test_release_underfundedRevertsAndStaysPending() public {
        MilestoneEscrow e = _deploy(agent, approver, contractor, LIMIT);
        usdc.mint(address(e), 5_000_000); // below milestone 0 (10)
        bytes32 c = e.commitmentFor(0, EVID, REASON);
        vm.prank(agent);
        e.commitDecision(c);
        vm.roll(block.number + 1);
        vm.prank(agent);
        vm.expectRevert("Transfer failed");
        e.releaseMilestone(0, EVID, REASON);
        (,, MilestoneEscrow.Status s) = e.milestones(0);
        assertEq(uint8(s), uint8(MilestoneEscrow.Status.Pending));
        assertEq(e.pendingTotal(), M0 + M1 + M2);
    }

    function test_override_underfundedRevertsAndKeepsAccounting() public {
        MilestoneEscrow e = _deploy(agent, approver, contractor, LIMIT);
        vm.prank(approver);
        vm.expectRevert("Transfer failed");
        e.overrideRelease(0, EVID);
        assertEq(e.pendingTotal(), M0 + M1 + M2);
    }

    // ---- pause

    function test_paused_blocksAgentRelease() public {
        _commit(0, EVID, REASON);
        vm.prank(approver);
        escrow.setPaused(true);
        vm.prank(agent);
        vm.expectRevert("Paused");
        escrow.releaseMilestone(0, EVID, REASON);

        vm.prank(approver);
        escrow.setPaused(false);
        vm.prank(agent);
        escrow.releaseMilestone(0, EVID, REASON);
        assertEq(usdc.balanceOf(contractor), M0);
    }

    function test_paused_blocksAgentRejectButNotApprover() public {
        vm.prank(approver);
        escrow.setPaused(true);
        vm.prank(agent);
        vm.expectRevert("Paused");
        escrow.rejectMilestone(0, REASON);

        vm.prank(approver);
        escrow.rejectMilestone(0, REASON);
    }

    function test_paused_approverOverrideAndWithdrawStillWork() public {
        vm.startPrank(approver);
        escrow.setPaused(true);
        escrow.overrideRelease(0, EVID);
        escrow.withdraw(approver, 20_000_000);
        vm.stopPrank();
        assertEq(usdc.balanceOf(contractor), M0);
        assertEq(usdc.balanceOf(approver), 20_000_000);
    }

    function test_setPaused_onlyApprover() public {
        vm.prank(agent);
        vm.expectRevert("Not approver");
        escrow.setPaused(true);
    }

    // ---- replay

    function test_commitmentFromOtherEscrow_unusable() public {
        MilestoneEscrow b = _deploy(agent, approver, contractor, LIMIT);
        usdc.mint(address(b), 100_000_000);
        // commit on A, try to release on B
        _commit(0, EVID, REASON);
        assertTrue(escrow.commitmentFor(0, EVID, REASON) != b.commitmentFor(0, EVID, REASON));
        vm.prank(agent);
        vm.expectRevert("No prior commit");
        b.releaseMilestone(0, EVID, REASON);
    }

    function test_commitmentBoundToChain() public {
        bytes32 c1 = escrow.commitmentFor(0, EVID, REASON);
        vm.chainId(999);
        assertTrue(c1 != escrow.commitmentFor(0, EVID, REASON));
    }

    function test_commitmentBoundToIndex() public view {
        assertTrue(escrow.commitmentFor(0, EVID, REASON) != escrow.commitmentFor(1, EVID, REASON));
    }

    function test_commitForOneMilestone_cannotReleaseAnother() public {
        _commit(0, EVID, REASON);
        vm.prank(agent);
        vm.expectRevert("No prior commit");
        escrow.releaseMilestone(1, EVID, REASON);
    }

    // ---- roles

    function test_constructor_rejectsBadRoles() public {
        uint256[] memory a = new uint256[](1);
        a[0] = 1;
        bytes32[] memory r = new bytes32[](1);
        r[0] = REQ0;
        vm.expectRevert("Roles must differ");
        new MilestoneEscrow(agent, agent, contractor, address(usdc), LIMIT, a, r);
        vm.expectRevert("Roles must differ");
        new MilestoneEscrow(agent, approver, agent, address(usdc), LIMIT, a, r);
        vm.expectRevert("Roles must differ");
        new MilestoneEscrow(agent, approver, approver, address(usdc), LIMIT, a, r);
        vm.expectRevert("Zero address");
        new MilestoneEscrow(address(0), approver, contractor, address(usdc), LIMIT, a, r);
        vm.expectRevert("Zero address");
        new MilestoneEscrow(agent, approver, contractor, address(0), LIMIT, a, r);
    }

    function test_constructor_rejectsBadMilestones() public {
        uint256[] memory a = new uint256[](1);
        a[0] = 1;
        bytes32[] memory r = new bytes32[](1);
        r[0] = REQ0;

        vm.expectRevert("Bad milestone count");
        new MilestoneEscrow(agent, approver, contractor, address(usdc), LIMIT, new uint256[](0), new bytes32[](0));

        vm.expectRevert("Length mismatch");
        new MilestoneEscrow(agent, approver, contractor, address(usdc), LIMIT, a, new bytes32[](2));

        uint256[] memory z = new uint256[](1);
        vm.expectRevert("Zero amount");
        new MilestoneEscrow(agent, approver, contractor, address(usdc), LIMIT, z, r);

        bytes32[] memory zr = new bytes32[](1);
        vm.expectRevert("No requirements hash");
        new MilestoneEscrow(agent, approver, contractor, address(usdc), LIMIT, a, zr);

        uint256[] memory many = new uint256[](51);
        bytes32[] memory manyR = new bytes32[](51);
        for (uint256 i = 0; i < 51; i++) {
            many[i] = 1;
            manyR[i] = REQ0;
        }
        vm.expectRevert("Bad milestone count");
        new MilestoneEscrow(agent, approver, contractor, address(usdc), LIMIT, many, manyR);
    }

    function test_agentRotation() public {
        vm.prank(approver);
        escrow.setAgent(other);
        assertEq(escrow.agent(), other);

        // old agent is locked out, new agent works
        vm.prank(agent);
        vm.expectRevert("Not agent");
        escrow.commitDecision(keccak256("x"));
        vm.prank(other);
        escrow.commitDecision(keccak256("x"));
    }

    function test_agentRotation_rejectsOverlapAndNonApprover() public {
        vm.startPrank(approver);
        vm.expectRevert("Roles must differ");
        escrow.setAgent(approver);
        vm.expectRevert("Roles must differ");
        escrow.setAgent(contractor);
        vm.expectRevert("Zero address");
        escrow.setAgent(address(0));
        vm.stopPrank();

        vm.prank(agent);
        vm.expectRevert("Not approver");
        escrow.setAgent(other);
    }

    function test_approverRotation_twoStep() public {
        vm.prank(approver);
        escrow.proposeApprover(other);
        assertEq(escrow.approver(), approver); // not yet

        vm.prank(agent);
        vm.expectRevert("Not pending approver");
        escrow.acceptApprover();

        vm.prank(other);
        escrow.acceptApprover();
        assertEq(escrow.approver(), other);
        assertEq(escrow.pendingApprover(), address(0));

        vm.prank(approver);
        vm.expectRevert("Not approver");
        escrow.setPaused(true);
    }

    function test_approverRotation_rejectsOverlap() public {
        vm.startPrank(approver);
        vm.expectRevert("Roles must differ");
        escrow.proposeApprover(agent);
        vm.expectRevert("Roles must differ");
        escrow.proposeApprover(contractor);
        vm.expectRevert("Zero address");
        escrow.proposeApprover(address(0));
        vm.stopPrank();
    }

    function test_approverRotation_pendingBecomingAgentCannotAccept() public {
        vm.prank(approver);
        escrow.proposeApprover(other);
        vm.prank(approver);
        escrow.setAgent(other); // approver moves the agent role onto the pending address
        vm.prank(other);
        vm.expectRevert("Roles must differ");
        escrow.acceptApprover();
    }

    // ---- invariant

    function testFuzz_withdrawNeverTouchesPending(uint96 funded, uint96 ask) public {
        MilestoneEscrow e = _deploy(agent, approver, contractor, LIMIT);
        usdc.mint(address(e), funded);
        uint256 pendingBefore = e.pendingTotal();
        bool tooMuch = ask > e.withdrawable();
        vm.prank(approver);
        if (tooMuch) {
            vm.expectRevert("Exceeds unreserved balance");
            e.withdraw(approver, ask);
        } else {
            e.withdraw(approver, ask);
            assertTrue(usdc.balanceOf(address(e)) >= pendingBefore || funded < pendingBefore);
        }
        assertEq(e.pendingTotal(), pendingBefore);
    }
}
