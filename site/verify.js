/* CeedeBooks public proof page: verification logic. No dependencies. Everything shown to the user is set with
   textContent (decision text is untrusted input), and links are only built from validated hex strings. */
(function (root) {
  "use strict";

  var CONFIG = {
    api: "https://api.ceedebooks.xyz",
    enforcer: "0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB", // the original business (v1 contract)
    factory: "0x97b9A3802bA6B258cBeF6070532a265656bb391C",   // every other business's contract is created by this factory
    anchor: "0x008217BeC86462E76126F94e50eBe68fb4a41444",    // LedgerAnchor: each agent wallet anchors its business's ledger head here
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
    // Hash format 1 is the six fields. Format 2 (v1.2.8.1) also binds the business and its contract. Format 3 (v1.2.9.3) adds
    // the rules-versus-model comparison.
    var v3 = parsed.hash_version === 3, v2 = parsed.hash_version === 2 || v3;
    var keys = ["action", "amount_usdc", "model_used", "reasoning", "subject", "timestamp"];
    if (v2) keys = keys.concat(["business_id", "enforcer_address", "hash_version"]);
    if (v3) keys.push("comparison");
    keys.sort();
    var sameKeys = Object.keys(parsed).sort().join(",") === keys.join(",");
    if (v2) {
      sameKeys = sameKeys && parsed.business_id === rec.business_id &&
        String(parsed.enforcer_address).toLowerCase() === String(rec.contract_address || "").toLowerCase();
    }
    out.enforcer = v2 ? String(parsed.enforcer_address).toLowerCase() : CONFIG.enforcer.toLowerCase();
    out.version = v3 ? 3 : v2 ? 2 : 1;
    if (v3) {
      var c = parsed.comparison, ck = ["agreed", "model_action", "model_confidence", "model_name", "model_reasoning", "rules_action"];
      var cmpOk = !!c && typeof c === "object" && Object.keys(c).sort().join(",") === ck.join(",") &&
        c.rules_action === rec.rules_action && c.model_action === rec.model_action && c.model_confidence === rec.model_confidence &&
        (c.agreed === null ? rec.agreed === null : Number(rec.agreed) === (c.agreed ? 1 : 0)) &&
        (c.model_action === null) === (c.agreed === null);
      out.comparison = cmpOk ? c : null;
      sameKeys = sameKeys && cmpOk;
    }
    var sameText = ["action", "subject", "reasoning", "model_used"].every(function (k) { return parsed[k] === rec[k]; });
    var sameNums = Number(parsed.amount_usdc) === Number(rec.amount_usdc) && Number(parsed.timestamp) === Number(rec.timestamp);
    out.fieldsMatch = sameKeys && sameText && sameNums;
    if (!out.fieldsMatch) out.problems.push("the displayed record differs from what was hashed");
    return out;
  }

  // Look through a transaction receipt for an audit event, from THIS business's contract, carrying this reasoning hash.
  function findEvent(receipt, hash, contractAddress) {
    if (!receipt || !Array.isArray(receipt.logs)) return { found: false };
    var enforcer = String(contractAddress || CONFIG.enforcer).toLowerCase();
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

  // Is this address a business contract made by the CeedeBooks factory? (The original business is built in.) Without this a
  // record could name any contract that emits a matching event. Throws if no public RPC answered.
  async function isKnownBusiness(enforcer, fetchImpl, rpcs) {
    var addr = String(enforcer || "").toLowerCase();
    if (!/^0x[0-9a-f]{40}$/.test(addr)) return false;
    if (addr === CONFIG.enforcer.toLowerCase()) return true;
    var list = rpcs || CONFIG.rpcs;
    for (var i = 0; i < list.length; i++) {
      try {
        var id = await rpcCall(list[i], "eth_chainId", [], fetchImpl);
        if (String(id).toLowerCase() !== CONFIG.chainId) continue;
        var data = "0x1466d019" + "0".repeat(24) + addr.slice(2);   // isBusiness(address)
        var out = await rpcCall(list[i], "eth_call", [{ to: CONFIG.factory, data: data }, "latest"], fetchImpl);
        return /^0x0*1$/.test(String(out));
      } catch (e) { /* try the next endpoint */ }
    }
    throw new Error("no public RPC answered");
  }

  // What the "Businesses" section shows for each active business: who it is for, and links to check it on the explorer.
  // `external` is set only by the operator. The home business (id 1) is always the builder's own.
  function businessRows(list, explorer) {
    return (list || []).map(function (b) {
      var own = b.id === 1 || !b.external;
      return {
        slug: b.slug, name: b.name,
        kind: b.id === 1 ? "Builder's own" : (b.external ? "Outside business" : "Not yet confirmed as outside"),
        outside: !own,
        contractUrl: explorer + "/address/" + b.contract,
        agentUrl: b.agent_address ? explorer + "/address/" + b.agent_address : null,
        contract: b.contract, agent: b.agent_address || null, version: b.contract_version
      };
    });
  }

  // Manual and demo invoices are never traction: the card names them in one line beside the figures, or says nothing when there are none.
  function apartText(na) {
    var parts = [];
    ["demo", "manual"].forEach(function (k) {
      var c = na && na[k];
      if (c && (c.paid + c.held + c.escalated) > 0) parts.push(k + ": " + c.paid + " paid (" + c.volume_usdc + " USDC), " + c.held + " held, " + c.escalated + " escalated");
    });
    return parts.length ? "Shown apart, not counted above (" + parts.join("; ") + ")" : "";
  }

  // What the per-business traction card shows. Live and shadow are never added together; the agreement rate always
  // carries how many verdicts it rests on, and says so plainly when there are none.
  function tractionView(t, explorer) {
    var ag = t.shadow.agreement, sh = t.shadow, live = t.live;
    var real = Object.keys(sh.real_totals).map(function (c) { return sh.real_totals[c] + " " + c; }).join(", ");
    return {
      kind: { own: "Builder's own", outside: "Outside business", unconfirmed: "Not yet confirmed as outside" }[t.business.kind] || t.business.kind,
      live: live.paid + " paid, " + live.held + " held, " + live.escalated + " escalated, " + live.volume_usdc + " USDC paid",
      shadow: sh.paid + " paid, " + sh.held + " held, " + sh.escalated + " escalated, " + sh.awaiting_owner + " waiting for the owner, " + sh.mirrored_usdc +
        " USDC mirrored" + (real ? " (real bills: " + real + ")" : ""),
      agreement: ag.n ? Math.round(ag.rate * 100) + "% agree: " + ag.agree + " of " + ag.n + " verdicts" : "No verdicts yet",
      apart: apartText(t.not_traction),
      paid: (t.latest_paid || []).map(function (p) {
        return { label: p.invoice + (p.mode === "shadow" ? " (shadow" + (p.real_amount ? ", real bill " + p.real_amount + " " + p.real_currency : "") + ")" : ""),
                 hash: p.reasoning_hash, txUrl: p.tx_hash ? explorer + "/tx/" + p.tx_hash : null, shadow: p.mode === "shadow" };
      })
    };
  }

  // The Outcomes block. Every sentence is built from the API's figures; nothing is rounded up and a missing rate says so.
  // Own and outside businesses are separate rows; only the agent origin is headline traction, the rest are shown apart.
  function outcomesView(o) {
    function secs(x) { return x == null ? "n/a" : (x >= 120 ? Math.round(x / 60) + " min" : x + " s"); }
    function row(label, b) {
      var pa = b.person_agreement, st = b.seconds_to_payment;
      return {
        label: label,
        processed: b.processed,
        alone: b.decided_alone + " decided with no person (" + b.paid_by_agent + " paid, " + b.held + " held)",
        escalated: b.escalated.total + " escalated to a person (" + b.escalated.waiting + " waiting, " + b.escalated.released + " released, " + b.escalated.rejected + " rejected)",
        agreement: pa.n ? Math.round(pa.rate * 100) + "% of settled escalations rejected, so the agent's caution stood: " + pa.confirmed_agent + " of " + pa.n : "No escalation settled by a person yet",
        duplicates: b.duplicates_caught,
        volume: b.volume_usdc,
        median: st.n ? secs(st.median) + " median, from " + st.n + " payment" + (st.n === 1 ? "" : "s") + (st.without_timing ? " (" + st.without_timing + " earlier not timed)" : "")
                     : "none timed yet" + (st.without_timing ? " (" + st.without_timing + " earlier not timed)" : "")
      };
    }
    var rows = [row("Builder's own businesses, agent traffic", o.by_ownership.own.agent)];
    var oc = o.by_ownership.outside.agent;
    rows.push(oc.processed || oc.duplicates_caught ? row("Outside businesses, agent traffic", oc) : null);
    var apart = [];
    ["own", "outside"].forEach(function (k) {
      ["manual", "demo"].forEach(function (origin) {
        var b = o.by_ownership[k][origin];
        if (b.processed || b.duplicates_caught) apart.push(b.processed + " " + origin + " invoice" + (b.processed === 1 ? "" : "s") + " (" + k + ")" +
          (b.duplicates_caught ? ", " + b.duplicates_caught + " duplicate" + (b.duplicates_caught === 1 ? "" : "s") : ""));
      });
    });
    return { statement: o.outside_businesses.statement, rows: rows.filter(Boolean), apart: apart };
  }

  // ---- the whole ledger chain
  // Selectors of agent() on a business contract and of lastSequence(address) / lastHead(address) on LedgerAnchor.
  var SELECTORS = { agent: "0xf5ff5c76", lastSequence: "0x0375b443", lastHead: "0x5f1eb783" };
  var SIGN_PREFIX = "ceedebooks-ledger-v1\n";

  function hexToBytes(hex) {
    var out = new Uint8Array(hex.length / 2);
    for (var i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
    return out;
  }

  // One read-only eth_call, on the first public RPC that reports Arc Testnet's chain id.
  async function ethCall(to, data, fetchImpl, rpcs) {
    var list = rpcs || CONFIG.rpcs;
    for (var i = 0; i < list.length; i++) {
      try {
        var id = await rpcCall(list[i], "eth_chainId", [], fetchImpl);
        if (String(id).toLowerCase() !== CONFIG.chainId) continue;
        return await rpcCall(list[i], "eth_call", [{ to: to, data: data }, "latest"], fetchImpl);
      } catch (e) { /* try the next endpoint */ }
    }
    throw new Error("no public RPC answered");
  }

  // What the chain holds for this business: its agent address, and the newest head that agent anchored.
  async function readAnchor(contract, fetchImpl, rpcs) {
    var word = function (r) { return String(r || "").replace(/^0x/, "").padStart(64, "0"); };
    var agent = "0x" + word(await ethCall(contract, SELECTORS.agent, fetchImpl, rpcs)).slice(24);
    var arg = word(agent).slice(24).padStart(64, "0");
    var seq = word(await ethCall(CONFIG.anchor, SELECTORS.lastSequence + arg, fetchImpl, rpcs));
    var head = word(await ethCall(CONFIG.anchor, SELECTORS.lastHead + arg, fetchImpl, rpcs));
    return { agent: agent.toLowerCase(), sequence: Number(BigInt("0x" + seq)), head: head.toLowerCase() };
  }

  // Re-checks every entry: hash, sequence, link, signature. Returns a list of problems (empty means intact) and says
  // whether the signature check could run at all (older browsers lack Ed25519).
  async function checkChain(entries, publicKey, subtle) {
    var problems = [], signatures = true;
    var bad = function (seq, code, detail) { problems.push({ sequence: seq, code: code, detail: detail }); };
    var keyId = (await sha256Bytes(hexToBytes(publicKey))).slice(0, 16);
    var key = null;
    try { key = await subtle.importKey("raw", hexToBytes(publicKey), { name: "Ed25519" }, false, ["verify"]); }
    catch (e) { signatures = false; }
    var previous = null, businessId = null;
    for (var i = 0; i < entries.length; i++) {
      var e = entries[i], body = null;
      try { body = JSON.parse(e.body); } catch (x) { /* handled below */ }
      if (!body || typeof body !== "object") { bad(e.sequence, "bad_body", "the signed body is not readable"); previous = e.entry_hash; continue; }
      if (i === 0) businessId = body.business_id;
      if (e.sequence !== i || body.sequence !== i) bad(e.sequence, "bad_sequence", "position " + i + " holds sequence " + e.sequence);
      if (body.business_id !== businessId) bad(e.sequence, "bad_body", "the entry belongs to a different business");
      if (body.prev_hash !== e.prev_hash || body.kind !== e.kind || body.key_id !== e.key_id) bad(e.sequence, "bad_body", "the signed body does not match the entry's columns");
      if ((await sha256Hex(e.body)) !== e.entry_hash) bad(e.sequence, "bad_hash", "the entry hash is not the hash of its body");
      var wantPrev = i === 0 ? await sha256Hex("ceedebooks-ledger-genesis:v1:business:" + businessId) : previous;
      if (e.prev_hash !== wantPrev) bad(e.sequence, "broken_link", i ? "the entry does not continue the one before it" : "the first entry is not a genesis entry");
      if (e.key_id !== keyId) bad(e.sequence, "wrong_key", "signed under a key other than the published ledger key");
      else if (key) {
        var okSig = false;
        try { okSig = await subtle.verify({ name: "Ed25519" }, key, hexToBytes(e.signature), new TextEncoder().encode(SIGN_PREFIX + e.body)); } catch (x) { okSig = false; }
        if (!okSig) bad(e.sequence, "bad_signature", "the signature does not verify under the published ledger key");
      }
      if ((e.kind === "genesis") !== (i === 0)) bad(e.sequence, "bad_genesis", "a genesis entry must be first, and only first");
      previous = e.entry_hash;
    }
    return { problems: problems, signatures: signatures, keyId: keyId };
  }

  async function sha256Bytes(bytes) {
    var d = await root.crypto.subtle.digest("SHA-256", bytes);
    return Array.prototype.map.call(new Uint8Array(d), function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }

  // Compares the chain with what is anchored on-chain for this business's agent. Same statuses as the API's /ledger/verify.
  function compareAnchor(entries, onchain) {
    if (!onchain.sequence) return { status: "none" };
    var mine = null;
    for (var i = 0; i < entries.length; i++) if (entries[i].sequence === onchain.sequence) mine = entries[i];
    if (!mine) return { status: "short", detail: "the chain has no entry " + onchain.sequence + ", but it was anchored on-chain" };
    if (mine.entry_hash.toLowerCase() !== onchain.head) return { status: "mismatch", detail: "entry " + onchain.sequence + " is not what was anchored on-chain" };
    return { status: "match", after: entries[entries.length - 1].sequence - onchain.sequence };
  }

  async function fetchAllEntries(slug, fetchImpl, apiBase) {
    var all = [], after = -1;
    for (var page = 0; page < 200; page++) {
      var res = await fetchImpl((apiBase || CONFIG.api) + "/ledger/entries?business=" + encodeURIComponent(slug) + "&after=" + after + "&limit=500");
      if (!res.ok) throw new Error("HTTP " + res.status);
      var data = await res.json();
      all = all.concat(data.entries);
      if (!data.more || !data.entries.length) return all;
      after = data.entries[data.entries.length - 1].sequence;
    }
    throw new Error("ledger too long to check here");
  }

  // The button: everything above, end to end. `contract` is the business's enforcer address from /businesses.
  async function verifyWholeChain(slug, contract, fetchImpl, opts) {
    opts = opts || {};
    var subtle = opts.subtle || root.crypto.subtle;
    var entries = await fetchAllEntries(slug, fetchImpl, opts.api);
    var kres = await fetchImpl((opts.api || CONFIG.api) + "/.well-known/ledger-key");
    if (!kres.ok) throw new Error("HTTP " + kres.status);
    var publicKey = (await kres.json()).public_key;
    var chain = await checkChain(entries, publicKey, subtle);
    var anchor = { status: "unreadable" }, onchain = null;
    try { onchain = await readAnchor(contract, fetchImpl, opts.rpcs); anchor = compareAnchor(entries, onchain); }
    catch (e) { /* the chain could not be read: unknown, not verified */ }
    var ok = chain.problems.length === 0 && anchor.status !== "mismatch" && anchor.status !== "short";
    return { entries: entries.length, chain: chain, anchor: anchor, onchain: onchain,
             ok: ok, fullyVerified: ok && chain.signatures && anchor.status === "match" };
  }

  var api = {
    tractionView: tractionView, outcomesView: outcomesView, CONFIG: CONFIG, TOPICS: TOPICS, normalizeHash: normalizeHash, isTxHash: isTxHash, sha256Hex: sha256Hex,
    checkRecord: checkRecord, findEvent: findEvent, readReceipt: readReceipt, isKnownBusiness: isKnownBusiness, businessRows: businessRows,
    SELECTORS: SELECTORS, checkChain: checkChain, compareAnchor: compareAnchor, readAnchor: readAnchor, verifyWholeChain: verifyWholeChain
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.CeedeVerify = api;
})(typeof window !== "undefined" ? window : globalThis);
