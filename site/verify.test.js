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
  // demo and manual invoices are named apart and never added into the live line
  assert.strictEqual(none.apart, "", "no apart line when there is nothing apart");
  const withApart = V.tractionView({ business: { kind: "own" }, live: { paid: 1, held: 0, escalated: 0, volume_usdc: 3 },
    not_traction: { manual: { paid: 1, held: 0, escalated: 0, volume_usdc: 2.2 }, demo: { paid: 1, held: 0, escalated: 2, volume_usdc: 0.01 } },
    shadow: { paid: 0, held: 0, escalated: 0, awaiting_owner: 0, mirrored_usdc: 0, real_totals: {}, agreement: { agree: 0, disagree: 0, n: 0, rate: null } }, latest_paid: [] }, "https://x");
  assert.strictEqual(withApart.live, "1 paid, 0 held, 0 escalated, 3 USDC paid");
  assert.ok(/^Shown apart, not counted above \(demo: 1 paid \(0.01 USDC\), 0 held, 2 escalated; manual: 1 paid \(2.2 USDC\)/.test(withApart.apart), withApart.apart);

  // ---- outcomes block: own and outside stay apart, the rate carries its n, a missing figure says so, manual and demo are shown apart
  const blk = (o) => Object.assign({ processed: 0, paid_by_agent: 0, released_by_person: 0, held: 0, decided_alone: 0,
    escalated: { waiting: 0, released: 0, rejected: 0, total: 0 }, person_agreement: { confirmed_agent: 0, overrode_agent: 0, n: 0, rate: null },
    duplicates_caught: 0, volume_usdc: 0, seconds_to_payment: { median: null, n: 0, without_timing: 0 } }, o);
  const oc = { outside_businesses: { statement: "Outside businesses: 0 confirmed. 1 active business, none confirmed as an outside party." },
    by_ownership: {
      own: { agent: blk({ processed: 6, paid_by_agent: 3, held: 1, decided_alone: 4, escalated: { waiting: 1, released: 0, rejected: 1, total: 2 },
                          person_agreement: { confirmed_agent: 1, overrode_agent: 0, n: 1, rate: 1 }, duplicates_caught: 1, volume_usdc: 3.5,
                          seconds_to_payment: { median: 42.5, n: 2, without_timing: 1 } }),
             manual: blk({ processed: 1 }), demo: blk({ processed: 2, duplicates_caught: 1 }) },
      outside: { agent: blk({}), manual: blk({}), demo: blk({}) } } };
  const ov = V.outcomesView(oc);
  assert.strictEqual(ov.statement, oc.outside_businesses.statement);
  assert.strictEqual(ov.rows.length, 1);                                  // no outside row until an outside business has traffic
  assert.ok(/^4 decided with no person \(3 paid, 1 held\)$/.test(ov.rows[0].alone));
  assert.ok(/2 escalated to a person \(1 waiting, 0 released, 1 rejected\)/.test(ov.rows[0].escalated));
  assert.ok(/100% of settled escalations rejected.*1 of 1/.test(ov.rows[0].agreement));
  assert.strictEqual(ov.rows[0].median, "42.5 s median, from 2 payments (1 earlier not timed)");
  assert.deepStrictEqual(ov.apart, ["1 manual invoice (own)", "2 demo invoices (own), 1 duplicate"]);
  oc.by_ownership.own.agent = blk({});
  const empty = V.outcomesView(oc);
  assert.strictEqual(empty.rows[0].agreement, "No escalation settled by a person yet");
  assert.strictEqual(empty.rows[0].median, "none timed yet");
  oc.by_ownership.outside.agent = blk({ processed: 1, seconds_to_payment: { median: 300, n: 1, without_timing: 0 } });
  const two = V.outcomesView(oc);
  assert.ok(two.rows.length === 2 && /^Outside businesses/.test(two.rows[1].label) && two.rows[1].median === "5 min median, from 1 payment");

  // ---- the whole ledger chain: a real Ed25519-signed chain built here, checked by the browser code
  const nodeCrypto = require("crypto"), fs = require("fs"), path = require("path");
  const sha = (x) => nodeCrypto.createHash("sha256").update(x).digest("hex");
  const kp = nodeCrypto.generateKeyPairSync("ed25519");
  const pub = Buffer.from(kp.publicKey.export({ format: "jwk" }).x, "base64url").toString("hex");
  const keyId = sha(Buffer.from(pub, "hex")).slice(0, 16);
  function makeChain(n, businessId) {
    const out = []; let prev = sha("ceedebooks-ledger-genesis:v1:business:" + businessId);
    for (let i = 0; i < n; i++) {
      const kind = i === 0 ? "genesis" : "decision";
      const body = JSON.stringify({ business_id: businessId, key_id: keyId, kind, prev_hash: prev, sequence: i, v: 1 });
      const entry_hash = sha(body);
      out.push({ sequence: i, kind, body, prev_hash: prev, entry_hash, key_id: keyId,
        signature: nodeCrypto.sign(null, Buffer.from("ceedebooks-ledger-v1\n" + body), kp.privateKey).toString("hex") });
      prev = entry_hash;
    }
    return out;
  }
  const word = (h) => "0x" + String(h).replace(/^0x/, "").padStart(64, "0");
  function world(entries, anchored, slug = "acme") {      // fake API + fake RPC; `anchored` = [sequence, head] the anchor contract holds
    const agent = "0x" + "a9".repeat(20), calls = [];
    return { calls, fetch: async (url, opts) => {
      const ok = (b) => ({ ok: true, status: 200, json: async () => b });
      if (url.includes("/ledger/entries")) {
        const q = new URL(url), after = Number(q.searchParams.get("after")), limit = Number(q.searchParams.get("limit"));
        assert.strictEqual(q.searchParams.get("business"), slug);
        const rest = entries.filter((e) => e.sequence > after);
        return ok({ entries: rest.slice(0, limit), more: rest.length > limit });
      }
      if (url.includes("/.well-known/ledger-key")) return ok({ public_key: pub });
      const req = JSON.parse(opts.body);
      if (req.method === "eth_chainId") return ok({ result: V.CONFIG.chainId });
      const { to, data } = req.params[0]; calls.push({ to: to.toLowerCase(), data });
      let result;
      if (data === V.SELECTORS.agent) result = word(agent);
      else if (data.startsWith(V.SELECTORS.lastSequence)) result = word(anchored[0].toString(16));
      else if (data.startsWith(V.SELECTORS.lastHead)) result = word(anchored[1]);
      assert.ok(data.length === 10 || data.endsWith(agent.slice(2)), "the anchor is read for the business's own agent");
      return ok({ result });
    } };
  }
  const run = (w, slug) => V.verifyWholeChain(slug || "acme", "0x" + "bb".repeat(20), w.fetch, { rpcs: ["https://rpc.test"] });

  const chain = makeChain(6, 7);
  let res = await run(world(chain, [5, chain[5].entry_hash]));
  assert.ok(res.ok && res.fullyVerified && res.anchor.status === "match" && res.anchor.after === 0, JSON.stringify(res));
  res = await run(world(chain, [3, chain[3].entry_hash]));
  assert.ok(res.fullyVerified && res.anchor.after === 2);                 // entries after the anchor are reported, not hidden
  res = await run(world(chain, [0, "00".repeat(32)]));
  assert.ok(res.ok && !res.fullyVerified && res.anchor.status === "none");
  res = await run(world(chain.slice(0, 4), [5, chain[5].entry_hash]));   // newest entries dropped, still a valid shorter chain
  assert.ok(res.chain.problems.length === 0 && !res.ok && res.anchor.status === "short");
  res = await run(world(chain, [4, "ab".repeat(32)]));
  assert.ok(!res.ok && res.anchor.status === "mismatch");
  const down = world(chain, [5, chain[5].entry_hash]);
  const flaky = { fetch: (u, o) => (u.includes("rpc.test") ? Promise.reject(new Error("down")) : down.fetch(u, o)) };
  res = await run(flaky);
  assert.ok(res.ok && !res.fullyVerified && res.anchor.status === "unreadable");   // unknown is never "verified"

  const edit = (i, f) => chain.map((e, k) => (k === i ? f({ ...e }) : e));
  const codes = async (entries) => (await V.checkChain(entries, pub, nodeCrypto.webcrypto.subtle)).problems.map((p) => p.code);
  assert.deepStrictEqual(await codes(chain), []);
  assert.ok((await codes(edit(2, (e) => ({ ...e, body: e.body.replace("sequence\":2", "sequence\":2,\"x\":1") })))).includes("bad_hash"));
  assert.ok((await codes(edit(2, (e) => ({ ...e, signature: chain[3].signature })))).includes("bad_signature"));
  assert.ok((await codes(chain.filter((e) => e.sequence !== 2))).includes("bad_sequence"));
  assert.ok((await codes([chain[0], chain[2], chain[3]])).includes("broken_link"));
  assert.ok((await codes(edit(1, (e) => ({ ...e, key_id: "0".repeat(16) })))).includes("wrong_key"));
  assert.ok((await codes(chain.slice(1))).includes("broken_link"));         // no genesis first
  const noEd = await V.checkChain(chain, pub, { importKey: async () => { throw new Error("unsupported"); } });
  assert.strictEqual(noEd.signatures, false);                               // reported, so it can never count as fully verified
  const old = world(chain, [5, chain[5].entry_hash]);
  res = await V.verifyWholeChain("acme", "0x" + "bb".repeat(20), old.fetch, { rpcs: ["https://rpc.test"], subtle: { importKey: async () => { throw new Error("x"); } } });
  assert.ok(res.ok && !res.fullyVerified);

  const big = makeChain(1203, 7);                                           // paged: 500 per request
  res = await run(world(big, [1202, big[1202].entry_hash]));
  assert.ok(res.fullyVerified && res.entries === 1203);

  const lastW = world(chain, [5, chain[5].entry_hash]); await run(lastW);
  assert.ok(lastW.calls.every((c) => c.to === "0x" + "bb".repeat(20) || c.to === V.CONFIG.anchor.toLowerCase()));
  const compiled = path.join(__dirname, "..", "out", "LedgerAnchor.sol", "LedgerAnchor.json");
  if (fs.existsSync(compiled)) {                                            // selectors must be the contract's own
    const ids = JSON.parse(fs.readFileSync(compiled)).methodIdentifiers;
    assert.strictEqual("0x" + ids["lastSequence(address)"], V.SELECTORS.lastSequence);
    assert.strictEqual("0x" + ids["lastHead(address)"], V.SELECTORS.lastHead);
    const be = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "out", "BudgetEnforcerV2.sol", "BudgetEnforcerV2.json"))).methodIdentifiers;
    assert.strictEqual("0x" + be["agent()"], V.SELECTORS.agent);
  }
  assert.strictEqual(V.CONFIG.anchor, "0x008217BeC86462E76126F94e50eBe68fb4a41444");

  // ---- hash format 3 (v1.2.9.3): rules versus model, produced by the real Python hashing code
  const [r4, r5] = [{"action": "INVOICE_PAID", "subject": "INV-9", "reasoning": "Matched PO and receipt", "amount_usdc": 5.0, "model_used": "rules", "timestamp": 1759400002.5, "hash_input": "{\"action\":\"INVOICE_PAID\",\"amount_usdc\":5.0,\"business_id\":2,\"comparison\":{\"agreed\":false,\"model_action\":\"hold\",\"model_confidence\":0.75,\"model_name\":\"m-1\",\"model_reasoning\":\"Runway looks short.\",\"rules_action\":\"pay\"},\"enforcer_address\":\"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"hash_version\":3,\"model_used\":\"rules\",\"reasoning\":\"Matched PO and receipt\",\"subject\":\"INV-9\",\"timestamp\":1759400002.5}", "reasoning_hash": "e05c491f8b49b3030b4f1124438070713cd378da03b8b2234a0da15e17ded2d8", "business_id": 2, "hash_version": 3, "contract_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "contract_version": 2, "rules_action": "pay", "model_action": "hold", "agreed": 0, "model_confidence": 0.75}, {"action": "INVOICE_PAID", "subject": "INV-9", "reasoning": "Matched PO and receipt", "amount_usdc": 5.0, "model_used": "rules", "timestamp": 1759400002.5, "hash_input": "{\"action\":\"INVOICE_PAID\",\"amount_usdc\":5.0,\"business_id\":2,\"comparison\":{\"agreed\":null,\"model_action\":null,\"model_confidence\":null,\"model_name\":null,\"model_reasoning\":null,\"rules_action\":\"pay\"},\"enforcer_address\":\"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"hash_version\":3,\"model_used\":\"rules\",\"reasoning\":\"Matched PO and receipt\",\"subject\":\"INV-9\",\"timestamp\":1759400002.5}", "reasoning_hash": "089d91d0a42382a525f1d9c73f3b1e2a9e0d1cff669b46c55e22ead3fd16b5e3", "business_id": 2, "hash_version": 3, "contract_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "contract_version": 2, "rules_action": "pay", "model_action": null, "agreed": null, "model_confidence": null}];
  const ok4 = await V.checkRecord(r4, r4.reasoning_hash);
  assert.ok(ok4.hashMatches && ok4.fieldsMatch && ok4.version === 3 && ok4.comparison.model_action === "hold" && ok4.comparison.agreed === false, JSON.stringify(ok4));
  const ok5 = await V.checkRecord(r5, r5.reasoning_hash);
  assert.ok(ok5.hashMatches && ok5.fieldsMatch && ok5.comparison.model_action === null && ok5.comparison.agreed === null);   // no model: null, not agreement
  assert.ok(!(await V.checkRecord({ ...r4, agreed: 1 }, r4.reasoning_hash)).fieldsMatch);                  // a flattering displayed column is caught
  assert.ok(!(await V.checkRecord({ ...r4, model_action: "pay" }, r4.reasoning_hash)).fieldsMatch);
  assert.ok(!(await V.checkRecord({ ...r4, model_confidence: 0.99 }, r4.reasoning_hash)).fieldsMatch);
  assert.ok(!(await V.checkRecord({ ...r5, agreed: 1 }, r5.reasoning_hash)).fieldsMatch);
  assert.ok(!(await V.checkRecord({ ...r4, rules_action: "hold" }, r4.reasoning_hash)).fieldsMatch);
  assert.ok(!(await V.checkRecord({ ...r4, hash_input: r4.hash_input.replace('"comparison":', '"comparisonX":') }, r4.reasoning_hash)).hashMatches);
  const h4 = JSON.parse(r4.hash_input); delete h4.comparison;
  assert.ok(!(await V.checkRecord({ ...r4, hash_input: JSON.stringify(h4) }, r4.reasoning_hash)).hashMatches);
  console.log("JS_TESTS_OK");
})().catch((e) => { console.error(e); process.exit(1); });
