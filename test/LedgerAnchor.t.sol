// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {LedgerAnchor} from "../contracts/LedgerAnchor.sol";

contract LedgerAnchorTest is Test {
    LedgerAnchor anchorC;
    address a = makeAddr("a");
    address b = makeAddr("b");

    event Anchored(address indexed sender, bytes32 head, uint256 sequence, uint256 timestamp);

    function setUp() public {
        anchorC = new LedgerAnchor();
    }

    function test_anchor_emitsAndStores() public {
        vm.expectEmit(true, false, false, true);
        emit Anchored(a, keccak256("h1"), 5, block.timestamp);
        vm.prank(a);
        anchorC.anchor(keccak256("h1"), 5);
        assertEq(anchorC.lastSequence(a), 5);
        assertEq(anchorC.lastHead(a), keccak256("h1"));
    }

    function test_anchor_sequenceMustIncrease() public {
        vm.startPrank(a);
        anchorC.anchor(keccak256("h1"), 5);
        vm.expectRevert("Stale sequence");
        anchorC.anchor(keccak256("h2"), 5);
        vm.expectRevert("Stale sequence");
        anchorC.anchor(keccak256("h2"), 4);
        anchorC.anchor(keccak256("h2"), 6);
        vm.stopPrank();
    }

    function test_anchor_isPerSender() public {
        vm.prank(a);
        anchorC.anchor(keccak256("h1"), 5);
        vm.prank(b);
        anchorC.anchor(keccak256("hb"), 1);
        assertEq(anchorC.lastSequence(b), 1);
        assertEq(anchorC.lastSequence(a), 5);
    }

    function test_anchor_rejectsEmptyHead() public {
        vm.prank(a);
        vm.expectRevert("Empty head");
        anchorC.anchor(bytes32(0), 1);
    }
}
