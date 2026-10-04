// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
}

/// One business's spending authority: the agent proposes, this contract enforces.
contract BudgetEnforcerV2 {
    address public agent;
    address public approver;
    address public pendingApprover;
    address public immutable usdc;

    uint256 public dailyLimit;
    uint256 public perTxLimit;
    uint256 public weeklyLimit;
    bool public paused;

    mapping(uint8 => uint256) public categoryDailyLimit;
    mapping(uint256 => uint256) public spentOnDay;
    mapping(uint256 => mapping(uint8 => uint256)) public categorySpentOnDay;

    mapping(address => bool) public vendorApproved;
    mapping(bytes32 => bool) public paid;
    mapping(bytes32 => uint256) public commitBlock;

    enum EscStatus { None, Pending, Approved, Rejected }

    struct Escalation {
        address vendor;
        uint256 amount;
        uint8 category;
        EscStatus status;
        bytes32 docHash;
        bytes32 reasoningHash;
    }

    mapping(bytes32 => Escalation) public escalations;

    event DecisionCommitted(bytes32 indexed commitment);
    event DecisionLogged(bytes32 indexed reasoningHash, uint256 timestamp);
    event PaymentMade(
        bytes32 indexed invoiceKey,
        bytes32 indexed reasoningHash,
        address indexed vendor,
        uint256 amount,
        uint8 category,
        bytes32 docHash,
        uint256 timestamp
    );
    event PaymentEscalated(
        bytes32 indexed invoiceKey,
        bytes32 indexed reasoningHash,
        address vendor,
        uint256 amount,
        uint8 category,
        string reason
    );
    event EscalationApproved(
        bytes32 indexed invoiceKey,
        bytes32 indexed reasoningHash,
        address indexed vendor,
        uint256 amount,
        uint8 category,
        bytes32 docHash
    );
    event EscalationRejected(bytes32 indexed invoiceKey, bytes32 indexed reasoningHash);
    event EscalationReopened(bytes32 indexed invoiceKey, bytes32 indexed reasoningHash);
    event VendorSet(address indexed vendor, bool approved, address by);
    event BudgetSet(uint256 dailyLimit, uint256 perTxLimit, uint256 weeklyLimit);
    event CategoryLimitSet(uint8 indexed category, uint256 dailyLimit);
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
        address _usdc,
        uint256 _dailyLimit,
        uint256 _perTxLimit,
        uint256 _weeklyLimit
    ) {
        require(_agent != address(0) && _approver != address(0) && _usdc != address(0), "Zero address");
        require(_agent != _approver, "Roles must differ");
        agent = _agent;
        approver = _approver;
        usdc = _usdc;
        _setBudget(_dailyLimit, _perTxLimit, _weeklyLimit);
    }

    // ---- approver

    function setBudget(uint256 _dailyLimit, uint256 _perTxLimit, uint256 _weeklyLimit) external onlyApprover {
        _setBudget(_dailyLimit, _perTxLimit, _weeklyLimit);
    }

    function setCategoryDailyLimit(uint8 category, uint256 limit) external onlyApprover {
        categoryDailyLimit[category] = limit;
        emit CategoryLimitSet(category, limit);
    }

    function setVendor(address vendor, bool approved) external onlyApprover {
        require(vendor != address(0), "Zero address");
        vendorApproved[vendor] = approved;
        emit VendorSet(vendor, approved, msg.sender);
    }

    function setPaused(bool _paused) external onlyApprover {
        paused = _paused;
        emit PausedSet(_paused);
    }

    function withdraw(address to, uint256 amount) external onlyApprover {
        require(to != address(0), "Zero address");
        require(IERC20(usdc).transfer(to, amount), "Transfer failed");
        emit Withdrawn(to, amount);
    }

    function setAgent(address newAgent) external onlyApprover {
        require(newAgent != address(0), "Zero address");
        require(newAgent != approver, "Roles must differ");
        emit AgentChanged(agent, newAgent);
        agent = newAgent;
    }

    function proposeApprover(address next) external onlyApprover {
        require(next != address(0), "Zero address");
        require(next != agent, "Roles must differ");
        pendingApprover = next;
        emit ApproverProposed(next);
    }

    function acceptApprover() external {
        require(msg.sender == pendingApprover, "Not pending approver");
        require(msg.sender != agent, "Roles must differ");
        emit ApproverChanged(approver, msg.sender);
        approver = msg.sender;
        pendingApprover = address(0);
    }

    function approveEscalation(bytes32 key) external onlyApprover {
        Escalation storage e = escalations[key];
        require(e.status == EscStatus.Pending, "Not pending");
        require(vendorApproved[e.vendor], "Vendor not approved");
        require(!paid[key], "Already paid");

        e.status = EscStatus.Approved;
        paid[key] = true;
        require(IERC20(usdc).transfer(e.vendor, e.amount), "Transfer failed");
        emit EscalationApproved(key, e.reasoningHash, e.vendor, e.amount, e.category, e.docHash);
    }

    function rejectEscalation(bytes32 key) external onlyApprover {
        Escalation storage e = escalations[key];
        require(e.status == EscStatus.Pending, "Not pending");
        e.status = EscStatus.Rejected;
        emit EscalationRejected(key, e.reasoningHash);
    }

    /// Returns a pending escalation to the agent; approved and rejected ones stay final.
    function reopenEscalation(bytes32 key) external onlyApprover {
        Escalation storage e = escalations[key];
        require(e.status == EscStatus.Pending, "Not pending");
        bytes32 reasoningHash = e.reasoningHash;
        delete escalations[key];
        emit EscalationReopened(key, reasoningHash);
    }

    // ---- agent

    function commitDecision(bytes32 commitment) external onlyAgent {
        require(commitment != bytes32(0), "Empty commitment");
        require(commitBlock[commitment] == 0, "Already committed");
        commitBlock[commitment] = block.number;
        emit DecisionCommitted(commitment);
    }

    function logDecision(bytes32 reasoningHash) external onlyAgent {
        require(reasoningHash != bytes32(0), "No reasoning hash");
        emit DecisionLogged(reasoningHash, block.timestamp);
    }

    function pay(
        address vendor,
        uint256 amount,
        bytes32 invoiceNumber,
        bytes32 docHash,
        uint8 category,
        bytes32 reasoningHash
    ) external onlyAgent {
        require(!paused, "Paused");
        require(vendorApproved[vendor], "Vendor not approved");
        require(amount > 0, "Zero amount");
        require(invoiceNumber != bytes32(0), "No invoice number");
        require(reasoningHash != bytes32(0), "No reasoning hash");

        bytes32 key = invoiceKey(vendor, invoiceNumber);
        require(!paid[key], "Already paid");
        require(escalations[key].status == EscStatus.None, "Invoice escalated");
        require(amount <= perTxLimit, "Exceeds per-tx limit");

        _consumeCommit(commitmentFor(vendor, amount, invoiceNumber, docHash, category, reasoningHash));
        _spend(amount, category);

        paid[key] = true;
        require(IERC20(usdc).transfer(vendor, amount), "Transfer failed");
        emit PaymentMade(key, reasoningHash, vendor, amount, category, docHash, block.timestamp);
    }

    function escalate(
        address vendor,
        uint256 amount,
        bytes32 invoiceNumber,
        bytes32 docHash,
        uint8 category,
        bytes32 reasoningHash,
        string calldata reason
    ) external onlyAgent {
        require(vendor != address(0), "Zero vendor");
        require(amount > 0, "Zero amount");
        require(invoiceNumber != bytes32(0), "No invoice number");
        require(reasoningHash != bytes32(0), "No reasoning hash");

        bytes32 key = invoiceKey(vendor, invoiceNumber);
        require(!paid[key], "Already paid");
        require(escalations[key].status == EscStatus.None, "Already escalated");

        escalations[key] = Escalation(vendor, amount, category, EscStatus.Pending, docHash, reasoningHash);
        emit PaymentEscalated(key, reasoningHash, vendor, amount, category, reason);
    }

    // ---- internals

    function _setBudget(uint256 _dailyLimit, uint256 _perTxLimit, uint256 _weeklyLimit) internal {
        require(_perTxLimit > 0, "Zero per-tx");
        require(_perTxLimit <= _dailyLimit, "perTx > daily");
        require(_dailyLimit <= _weeklyLimit, "daily > weekly");
        dailyLimit = _dailyLimit;
        perTxLimit = _perTxLimit;
        weeklyLimit = _weeklyLimit;
        emit BudgetSet(_dailyLimit, _perTxLimit, _weeklyLimit);
    }

    function _consumeCommit(bytes32 commitment) internal {
        uint256 b = commitBlock[commitment];
        require(b != 0 && b < block.number, "No prior commit");
        delete commitBlock[commitment];
    }

    function _spentInWeek(uint256 day) internal view returns (uint256 total) {
        for (uint256 i = 0; i < 7 && i <= day; i++) {
            total += spentOnDay[day - i];
        }
    }

    function _spend(uint256 amount, uint8 category) internal {
        uint256 day = block.timestamp / 86400;
        require(spentOnDay[day] + amount <= dailyLimit, "Exceeds daily limit");
        require(_spentInWeek(day) + amount <= weeklyLimit, "Exceeds weekly limit");
        require(
            categorySpentOnDay[day][category] + amount <= categoryDailyLimit[category],
            "Exceeds category daily limit"
        );
        spentOnDay[day] += amount;
        categorySpentOnDay[day][category] += amount;
    }

    // ---- views

    function invoiceKey(address vendor, bytes32 invoiceNumber) public pure returns (bytes32) {
        return keccak256(abi.encode(vendor, invoiceNumber));
    }

    /// Bound to this contract and chain, so a commitment cannot be reused elsewhere.
    function commitmentFor(
        address vendor,
        uint256 amount,
        bytes32 invoiceNumber,
        bytes32 docHash,
        uint8 category,
        bytes32 reasoningHash
    ) public view returns (bytes32) {
        return keccak256(
            abi.encode(address(this), block.chainid, vendor, amount, invoiceNumber, docHash, category, reasoningHash)
        );
    }

    function remainingToday(uint8 category) external view returns (uint256 overall, uint256 inCategory) {
        uint256 day = block.timestamp / 86400;
        uint256 spent = spentOnDay[day];
        uint256 catSpent = categorySpentOnDay[day][category];
        overall = dailyLimit > spent ? dailyLimit - spent : 0;
        uint256 catLimit = categoryDailyLimit[category];
        inCategory = catLimit > catSpent ? catLimit - catSpent : 0;
    }

    function remainingThisWeek() external view returns (uint256) {
        uint256 spent = _spentInWeek(block.timestamp / 86400);
        return weeklyLimit > spent ? weeklyLimit - spent : 0;
    }
}
