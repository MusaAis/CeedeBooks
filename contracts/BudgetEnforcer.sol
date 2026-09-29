// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
}

/// @title BudgetEnforcer
/// @notice The agent's spending authority lives here, not in a prompt (Rule #7).
///
///  Funds are HELD BY THIS CONTRACT. The agent can only move them through pay(), and only
///  inside the limits below. The approver (a human key, kept off the server) sets the limits,
///  registers vendors, pauses the agent, resolves escalations and can withdraw funds.
///  The agent can do none of that.
///
///  What the contract enforces (every one has a test):
///   - vendor registry: only approver-registered addresses can be paid (wallet change control)
///   - hash-before-pay: pay() needs a commitDecision() from an EARLIER block, bound to the exact
///     payment parameters, single use. "Reasoned first, paid second" is provable from block order.
///   - idempotency: one payment per (vendor, invoiceNumber). A re-sent PDF with a different file
///     hash or a changed amount cannot pay twice. docHash is only a pointer emitted in the event.
///   - limits: per-tx, per-day, and per-category per-day (cumulative). An unset category is 0,
///     so it is fail-closed.
///   - escalation: the agent parks a payment; only the approver can pay it, exactly once.
///
///  The model's output is an INPUT to these calls, never a substitute for these checks (Rule #4).
///  Amounts are USDC in the 6-decimal ERC-20 interface (never native 18-decimal units).
contract BudgetEnforcer {
    address public agent;
    address public approver;
    address public pendingApprover;
    address public immutable usdc;

    uint256 public dailyLimit; // total USDC the agent may spend per UTC day
    uint256 public perTxLimit; // max USDC per single payment
    bool public paused;

    mapping(uint8 => uint256) public categoryDailyLimit; // 0 = blocked (fail-closed)
    mapping(uint256 => uint256) public spentOnDay; // keyed by UTC day number: no reset logic
    mapping(uint256 => mapping(uint8 => uint256)) public categorySpentOnDay;

    mapping(address => bool) public vendorApproved;
    mapping(bytes32 => bool) public paid; // invoiceKey => paid
    mapping(bytes32 => uint256) public commitBlock; // commitment => block it was committed in

    enum EscStatus { None, Pending, Approved, Rejected }

    struct Escalation {
        address vendor;
        uint256 amount;
        uint8 category;
        EscStatus status;
        bytes32 docHash;
        bytes32 reasoningHash;
    }

    mapping(bytes32 => Escalation) public escalations; // invoiceKey => escalation

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
    event VendorSet(address indexed vendor, bool approved, address by);
    event BudgetSet(uint256 dailyLimit, uint256 perTxLimit);
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

    constructor(address _agent, address _approver, address _usdc) {
        require(_agent != address(0) && _approver != address(0) && _usdc != address(0), "Zero address");
        agent = _agent;
        approver = _approver;
        usdc = _usdc;
    }

    // ------------------------------------------------------------------ approver

    function setBudget(uint256 _dailyLimit, uint256 _perTxLimit) external onlyApprover {
        require(_perTxLimit <= _dailyLimit, "perTx > daily");
        dailyLimit = _dailyLimit;
        perTxLimit = _perTxLimit;
        emit BudgetSet(_dailyLimit, _perTxLimit);
    }

    /// @notice Cumulative per-category ceiling per UTC day. Names live off-chain (agent/categories.py).
    function setCategoryDailyLimit(uint8 category, uint256 limit) external onlyApprover {
        categoryDailyLimit[category] = limit;
        emit CategoryLimitSet(category, limit);
    }

    /// @notice Vendor registry. A vendor wallet change is a new address the approver must register.
    function setVendor(address vendor, bool approved) external onlyApprover {
        require(vendor != address(0), "Zero address");
        vendorApproved[vendor] = approved;
        emit VendorSet(vendor, approved, msg.sender);
    }

    /// @notice Circuit breaker. While paused the agent cannot pay.
    function setPaused(bool _paused) external onlyApprover {
        paused = _paused;
        emit PausedSet(_paused);
    }

    /// @notice Approver can always pull funds back out.
    function withdraw(address to, uint256 amount) external onlyApprover {
        require(to != address(0), "Zero address");
        require(IERC20(usdc).transfer(to, amount), "Transfer failed");
        emit Withdrawn(to, amount);
    }

    /// @notice Rotate a compromised or replaced agent wallet.
    function setAgent(address newAgent) external onlyApprover {
        require(newAgent != address(0), "Zero address");
        emit AgentChanged(agent, newAgent);
        agent = newAgent;
    }

    /// @notice Two-step approver rotation so a typo cannot lock everyone out.
    function proposeApprover(address next) external onlyApprover {
        require(next != address(0), "Zero address");
        pendingApprover = next;
        emit ApproverProposed(next);
    }

    function acceptApprover() external {
        require(msg.sender == pendingApprover, "Not pending approver");
        emit ApproverChanged(approver, msg.sender);
        approver = msg.sender;
        pendingApprover = address(0);
    }

    /// @notice Pay an escalated invoice. Only the approver, only once. This is a human decision,
    ///         so it does not count against the agent's own daily limits.
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

    // --------------------------------------------------------------------- agent

    /// @notice Step 1 of a payment. Commits the hash of the exact payment BEFORE it is made.
    function commitDecision(bytes32 commitment) external onlyAgent {
        require(commitment != bytes32(0), "Empty commitment");
        require(commitBlock[commitment] == 0, "Already committed");
        commitBlock[commitment] = block.number;
        emit DecisionCommitted(commitment);
    }

    /// @notice Put the reasoning hash of a decision that moves no money (held, escalated,
    ///         treasury moves) on-chain, so the audit trail shows refusals as well as payments.
    function logDecision(bytes32 reasoningHash) external onlyAgent {
        require(reasoningHash != bytes32(0), "No reasoning hash");
        emit DecisionLogged(reasoningHash, block.timestamp);
    }

    /// @notice Step 2. Needs a matching commitDecision() from an earlier block; consumes it.
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

        paid[key] = true; // effects before interaction; a failed transfer reverts all of it
        require(IERC20(usdc).transfer(vendor, amount), "Transfer failed");
        emit PaymentMade(key, reasoningHash, vendor, amount, category, docHash, block.timestamp);
    }

    /// @notice Agent refuses to pay and parks the payment for a human.
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

    // ------------------------------------------------------------------ internals

    function _consumeCommit(bytes32 commitment) internal {
        uint256 b = commitBlock[commitment];
        require(b != 0 && b < block.number, "No prior commit");
        delete commitBlock[commitment];
    }

    function _spend(uint256 amount, uint8 category) internal {
        uint256 day = block.timestamp / 86400;
        require(spentOnDay[day] + amount <= dailyLimit, "Exceeds daily limit");
        require(
            categorySpentOnDay[day][category] + amount <= categoryDailyLimit[category],
            "Exceeds category daily limit"
        );
        spentOnDay[day] += amount;
        categorySpentOnDay[day][category] += amount;
    }

    // ---------------------------------------------------------------------- views

    /// @notice Dedupe key: derived from canonical fields, not from a file the agent supplies.
    ///         Amount is deliberately excluded so an altered amount cannot sneak a second payment.
    function invoiceKey(address vendor, bytes32 invoiceNumber) public pure returns (bytes32) {
        return keccak256(abi.encode(vendor, invoiceNumber));
    }

    function commitmentFor(
        address vendor,
        uint256 amount,
        bytes32 invoiceNumber,
        bytes32 docHash,
        uint8 category,
        bytes32 reasoningHash
    ) public pure returns (bytes32) {
        return keccak256(abi.encode(vendor, amount, invoiceNumber, docHash, category, reasoningHash));
    }

    /// @notice What the agent can still spend today, overall and in one category.
    function remainingToday(uint8 category) external view returns (uint256 overall, uint256 inCategory) {
        uint256 day = block.timestamp / 86400;
        uint256 spent = spentOnDay[day];
        uint256 catSpent = categorySpentOnDay[day][category];
        overall = dailyLimit > spent ? dailyLimit - spent : 0;
        uint256 catLimit = categoryDailyLimit[category];
        inCategory = catLimit > catSpent ? catLimit - catSpent : 0;
    }
}
