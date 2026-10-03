const assert = require("assert");
const V = require("./verify.js");
// Records produced by agent/decision_log.compute_reasoning_hash (sorted keys, compact separators).
const [r1, r2] = [{"action": "INVOICE_PAID", "subject": "INV-1", "reasoning": "Matched PO and receipt, treasury healthy, vendor registered", "amount_usdc": 5.0, "model_used": "rules", "timestamp": 1759400000.123456, "hash_input": "{\"action\":\"INVOICE_PAID\",\"amount_usdc\":5.0,\"model_used\":\"rules\",\"reasoning\":\"Matched PO and receipt, treasury healthy, vendor registered\",\"subject\":\"INV-1\",\"timestamp\":1759400000.123456}", "reasoning_hash": "3a167f3ab8c8e6fe186e8d4a9a0815afb7c008ca1a5325ec2e695dcc5e39496b"}, {"action": "INVOICE_HELD", "subject": "NAIRA-\u00e9", "reasoning": "Runway is 3 days \u2014 holding \"non-critical\" spend", "amount_usdc": 2.2, "model_used": "manual", "timestamp": 1759400001.5, "hash_input": "{\"action\":\"INVOICE_HELD\",\"amount_usdc\":2.2,\"model_used\":\"manual\",\"reasoning\":\"Runway is 3 days \\u2014 holding \\\"non-critical\\\" spend\",\"subject\":\"NAIRA-\\u00e9\",\"timestamp\":1759400001.5}", "reasoning_hash": "bea64bfcc08c463db0a4ce8737b21c3f278fe44da3b90c2378b02699bfd88aad"}];
const T = V.TOPICS, E = V.CONFIG.enforcer;
const topicOf = (name) => Object.keys(T).find((k) => T[k].name === name);
const pad = (h) => "0x" + h;
const zero = "0x" + "00".repeat(32);
(async () => {
  assert.strictEqual(V.normalizeHash("  0x" + "AB".repeat(32) + " "), "ab".repeat(32));
  assert.strictEqual(V.normalizeHash("xyz"), null);
  assert.strictEqual(V.normalizeHash("ab".repeat(31)), null);
  assert.ok(V.isTxHash("0x" + "a".repeat(64)) && !V.isTxHash("0x12"));

  for (const r of [r1, r2]) {                       // includes float 5.0, non-ASCII and quotes
    const ok = await V.checkRecord(r, r.reasoning_hash);
    assert.ok(ok.hashMatches && ok.fieldsMatch, JSON.stringify(ok));
  }
  const bent = { ...r1, reasoning: "Matched PO and receipt, treasury healthy, vendor registered!" };
  const c1 = await V.checkRecord(bent, r1.reasoning_hash);
  assert.ok(c1.hashMatches && !c1.fieldsMatch);     // displayed text changed, hashed text did not
  const c2 = await V.checkRecord({ ...r1, hash_input: r1.hash_input.replace("5.0", "6.0") }, r1.reasoning_hash);
  assert.ok(!c2.hashMatches);                       // hashed text changed
  const c3 = await V.checkRecord({ ...r1, amount_usdc: 6.0 }, r1.reasoning_hash);
  assert.ok(!c3.fieldsMatch);
  assert.ok(!(await V.checkRecord(r1, "0".repeat(64))).hashMatches);

  const h = r1.reasoning_hash;
  const mkReceipt = (name, status = "0x1", addr = E) => ({
    blockNumber: "0x2a", status,
    logs: [{ address: "0xdead", topics: [pad("11".repeat(32))] },
           { address: addr, topics: name === "DecisionLogged" ? [topicOf(name), pad(h)] : [topicOf(name), zero, pad(h), zero] }]
  });
  for (const n of ["PaymentMade", "PaymentEscalated", "DecisionLogged"]) {
    const ev = V.findEvent(mkReceipt(n), h);
    assert.ok(ev.found && ev.event === n && ev.block === 42 && ev.success, n);
  }
  assert.ok(!V.findEvent(mkReceipt("PaymentMade", "0x1", "0x" + "9".repeat(40)), h).found);   // wrong contract
  assert.ok(!V.findEvent(mkReceipt("PaymentMade"), "f".repeat(64)).found);                    // other hash
  assert.ok(V.findEvent(mkReceipt("PaymentMade", "0x0"), h).success === false);               // reverted tx
  assert.ok(!V.findEvent({ logs: [] }, h).found && !V.findEvent(null, h).found);

  const calls = [];
  const fake = (plan) => async (url, opt) => {
    const m = JSON.parse(opt.body).method; calls.push(url + " " + m);
    const p = plan[url];
    if (p === "down") throw new Error("CORS");
    const result = m === "eth_chainId" ? p.chain : p.receipt;
    return { ok: true, json: async () => ({ result }) };
  };
  const rpcs = ["https://a", "https://b", "https://c"];
  const got = await V.readReceipt("0x" + "1".repeat(64), fake({
    "https://a": { chain: "0x1", receipt: {} },                 // wrong chain: must be skipped
    "https://b": "down",
    "https://c": { chain: "0x4cef52", receipt: mkReceipt("PaymentMade") }
  }), rpcs);
  assert.strictEqual(got.rpc, "https://c");
  assert.ok(!calls.some((c) => c === "https://a eth_getTransactionReceipt"));
  await assert.rejects(V.readReceipt("0x" + "1".repeat(64), fake({ "https://a": "down", "https://b": "down", "https://c": { chain: "0x4cef52", receipt: null } }), rpcs),
    (e) => e.details.length === 3);
  console.log("JS_TESTS_OK");
})().catch((e) => { console.error(e); process.exit(1); });
