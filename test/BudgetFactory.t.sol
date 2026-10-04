// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {BudgetFactory} from "../contracts/BudgetFactory.sol";
import {BudgetEnforcerV2} from "../contracts/BudgetEnforcerV2.sol";
import {MockUSDC} from "./mocks/MockUSDC.sol";

contract BudgetFactoryTest is Test {
    BudgetFactory factory;
    MockUSDC usdc;
    address owner1 = makeAddr("owner1");
    address owner2 = makeAddr("owner2");
    address agent1 = makeAddr("agent1");
    address agent2 = makeAddr("agent2");

    event BusinessCreated(uint256 indexed id, address indexed enforcer, address indexed approver, address agent);

    function setUp() public {
        usdc = new MockUSDC();
        factory = new BudgetFactory(address(usdc));
    }

    function test_createBusiness_callerBecomesApprover() public {
        vm.prank(owner1);
        address e = factory.createBusiness(agent1, 100, 10, 400);
        BudgetEnforcerV2 b = BudgetEnforcerV2(e);
        assertEq(b.approver(), owner1);
        assertEq(b.agent(), agent1);
        assertEq(b.usdc(), address(usdc));
        assertEq(b.dailyLimit(), 100);
        assertEq(b.weeklyLimit(), 400);
        assertEq(factory.enforcerOf(1), e);
        assertTrue(factory.isBusiness(e));
        assertEq(factory.businessCount(), 1);
    }

    function test_createBusiness_emitsExactEvent() public {
        vm.expectEmit(true, false, true, true);
        emit BusinessCreated(1, address(0), owner1, agent1);
        vm.prank(owner1);
        factory.createBusiness(agent1, 100, 10, 400);
    }

    function test_businesses_areIsolated() public {
        vm.prank(owner1);
        address e1 = factory.createBusiness(agent1, 100, 10, 400);
        vm.prank(owner2);
        address e2 = factory.createBusiness(agent2, 100, 10, 400);
        assertTrue(e1 != e2);
        assertEq(factory.businessCount(), 2);
        vm.prank(owner2);
        vm.expectRevert("Not approver");
        BudgetEnforcerV2(e1).setVendor(makeAddr("v"), true);
        vm.prank(agent1);
        vm.expectRevert("Not agent");
        BudgetEnforcerV2(e2).logDecision(keccak256("x"));
    }

    function test_createBusiness_rejectsInvalidInputs() public {
        vm.prank(owner1);
        vm.expectRevert("Roles must differ");
        factory.createBusiness(owner1, 100, 10, 400);
        vm.prank(owner1);
        vm.expectRevert("daily > weekly");
        factory.createBusiness(agent1, 100, 10, 50);
    }

    function test_factory_hasNoPowerOverEnforcers() public {
        vm.prank(owner1);
        address e = factory.createBusiness(agent1, 100, 10, 400);
        usdc.mint(e, 1000);
        vm.expectRevert("Not approver");
        BudgetEnforcerV2(e).withdraw(address(this), 1000);
        vm.prank(address(factory));
        vm.expectRevert("Not approver");
        BudgetEnforcerV2(e).withdraw(address(factory), 1000);
    }

    function test_constructor_rejectsZeroUsdc() public {
        vm.expectRevert("Zero address");
        new BudgetFactory(address(0));
    }
}
