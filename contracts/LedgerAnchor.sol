// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// Public anchor for an off-chain ledger head. Trust only events from the business's agent address.
contract LedgerAnchor {
    mapping(address => uint256) public lastSequence;
    mapping(address => bytes32) public lastHead;

    event Anchored(address indexed sender, bytes32 head, uint256 sequence, uint256 timestamp);

    function anchor(bytes32 head, uint256 sequence) external {
        require(head != bytes32(0), "Empty head");
        require(sequence > lastSequence[msg.sender], "Stale sequence");
        lastSequence[msg.sender] = sequence;
        lastHead[msg.sender] = head;
        emit Anchored(msg.sender, head, sequence, block.timestamp);
    }
}
