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

  // ---- hash format 2 (v1.2.8.1): a record from another business, produced by the real Python hashing code
  const r3 = {"action": "INVOICE_PAID", "subject": "INV-1", "reasoning": "Matched PO and receipt", "amount_usdc": 5.0, "model_used": "rules", "timestamp": 1759400000.5, "hash_input": "{\"action\":\"INVOICE_PAID\",\"amount_usdc\":5.0,\"business_id\":2,\"enforcer_address\":\"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"hash_version\":2,\"model_used\":\"rules\",\"reasoning\":\"Matched PO and receipt\",\"subject\":\"INV-1\",\"timestamp\":1759400000.5}", "reasoning_hash": "1869a4d81eba5c517c54a7d5ede760c02d98ea68683cd62376618d65ef4b9170", "business_id": 2, "hash_version": 2, "contract_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "contract_version": 2};
  const ok3 = await V.checkRecord(r3, r3.reasoning_hash);
  assert.ok(ok3.hashMatches && ok3.fieldsMatch && ok3.version === 2 && ok3.enforcer === "0x" + "bb".repeat(20), JSON.stringify(ok3));
  const okOld = await V.checkRecord(r1, r1.reasoning_hash);
  assert.ok(okOld.version === 1 && okOld.enforcer === V.CONFIG.enforcer.toLowerCase());          // old records still read as business 1
  assert.ok(!(await V.checkRecord({ ...r3, business_id: 3 }, r3.reasoning_hash)).fieldsMatch);          // displayed business differs from the hashed one
  assert.ok(!(await V.checkRecord({ ...r3, contract_address: "0x" + "cc".repeat(20) }, r3.reasoning_hash)).fieldsMatch);
  assert.ok(!(await V.checkRecord({ ...r3, hash_input: r3.hash_input.replace('"hash_version":2,', "") }, r3.reasoning_hash)).hashMatches);
  const mkAt = (addr) => ({ blockNumber: "0x2a", status: "0x1", logs: [{ address: addr, topics: [topicOf("PaymentMade"), zero, pad(r3.reasoning_hash), zero] }] });
  assert.ok(V.findEvent(mkAt("0x" + "bb".repeat(20)), r3.reasoning_hash, ok3.enforcer).found);
  assert.ok(!V.findEvent(mkAt(E), r3.reasoning_hash, ok3.enforcer).found);                              // business 1's events never confirm business 2's record
  assert.ok(!V.findEvent(mkAt("0x" + "bb".repeat(20)), r3.reasoning_hash).found);                       // and the default is still business 1 only
  const factoryAnswers = (result, chainId = V.CONFIG.chainId) => async (url, opt) => {
    const b = JSON.parse(opt.body);
    return { ok: true, json: async () => ({ result: b.method === "eth_chainId" ? chainId : result }) };
  };
  const yes = "0x" + "0".repeat(63) + "1", no = "0x" + "0".repeat(64);
  assert.strictEqual(await V.isKnownBusiness("0x" + "bb".repeat(20), factoryAnswers(yes), ["https://r"]), true);
  assert.strictEqual(await V.isKnownBusiness("0x" + "bb".repeat(20), factoryAnswers(no), ["https://r"]), false);
  assert.strictEqual(await V.isKnownBusiness(E, factoryAnswers(no), ["https://r"]), true);              // the original business needs no lookup
  assert.strictEqual(await V.isKnownBusiness("nope", factoryAnswers(yes), ["https://r"]), false);
  await assert.rejects(V.isKnownBusiness("0x" + "bb".repeat(20), factoryAnswers(yes, "0x1"), ["https://r"]));   // wrong chain: not trusted

  // ---- the Businesses section
  const rows = V.businessRows([
    { id: 1, name: "CeedeBooks", slug: "ceedebooks", contract: E, agent_address: "0x" + "a0".repeat(20), external: 0, contract_version: 1 },
    { id: 2, name: "Acme", slug: "acme", contract: "0x" + "bb".repeat(20), agent_address: "0x" + "a1".repeat(20), external: 1, contract_version: 2 },
    { id: 3, name: "Beta", slug: "beta", contract: "0x" + "cc".repeat(20), agent_address: "", external: 0, contract_version: 2 }], "https://x.test");
  assert.deepStrictEqual(rows.map((r) => [r.slug, r.kind, r.outside]), [["ceedebooks", "Builder's own", false], ["acme", "Outside business", true], ["beta", "Not yet confirmed as outside", false]]);
  assert.strictEqual(rows[1].contractUrl, "https://x.test/address/0x" + "bb".repeat(20));
  assert.strictEqual(rows[1].agentUrl, "https://x.test/address/0x" + "a1".repeat(20));
  assert.strictEqual(rows[2].agentUrl, null);
  assert.deepStrictEqual(V.businessRows(null, "https://x.test"), []);
  assert.strictEqual(V.businessRows([{ id: 1, name: "X", slug: "x", contract: E, agent_address: "", external: 1, contract_version: 1 }], "u")[0].outside, false, "the home business can never read as outside");

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
  // the per-business card: live and shadow stay apart, and the rate always carries its n
  const view = V.tractionView({ business: { kind: "outside" }, live: { paid: 1, held: 0, escalated: 2, volume_usdc: 3 },
    shadow: { paid: 2, held: 1, escalated: 0, awaiting_owner: 1, mirrored_usdc: 1, real_totals: { NGN: "4500.50" }, agreement: { agree: 3, disagree: 1, n: 4, rate: 0.75 } },
    latest_paid: [{ invoice: "SH-1", mode: "shadow", real_amount: "4500.50", real_currency: "NGN", reasoning_hash: "ab".repeat(32), tx_hash: "0x" + "cd".repeat(32) },
                  { invoice: "LV-1", mode: "live", reasoning_hash: null, tx_hash: null }] }, "https://x");
  assert.strictEqual(view.kind, "Outside business");
  assert.strictEqual(view.live, "1 paid, 0 held, 2 escalated, 3 USDC paid");
  assert.ok(/2 paid, 1 held, 0 escalated, 1 waiting for the owner, 1 USDC mirrored \(real bills: 4500.50 NGN\)/.test(view.shadow));
  assert.strictEqual(view.agreement, "75% agree: 3 of 4 verdicts");
  assert.strictEqual(view.paid[0].txUrl, "https://x/tx/0x" + "cd".repeat(32));
  assert.ok(/shadow, real bill 4500.50 NGN/.test(view.paid[0].label) && view.paid[1].txUrl === null && !view.paid[1].shadow);
  const none = V.tractionView({ business: { kind: "unconfirmed" }, live: { paid: 0, held: 0, escalated: 0, volume_usdc: 0 },
    shadow: { paid: 0, held: 0, escalated: 0, awaiting_owner: 0, mirrored_usdc: 0, real_totals: {}, agreement: { agree: 0, disagree: 0, n: 0, rate: null } }, latest_paid: [] }, "https://x");
  assert.strictEqual(none.agreement, "No verdicts yet");
  assert.strictEqual(none.kind, "Not yet confirmed as outside");
  console.log("JS_TESTS_OK");
})().catch((e) => { console.error(e); process.exit(1); });
