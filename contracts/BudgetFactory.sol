// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {BudgetEnforcerV2} from "./BudgetEnforcerV2.sol";

/// Creates one enforcer per business; the caller becomes its approver. No admin, holds no funds.
contract BudgetFactory {
    address public immutable usdc;
    uint256 public businessCount;
    mapping(uint256 => address) public enforcerOf;
    mapping(address => bool) public isBusiness;

    event BusinessCreated(uint256 indexed id, address indexed enforcer, address indexed approver, address agent);

    constructor(address _usdc) {
        require(_usdc != address(0), "Zero address");
        usdc = _usdc;
    }

    function createBusiness(address agent, uint256 dailyLimit, uint256 perTxLimit, uint256 weeklyLimit)
        external
        returns (address enforcer)
    {
        uint256 id = ++businessCount;
        enforcer = address(new BudgetEnforcerV2(agent, msg.sender, usdc, dailyLimit, perTxLimit, weeklyLimit));
        enforcerOf[id] = enforcer;
        isBusiness[enforcer] = true;
        emit BusinessCreated(id, enforcer, msg.sender, agent);
    }
}
