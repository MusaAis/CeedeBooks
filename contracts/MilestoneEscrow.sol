// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// Holds USDC for one contractor job and releases it per milestone.
/// The agent proposes, this contract enforces, the approver can always override.
/// Milestones and their requirement hashes are fixed at deployment; funding is a plain USDC transfer.
contract MilestoneEscrow {
    enum Status { Pending, Released, Rejected }

    struct Milestone {
        uint256 amount;
        bytes32 requirementsHash;
        Status status;
    }

    uint256 public constant MAX_MILESTONES = 50;

    address public agent;
    address public approver;
    address public pendingApprover;
    address public immutable contractor;
    address public immutable usdc;
    /// Milestones above this amount can only be released by the approver.
    uint256 public immutable agentReleaseLimit;

    bool public paused;
    /// Sum of the amounts of Pending milestones; withdraw can never touch it.
    uint256 public pendingTotal;

    Milestone[] public milestones;
    mapping(bytes32 => uint256) public commitBlock;

    event DecisionCommitted(bytes32 indexed commitment);
    event MilestoneReleased(
        uint256 indexed index, bytes32 indexed reasoningHash, bytes32 evidenceHash, uint256 amount
    );
    event MilestoneRejected(uint256 indexed index, bytes32 indexed reasoningHash, address by);
    event MilestoneOverridden(uint256 indexed index, bytes32 evidenceHash, uint256 amount);
    event PausedSet(bool paused);
    event Withdrawn(address indexed to, uint256 amount);
    event AgentChanged(address indexed oldAgent, address indexed newAgent);
    event ApproverProposed(address indexed proposed);
    event ApproverChanged(address indexed oldApprover, address indexed newApprover);

    modifier onlyAgent() {
        require(msg.sender == agent, "Not agent");
        _;
    }

    modifier onlyApprover() {
        require(msg.sender == approver, "Not approver");
        _;
    }

    constructor(
        address _agent,
        address _approver,
        address _contractor,
        address _usdc,
        uint256 _agentReleaseLimit,
        uint256[] memory amounts,
        bytes32[] memory requirementsHashes
    ) {
        require(
            _agent != address(0) && _approver != address(0) && _contractor != address(0) && _usdc != address(0),
            "Zero address"
        );
        require(_agent != _approver && _agent != _contractor && _approver != _contractor, "Roles must differ");
        require(amounts.length > 0 && amounts.length <= MAX_MILESTONES, "Bad milestone count");
        require(amounts.length == requirementsHashes.length, "Length mismatch");

        agent = _agent;
        approver = _approver;
        contractor = _contractor;
        usdc = _usdc;
        agentReleaseLimit = _agentReleaseLimit;

        uint256 total;
        for (uint256 i = 0; i < amounts.length; i++) {
            require(amounts[i] > 0, "Zero amount");
            require(requirementsHashes[i] != bytes32(0), "No requirements hash");
            milestones.push(Milestone(amounts[i], requirementsHashes[i], Status.Pending));
            total += amounts[i];
        }
        pendingTotal = total;
    }

    // ---- agent

    function commitDecision(bytes32 commitment) external onlyAgent {
        require(commitment != bytes32(0), "Empty commitment");
        require(commitBlock[commitment] == 0, "Already committed");
        commitBlock[commitment] = block.number;
        emit DecisionCommitted(commitment);
    }

    function releaseMilestone(uint256 index, bytes32 evidenceHash, bytes32 reasoningHash) external onlyAgent {
        require(!paused, "Paused");
        Milestone storage m = _pending(index);
        require(m.amount <= agentReleaseLimit, "Needs approver");
        require(evidenceHash != bytes32(0), "No evidence hash");
        require(reasoningHash != bytes32(0), "No reasoning hash");

        _consumeCommit(commitmentFor(index, evidenceHash, reasoningHash));
        pendingTotal -= m.amount;
        _pay(m);
        emit MilestoneReleased(index, reasoningHash, evidenceHash, m.amount);
    }

    function rejectMilestone(uint256 index, bytes32 reasoningHash) external {
        require(msg.sender == agent || msg.sender == approver, "Not agent or approver");
        if (msg.sender == agent) require(!paused, "Paused");
        require(reasoningHash != bytes32(0), "No reasoning hash");
        Milestone storage m = _pending(index);
        m.status = Status.Rejected;
        pendingTotal -= m.amount;
        emit MilestoneRejected(index, reasoningHash, msg.sender);
    }

    // ---- approver (works while paused)

    /// Release a Pending or Rejected milestone; the only way to release one above agentReleaseLimit.
    function overrideRelease(uint256 index, bytes32 evidenceHash) external onlyApprover {
        require(index < milestones.length, "Bad index");
        Milestone storage m = milestones[index];
        require(m.status != Status.Released, "Already released");
        if (m.status == Status.Pending) pendingTotal -= m.amount;
        _pay(m);
        emit MilestoneOverridden(index, evidenceHash, m.amount);
    }

    /// Only the part of the balance not reserved for Pending milestones can leave.
    function withdraw(address to, uint256 amount) external onlyApprover {
        require(to != address(0), "Zero address");
        require(amount <= withdrawable(), "Exceeds unreserved balance");
        require(IERC20(usdc).transfer(to, amount), "Transfer failed");
        emit Withdrawn(to, amount);
    }

    function setPaused(bool _paused) external onlyApprover {
        paused = _paused;
        emit PausedSet(_paused);
    }

    function setAgent(address newAgent) external onlyApprover {
        require(newAgent != address(0), "Zero address");
        require(newAgent != approver && newAgent != contractor, "Roles must differ");
        emit AgentChanged(agent, newAgent);
        agent = newAgent;
    }

    function proposeApprover(address next) external onlyApprover {
        require(next != address(0), "Zero address");
        require(next != agent && next != contractor, "Roles must differ");
        pendingApprover = next;
        emit ApproverProposed(next);
    }

    function acceptApprover() external {
        require(msg.sender == pendingApprover, "Not pending approver");
        require(msg.sender != agent && msg.sender != contractor, "Roles must differ");
        emit ApproverChanged(approver, msg.sender);
        approver = msg.sender;
        pendingApprover = address(0);
    }

    // ---- internals

    function _pending(uint256 index) internal view returns (Milestone storage m) {
        require(index < milestones.length, "Bad index");
        m = milestones[index];
        require(m.status == Status.Pending, "Not pending");
    }

    function _pay(Milestone storage m) internal {
        m.status = Status.Released;
        require(IERC20(usdc).transfer(contractor, m.amount), "Transfer failed");
    }

    function _consumeCommit(bytes32 commitment) internal {
        uint256 b = commitBlock[commitment];
        require(b != 0 && b < block.number, "No prior commit");
        delete commitBlock[commitment];
    }

    // ---- views

    function milestoneCount() external view returns (uint256) {
        return milestones.length;
    }

    function withdrawable() public view returns (uint256) {
        uint256 bal = IERC20(usdc).balanceOf(address(this));
        return bal > pendingTotal ? bal - pendingTotal : 0;
    }

    /// Bound to this contract, chain and milestone, so a commitment cannot be reused elsewhere.
    function commitmentFor(uint256 index, bytes32 evidenceHash, bytes32 reasoningHash) public view returns (bytes32) {
        return keccak256(abi.encode(address(this), block.chainid, index, evidenceHash, reasoningHash));
    }
}
