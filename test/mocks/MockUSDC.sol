// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Minimal 6-decimal ERC-20 for tests. Deliberately returns false (not revert) on
/// insufficient balance, matching real USDC's behavior, so BudgetEnforcer's own checked
/// `require(transfer(...))` is what's actually under test.
contract MockUSDC {
    string public constant name = "Mock USDC";
    string public constant symbol = "USDC";
    uint8 public constant decimals = 6;

    mapping(address => uint256) public balanceOf;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        if (balanceOf[msg.sender] < amount) return false;
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}
