/* CeedeBooks public proof page: verification logic. No dependencies. Everything shown to the user is set with
   textContent (decision text is untrusted input), and links are only built from validated hex strings. */
(function (root) {
  "use strict";

  var CONFIG = {
    api: "https://api.ceedebooks.xyz",
    enforcer: "0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB",
    chainId: "0x4cef52", // Arc Testnet, 5042002
    explorer: "https://explorer.testnet.arc.io",
    rpcs: [
      "https://rpc.testnet.arc.network",
      "https://rpc.testnet.arc.io",
      "https://rpc.blockdaemon.testnet.arc.network",
      "https://rpc.drpc.testnet.arc.network"
    ]
  };

  // topic0 = keccak256(event signature); `index` is the position of the indexed reasoningHash in topics.
  var TOPICS = {
    "0x1c9d044335a4a11ba5017f5152049ff125e54489e1f73345177810d658c624d0": { name: "PaymentMade", index: 2 },
    "0x8b958ddaf670e221a55a2f21042994d5613123b0230429b5763e2178979ed045": { name: "PaymentEscalated", index: 2 },
    "0x15eb229f5b2ce7ced26c2ceefe969a9c01e6e867545204be4053b0195c4fc4ec": { name: "DecisionLogged", index: 1 }
  };

  function normalizeHash(input) {
    var h = String(input == null ? "" : input).trim().toLowerCase().replace(/^0x/, "");
    return /^[0-9a-f]{64}$/.test(h) ? h : null;
  }

  function isTxHash(value) {
    return typeof value === "string" && /^0x[0-9a-fA-F]{64}$/.test(value);
  }

  async function sha256Hex(text) {
    var digest = await root.crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.prototype.map.call(new Uint8Array(digest), function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }

  // Check 1: the stored hash_input hashes to the claimed hash. Check 2: the readable record is exactly what was hashed.
  async function checkRecord(rec, hash) {
    var out = { hashMatches: false, fieldsMatch: false, problems: [] };
    if (!rec || typeof rec.hash_input !== "string") { out.problems.push("record has no hash_input"); return out; }
    out.hashMatches = (await sha256Hex(rec.hash_input)) === hash && rec.reasoning_hash === hash;
    if (!out.hashMatches) out.problems.push("SHA-256 of hash_input does not equal the hash");
    var parsed;
    try { parsed = JSON.parse(rec.hash_input); } catch (e) { out.problems.push("hash_input is not valid JSON"); return out; }
    var keys = ["action", "amount_usdc", "model_used", "reasoning", "subject", "timestamp"];
    var sameKeys = Object.keys(parsed).sort().join(",") === keys.join(",");
    var sameText = ["action", "subject", "reasoning", "model_used"].every(function (k) { return parsed[k] === rec[k]; });
    var sameNums = Number(parsed.amount_usdc) === Number(rec.amount_usdc) && Number(parsed.timestamp) === Number(rec.timestamp);
    out.fieldsMatch = sameKeys && sameText && sameNums;
    if (!out.fieldsMatch) out.problems.push("the displayed record differs from what was hashed");
    return out;
  }

  // Look through a transaction receipt for a BudgetEnforcer audit event carrying this reasoning hash.
  function findEvent(receipt, hash) {
    if (!receipt || !Array.isArray(receipt.logs)) return { found: false };
    var enforcer = CONFIG.enforcer.toLowerCase();
    for (var i = 0; i < receipt.logs.length; i++) {
      var log = receipt.logs[i];
      if (String(log.address).toLowerCase() !== enforcer || !log.topics || !log.topics.length) continue;
      var spec = TOPICS[String(log.topics[0]).toLowerCase()];
      if (!spec) continue;
      var topic = log.topics[spec.index];
      if (topic && String(topic).toLowerCase() === "0x" + hash) {
        return { found: true, event: spec.name, block: parseInt(receipt.blockNumber, 16), success: receipt.status === "0x1" };
      }
    }
    return { found: false };
  }

  async function rpcCall(url, method, params, fetchImpl) {
    var res = await fetchImpl(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: method, params: params })
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    var body = await res.json();
    if (body.error) throw new Error(body.error.message || "rpc error");
    return body.result;
  }

  // Ask each public RPC in turn. An endpoint is used only if it reports Arc Testnet's chain id.
  async function readReceipt(txHash, fetchImpl, rpcs) {
    var errors = [];
    var list = rpcs || CONFIG.rpcs;
    for (var i = 0; i < list.length; i++) {
      try {
        var id = await rpcCall(list[i], "eth_chainId", [], fetchImpl);
        if (String(id).toLowerCase() !== CONFIG.chainId) { errors.push(list[i] + ": wrong chain " + id); continue; }
        var receipt = await rpcCall(list[i], "eth_getTransactionReceipt", [txHash], fetchImpl);
        if (!receipt) { errors.push(list[i] + ": transaction not found"); continue; }
        return { receipt: receipt, rpc: list[i] };
      } catch (e) {
        errors.push(list[i] + ": " + (e && e.message ? e.message : "failed"));
      }
    }
    var err = new Error("no public RPC answered");
    err.details = errors;
    throw err;
  }

  var api = {
    CONFIG: CONFIG, TOPICS: TOPICS, normalizeHash: normalizeHash, isTxHash: isTxHash, sha256Hex: sha256Hex,
    checkRecord: checkRecord, findEvent: findEvent, readReceipt: readReceipt
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.CeedeVerify = api;
})(typeof window !== "undefined" ? window : globalThis);
