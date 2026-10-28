// Run: node admin/admin.test.js            (default business)
//      BIZ=acme node admin/admin.test.js   (the same flows for /?business=acme)
// Fake DOM, mocked wallet and API: checks the sign-in, approval and operator flows end to end.
const assert = require("assert");
const W = require("./wallet.js");

function mk(tag) {
  const n = { tag, children: [], className: "", _text: "", attrs: {}, listeners: {}, value: "" };
  Object.defineProperty(n, "textContent", { get() { return n._text + n.children.map((c) => c.textContent).join(""); }, set(v) { n._text = String(v); n.children = []; } });
  n.appendChild = (c) => { n.children.push(c); return c; };
  n.addEventListener = (e, f) => { n.listeners[e] = f; };
  n.setAttribute = (k, v) => { n.attrs[k] = v; }; n.getAttribute = (k) => n.attrs[k];
  n.remove = () => {};
  return n;
}
const walk = (n, f) => { f(n); (n.children || []).forEach((c) => walk(c, f)); };
const findBtn = (n, text) => { let hit = null; walk(n, (x) => { if (!hit && x.tag === "button" && x.textContent === text) hit = x; }); return hit; };
const root = mk("div"), dlg = mk("dialog");
dlg.close = () => {};
dlg.showModal = () => { // the user presses "Continue to wallet"
  let acts = null; walk(dlg, (x) => { if (x.className === "acts") acts = x; });
  setImmediate(() => acts.children[1].listeners.click());
};
const byId = (id) => { let hit = null; walk(root, (x) => { if (!hit && x.attrs && x.attrs.id === id) hit = x; }); return hit; };
global.document = { getElementById: (id) => (id === "root" ? root : id === "dlg" ? dlg : byId(id)), createElement: mk, createTextNode: (t) => ({ textContent: String(t), children: [] }), body: mk("body") };
const ADMIN = "0x" + "aa".repeat(20), OTHER = "0x" + "bb".repeat(20);
const BIZ = process.env.BIZ || null, ACME_CONTRACT = "0x" + "c9".repeat(20);
if (BIZ) global.location = { search: "?business=" + BIZ };
let dryMerged = true;
const ESC = "0x" + "e5".repeat(20);
let escrowsList = [{ id: 1, address: ESC, chain_ok: true, escrow: { balance_usdc: "1", pending_usdc: "0.8", agent_release_limit_usdc: "0.25", paused: false, contractor: "0x" + "c4".repeat(20),
  milestones: [{ index: 0, amount_usdc: "0.1", requirements_hash: "0x" + "11".repeat(32), status: "pending", agent_can_release: true },
               { index: 1, amount_usdc: "0.5", requirements_hash: "0x" + "22".repeat(32), status: "pending", agent_can_release: false },
               { index: 2, amount_usdc: "0.2", requirements_hash: "0x" + "33".repeat(32), status: "released", agent_can_release: false }] } }];
let operator = true, stateUrls = [], challengeBodies = [], pauseState = { paused: false, reason: null, by: null }, escalationUrls = [];
let account = ADMIN, state = { approver: ADMIN, pending_approver: "0x" + "00".repeat(20), contract: ACME_CONTRACT }, calls = [], posts = [], expireSession = false;
const provider = { request: async ({ method, params }) => {
  calls.push({ method, params });
  if (method === "eth_requestAccounts") return [account];
  if (method === "eth_chainId") return "0x4cef52";
  if (method === "personal_sign") return "0x" + "11".repeat(65);
  if (method === "eth_sendTransaction") return "0x" + "cd".repeat(32);
  if (method === "eth_getTransactionReceipt") return { status: "0x1" };
  throw new Error("unexpected " + method);
}, on: () => {} };
global.window = global; global.ethereum = provider;
global.addEventListener = () => {}; global.removeEventListener = () => {}; global.dispatchEvent = () => true;
global.CeedeWallet = W;
global.fetch = async (url, opt = {}) => {
  const full = url.replace(W.CONFIG.api, ""), path = full.split("?")[0], method = opt.method || "GET", auth = (opt.headers || {}).Authorization;
  const ok = (o) => ({ ok: true, status: 200, json: async () => o });
  if (path === "/businesses/ceedebooks/traction") return ok({ live: { paid: 2, held: 1, escalated: 0, volume_usdc: 1.5 },
    shadow: { paid: 3, held: 1, escalated: 1, awaiting_owner: 2, rejected: 0, mirrored_usdc: 1.5, real_totals: { NGN: "4500.50", KES: "99" }, agreement: { agree: 3, disagree: 1, n: 4, rate: 0.75 } },
    latest_paid: [{ invoice: "SH-1", mode: "shadow", tx_url: "https://explorer.testnet.arc.io/tx/0xabc" }, { invoice: "LV-1", mode: "live", tx_url: null }] });
  if (path === "/pause-status") return ok(pauseState);
  if (path === "/admin/auth/state") { stateUrls.push(full); return ok(state); }
  if (path === "/admin/auth/challenge") { challengeBodies.push(JSON.parse(opt.body)); return ok({ nonce: "n".repeat(32), message: "sign me" }); }
  if (path === "/admin/auth/verify") { assert.strictEqual(JSON.parse(opt.body).signature.length, 132); return ok({ token: "tok", address: ADMIN }); }
  if (path.startsWith("/decisions/")) return ok({ verified: true, onchain: { match: true, chain_tx_hash: "0x" + "ab".repeat(32) } });
  if (path.startsWith("/admin/") || path.startsWith("/operator/") || ["/vendors", "/receipts", "/invoices", "/purchase-orders", "/demo/seed"].includes(path)) {
    if (auth !== "Bearer tok" || expireSession) return { ok: false, status: 401, json: async () => ({ detail: "no" }) };
    if (path === "/admin/onboarding") return ok({ steps: [{ key: "pool_funded", label: "Fund your pool", done: false }, { key: "agent_gas", label: "Agent wallet gas", done: null }, { key: "vendor", label: "Add a vendor", done: true }], custody: "We run your agent's wallet." });
    if (path === "/operator/business-applications" && method === "GET") {
      if (!operator) return { ok: false, status: 403, json: async () => ({ detail: "no" }) };
      return ok({ applications: [{ id: 7, name: "Beta Ltd", slug: "beta", owner_wallet: "0x" + "ab".repeat(20), contact: "beta@example.com", status: "pending", created_at: 1760000000 }],
        businesses: [{ id: 1, name: "CeedeBooks", slug: "ceedebooks", status: "active", external: 0, enforcer_address: "0x" + "47".repeat(20), agent_address: "" }, { id: 2, name: "Acme", slug: "acme", status: "active", external: 0, enforcer_address: ACME_CONTRACT, agent_address: "0x" + "a1".repeat(20) }] });
    }
    if (path === "/operator/business-applications/7/accept" && method === "POST") { posts.push({ business_accepted: 7 }); return ok({ business: { agent_address: "0x" + "a1".repeat(20) } }); }
    if (path === "/operator/businesses/2/archive" && method === "POST") { posts.push({ archived: JSON.parse(opt.body).archived }); return ok({ id: 2 }); }
    if (path === "/operator/businesses/2/external" && method === "POST") { posts.push({ external: JSON.parse(opt.body).external }); return ok({ id: 2 }); }
    if (path === "/admin/overview") return ok({ operator: operator, business: { name: "CeedeBooks", slug: "ceedebooks" }, chain: { chain_ok: true, approver: ADMIN, paused: false, balance_usdc: 108.79, daily_limit_usdc: 100, per_tx_limit_usdc: 20, categories: [{ id: 1, name: "Infrastructure", daily_limit_usdc: 20, remaining_usdc: 20, usable: true }] }, invoices: { escalated: 3 }, vendors: 1, purchase_orders: 0 });
    if (path === "/admin/escrows" && method === "GET") return ok({ escrows: escrowsList });
    if (path === "/admin/escrows" && method === "POST") { posts.push({ escrow_registered: JSON.parse(opt.body) }); return ok({ id: 1 }); }
    if (/^\/admin\/escrows\/1\/milestones\/\d+\/decision$/.test(path)) { posts.push({ dry: path, body: JSON.parse(opt.body) }); return ok({ verdict: "release", reason: "The escrow allows it.", evidence_hash: "0x" + "ee".repeat(32),
      facts: { merged: dryMerged, repo: "acme/app", pr: 7, author: "dev" } }); }
    if (/^\/admin\/escrows\/1\/milestones\/\d+\/release$/.test(path)) { posts.push({ release: path, body: JSON.parse(opt.body) }); return ok({ verdict: "release", reason: "The escrow allows it.", reasoning_hash: "f1".repeat(32),
      sent: true, milestone_status_after: "released", evidence_hash: JSON.parse(opt.body).evidence_hash }); }
    if (path === "/admin/categories" && method === "GET") return ok({ chain_ok: true, categories: [
      { id: 0, name: "Hosting", daily_limit_usdc: 0, remaining_usdc: 0, usable: false },
      { id: 1, name: "Infrastructure", daily_limit_usdc: 20, remaining_usdc: 20, usable: true },
      { id: 4, name: "Software", daily_limit_usdc: null, remaining_usdc: null, usable: null }] });
    if (path === "/admin/categories" && method === "POST") { posts.push({ category_added: JSON.parse(opt.body) }); return ok({ id: 5, name: JSON.parse(opt.body).name }); }
    if (/^\/admin\/categories\/\d+$/.test(path) && method === "PATCH") { posts.push({ category_renamed: path, body: JSON.parse(opt.body) }); return ok({ ok: true }); }
    if (/^\/admin\/invoices\/\d+\/escalation$/.test(path)) { escalationUrls.push(full); return ok({ invoice_key: "0x" + "9a".repeat(32), can_reopen: true, submitted_by_you: false }); }
    if (/^\/admin\/invoices\/\d+\/rerun$/.test(path) && method === "POST") { posts.push({ rerun: path }); return ok({ ok: true }); }
    if (path === "/purchase-orders" && method === "POST") { posts.push({ po: JSON.parse(opt.body) }); return ok({ id: 9 }); }
    if (path === "/admin/vendors") return ok({ vendors: [{ id: 1, name: "Ops", wallet_address: "0x" + "d7".repeat(20), approved_onchain: false }] });
    if (path === "/admin/invoices") return ok({ invoices: [
      { id: 11, invoice_number: "SH-1", vendor_name: "Lagos Hosting", amount_usdc: 0.5, status: "awaiting_owner", mode: "shadow", real_amount: "1200.50", real_currency: "NGN", reasoning_hash: "a1".repeat(32), verdict: null },
      { id: 12, invoice_number: "SH-2", vendor_name: "Lagos Hosting", amount_usdc: 0.5, status: "held", mode: "shadow", real_amount: "99", real_currency: "KES", reasoning_hash: "b2".repeat(32), verdict: null },
      { id: 17, invoice_number: "DEMO-1-B", vendor_name: "Demo Supplies (demo)", amount_usdc: 0.01, status: "escalated", mode: "live", origin: "demo", real_amount: null, real_currency: null, reasoning_hash: "a7".repeat(32), verdict: null, submitted_by_you: false },
      { id: 14, invoice_number: "ESC-MINE", vendor_name: "Ops", amount_usdc: 3, status: "escalated", mode: "live", real_amount: null, real_currency: null, reasoning_hash: "d4".repeat(32), verdict: null, submitted_by_you: true },
      { id: 15, invoice_number: "ESC-KEY", vendor_name: "Ops", amount_usdc: 4, status: "escalated", mode: "live", real_amount: null, real_currency: null, reasoning_hash: "e5".repeat(32), verdict: null, submitted_by_you: false },
      { id: 16, invoice_number: "HELD-P", vendor_name: "Ops", amount_usdc: 1, status: "held", held_because: "paused", mode: "live", real_amount: null, real_currency: null, reasoning_hash: "f6".repeat(32), verdict: null },
      { id: 13, invoice_number: "LV-1", vendor_name: "Ops", amount_usdc: 2, status: "paid", mode: "live", real_amount: null, real_currency: null, reasoning_hash: "c3".repeat(32), verdict: null }] });
    if (path === "/admin/purchase-orders") return ok({ purchase_orders: [{ id: 5, po_number: "PO-5", vendor_id: 1, vendor_name: "Lagos Hosting", amount_usdc: 0.5, category: 1, received: 1 }] });
    if (path === "/demo/seed" && method === "POST") { posts.push({ demo: true }); return ok({ run: 1, label: "demo", invoices: [{ invoice_number: "DEMO-1-A", decision: "escalate" }, { invoice_number: "DEMO-1-B", decision: "escalate" }] }); }
    if (path === "/invoices" && method === "POST") { posts.push({ invoice: JSON.parse(opt.body) }); return ok({ invoice_id: 20, decision: "await_owner" }); }
    if (/^\/admin\/invoices\/\d+\/shadow-(approve|reject)$/.test(path) && method === "POST") { posts.push({ shadow: path }); return ok({ ok: true }); }
    if (/^\/admin\/decisions\/[0-9a-f]{64}\/verdict$/.test(path) && method === "POST") { posts.push({ verdict: path, body: JSON.parse(opt.body) }); return ok({ ok: true }); }
    if (path === "/admin/applications" && method === "GET") return ok({ applications: [{ id: 4, business_name: "Acme Data", wallet: "0x" + "ee".repeat(20), contact: "acme@example.com", status: "pending", created_at: 1 }] });
    if (path === "/admin/applications/4/accept" && method === "POST") { posts.push({ accepted: 4 }); return ok({ ok: true, vendor_id: 3 }); }
    if (path === "/admin/actions" && method === "POST") { posts.push(JSON.parse(opt.body)); return ok({ ok: true }); }
  }
  throw new Error("unexpected fetch " + method + " " + path);
};
const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));

(async () => {
  require("./admin.js");
  await tick(400);                                           // wallet discovery window
  assert.ok(/Connect wallet/.test(root.textContent) && !/Pool balance/.test(root.textContent), "before connect only the connect button shows");

  await findBtn(root, "Connect wallet").listeners.click(); await tick();
  assert.ok(/Admin wallet connected/.test(root.textContent) && findBtn(root, "Sign in"));
  await findBtn(root, "Sign in").listeners.click(); await tick();
  assert.ok(/Pool balance/.test(root.textContent) && /108.79 USDC/.test(root.textContent), "overview renders after sign-in");
  // the copy block: plain text for the Canteen CLI, never a blank line (an empty line ends its input)
  await findBtn(root, "Copy traction update").listeners.click(); await tick();
  let copied = ""; walk(root, (x) => { if (x.tag === "textarea" && x.value) copied = x.value; });
  assert.ok(copied.split("\n").every((l) => l.trim() !== ""), "no blank line in the traction text");
  assert.ok(/Shadow \(real bills/.test(copied) && /4500.50 NGN, 99 KES/.test(copied) && /75% \(3 agree, 1 disagree, 4 verdicts\)/.test(copied) && /Paid SH-1 \(shadow\): https:\/\/explorer/.test(copied), copied);
  assert.ok(/Paid LV-1: no transaction link yet/.test(copied));
  assert.ok(calls.some((c) => c.method === "personal_sign" && c.params[0] === W.utf8Hex("sign me") && c.params[1] === ADMIN));
  assert.ok(stateUrls[0] === (BIZ ? "/admin/auth/state?business=" + BIZ : "/admin/auth/state"), "state is read for the chosen business: " + stateUrls[0]);
  assert.deepStrictEqual(challengeBodies[0], BIZ ? { address: ADMIN, business: BIZ } : { address: ADMIN }, "the challenge names the business only when one was chosen");
  assert.ok(/Finish setting up/.test(root.textContent) && /Fund your pool/.test(root.textContent) && /Unknown/.test(root.textContent), "the setup checklist shows to-do and unknown steps");
  assert.ok(findBtn(root, "Businesses"), "the operator sees the Businesses tab");

  // vendors tab: approve on-chain
  let tabs = []; walk(root, (x) => { if (x.attrs && x.attrs["data-tab"] === "vendors") tabs.push(x); });
  await tabs[0].listeners.click(); await tick();
  assert.ok(/Not approved/.test(root.textContent));
  let labels = []; walk(root, (x) => { if (x.tag === "td" && x.attrs && x.attrs["data-label"]) labels.push(x.attrs["data-label"]); });
  assert.ok(["Name", "Wallet", "Status"].every((l) => labels.includes(l)), "table cells carry their column label for the phone card layout");
  assert.ok(!labels.includes(""), "the unlabelled action column gets no empty label");
  await findBtn(root, "Approve on-chain").listeners.click(); await tick(200);
  const tx = calls.find((c) => c.method === "eth_sendTransaction").params[0];
  assert.strictEqual(tx.to, ACME_CONTRACT, "a transaction goes to the contract of the business that was signed in to, not a fixed address");
  assert.strictEqual(tx.data, W.CALLS.setVendor("0x" + "d7".repeat(20), true));
  assert.strictEqual(tx.from, ADMIN);
  assert.deepStrictEqual(posts[0], { action: "set_vendor", tx_hash: "0x" + "cd".repeat(32) });

  // invoices tab: shadow mode. Approve and reject are server calls with a confirmation, never a wallet transaction.
  let invTab = []; walk(root, (x) => { if (x.attrs && x.attrs["data-tab"] === "invoices") invTab.push(x); });
  await invTab[0].listeners.click(); await tick();
  assert.ok(/1200.50 NGN/.test(root.textContent) && /waiting for you/.test(root.textContent) && /shadow/.test(root.textContent), "a shadow row shows the real bill and that it waits for the owner");
  // the demo: one button, a confirmation first, one server call, no wallet transaction; demo rows say so
  assert.ok(/Try the demo/.test(root.textContent) && /never counted as traction/.test(root.textContent), "the demo panel says it is never traction");
  const txBeforeDemo = calls.filter((c) => c.method === "eth_sendTransaction").length;
  await findBtn(root, "Load demo data").listeners.click(); await tick(60);
  assert.ok(posts.some((p) => p.demo), "the button calls /demo/seed");
  assert.strictEqual(calls.filter((c) => c.method === "eth_sendTransaction").length, txBeforeDemo, "loading the demo needs no wallet transaction");
  assert.ok(/Demo run 1 loaded: DEMO-1-A escalate, DEMO-1-B escalate/.test(document.body.textContent), "the person is told what was decided");
  posts.length = 0;
  const txBeforeShadow = calls.filter((c) => c.method === "eth_sendTransaction").length;
  await findBtn(root, "Approve and pay").listeners.click(); await tick(60);
  await findBtn(root, "Reject").listeners.click(); await tick(60);
  await findBtn(root, "I would do the same").listeners.click(); await tick(60);
  await findBtn(root, "I would not").listeners.click(); await tick(60);
  assert.deepStrictEqual(posts.filter((p) => p.shadow).map((p) => p.shadow), ["/admin/invoices/11/shadow-approve", "/admin/invoices/11/shadow-reject"]);
  assert.deepStrictEqual(posts.filter((p) => p.verdict).map((p) => p.body), [{ verdict: "agree" }, { verdict: "disagree" }]);
  assert.ok(posts.filter((p) => p.verdict).every((p) => p.verdict === "/admin/decisions/" + "b2".repeat(32) + "/verdict"), "the verdict goes to the held decision's hash");
  assert.strictEqual(calls.filter((c) => c.method === "eth_sendTransaction").length, txBeforeShadow, "shadow approval needs no wallet transaction");
  let inputs = {}; walk(root, (x) => { if (x.tag === "input" && x.attrs.placeholder) inputs[x.attrs.placeholder] = x; });
  let poSelect = null; walk(root, (x) => { if (x.tag === "select" && /PO-5/.test(x.textContent)) poSelect = x; });
  poSelect.value = "5"; poSelect.listeners.change();                       // choosing the order fills the amount in, visibly, and it stays editable
  assert.strictEqual(inputs["5.00"].value, "0.5", "the amount field is prefilled from the purchase order");
  inputs["INV-001"].value = "SH-9"; inputs["1200.50"].value = "5000"; inputs["NGN"].value = "ngn"; inputs["64-character SHA-256, or pick the file"].value = "ab".repeat(32);
  inputs["5.00"].value = "";                                                // nothing is sent with the amount left empty
  await findBtn(root, "Submit shadow invoice").listeners.click(); await tick(60);
  assert.ok(!posts.some((p) => p.invoice), "an empty amount sends nothing");
  assert.ok(/Enter the USDC amount/.test(document.body.textContent), "the person is told to enter it");
  inputs["5.00"].value = "abc";
  await findBtn(root, "Submit shadow invoice").listeners.click(); await tick(60);
  assert.ok(!posts.some((p) => p.invoice), "an amount that is not a number sends nothing");
  inputs["5.00"].value = "0.51";                                            // typed by the person, not the PO's 0.5
  await findBtn(root, "Submit shadow invoice").listeners.click(); await tick(60);
  assert.deepStrictEqual(posts.find((p) => p.invoice).invoice, { invoice_number: "SH-9", vendor_id: 1, amount_usdc: "0.51", category: 1, doc_hash: "ab".repeat(32),
    po_number: "PO-5", mode: "shadow", real_amount: "5000", real_currency: "NGN" }, "the form sends the amount that was entered, and the real bill with its currency capitalised");
  posts.length = 0;

  // purchase orders: only categories that are ready (on-chain limit above zero) can be chosen; the amount is always typed
  await findBtn(root, "Purchase orders").listeners.click(); await tick();
  let poSel = null; walk(root, (x) => { if (x.tag === "select" && /Infrastructure/.test(x.textContent)) poSel = x; });
  assert.ok(poSel && !/Hosting/.test(poSel.textContent) && !/Software/.test(poSel.textContent), "categories without a readable limit above zero are not offered");
  let poIn = {}; walk(root, (x) => { if (x.tag === "input" && x.attrs.placeholder) poIn[x.attrs.placeholder] = x; });
  assert.strictEqual(poIn["5.00"].value, "", "the PO amount field starts empty");
  poIn["5.00"].value = "";
  await findBtn(root, "Create PO").listeners.click(); await tick(60);
  assert.ok(!posts.some((p) => p.po), "no purchase order without an amount");
  walk(root, (x) => { if (x.tag === "select" && /Infrastructure/.test(x.textContent)) x.value = "1"; });
  poIn["5.00"].value = "12.5"; walk(root, (x) => { if (x.tag === "input" && !x.attrs.placeholder && x.attrs.maxlength === "64") x.value = "PO-77"; });
  walk(root, (x) => { if (x.tag === "select" && /Ops/.test(x.textContent)) x.value = "1"; });
  await findBtn(root, "Create PO").listeners.click(); await tick(60);
  assert.deepStrictEqual(posts.find((p) => p.po).po, { po_number: "PO-77", vendor_id: 1, amount_usdc: "12.5", category: 1 });
  posts.length = 0;


  // escrows tab: register, fund from the owner's wallet, dry run, then release only after a dry run with the same inputs
  await findBtn(root, "Escrows").listeners.click(); await tick();
  assert.ok(/Escrow 0xe5e5/.test(root.textContent) && /Agent release limit/.test(root.textContent) && /No: approver only/.test(root.textContent), "an escrow shows its limit and which milestones the agent may release");
  assert.ok(/not that the requirements are met/.test(root.textContent), "the page says what a merged pull request is not");
  let ein = {}; walk(root, (x) => { if (x.tag === "input" && x.attrs.placeholder) ein[x.attrs.placeholder] = x; });
  ein["0x..."].value = "nope";
  await findBtn(root, "Register").listeners.click(); await tick(60);
  assert.ok(!posts.some((p) => p.escrow_registered) && /Enter the escrow's 0x address/.test(document.body.textContent), "a bad address sends nothing");
  ein["0x..."].value = ESC;
  await findBtn(root, "Register").listeners.click(); await tick(60);
  assert.deepStrictEqual(posts.pop(), { escrow_registered: { address: ESC } });
  // funding is the owner's own wallet sending a USDC transfer to the escrow, after a confirmation
  ein = {}; walk(root, (x) => { if (x.tag === "input" && x.attrs.placeholder) ein[x.attrs.placeholder] = x; });
  const txBeforeEscrow = calls.filter((c) => c.method === "eth_sendTransaction").length;
  ein["0.5"].value = "0";
  await findBtn(root, "Add funds").listeners.click(); await tick(60);
  assert.strictEqual(calls.filter((c) => c.method === "eth_sendTransaction").length, txBeforeEscrow, "a zero amount sends nothing");
  ein["0.5"].value = "0.3";
  await findBtn(root, "Add funds").listeners.click(); await tick(200);
  const fundTx = calls.filter((c) => c.method === "eth_sendTransaction").pop().params[0];
  assert.strictEqual(fundTx.to, W.CONFIG.usdc); assert.strictEqual(fundTx.data, W.CALLS.usdcTransfer(ESC, 300000n)); assert.strictEqual(fundTx.from, ADMIN);
  // deciding: release is refused until a dry run with the same inputs has been shown
  ein = {}; walk(root, (x) => { if (x.tag === "input" && x.attrs.placeholder) ein[x.attrs.placeholder] = x; });
  const release = () => findBtn(root, "Release");
  await release().listeners.click(); await tick(60);
  assert.ok(!posts.some((p) => p.release) && /Enter the repository owner/.test(document.body.textContent), "no release without a target");
  ein["owner"].value = "acme"; ein["repository"].value = "app"; ein["pull request number"].value = "7";
  await release().listeners.click(); await tick(60);
  assert.ok(!posts.some((p) => p.release) && /Run the dry run first/.test(document.body.textContent), "no release before a dry run");
  await findBtn(root, "Dry run").listeners.click(); await tick(60);
  assert.deepStrictEqual(posts.pop(), { dry: "/admin/escrows/1/milestones/0/decision", body: { owner: "acme", repo: "app", pr_number: 7 } });
  assert.ok(/Verdict: release/.test(root.textContent) && /Evidence hash: 0xeeee/.test(root.textContent) && /Nothing was recorded and nothing was sent/.test(root.textContent), "the dry run shows the verdict and the hash and says it wrote nothing");
  dryMerged = false;
  await findBtn(root, "Dry run").listeners.click(); await tick(60);
  assert.ok(/The pull request is not merged/.test(root.textContent), "an unmerged pull request is shown as not merged");
  dryMerged = true;
  await findBtn(root, "Dry run").listeners.click(); await tick(60);
  assert.ok(/The pull request is merged/.test(root.textContent));
  posts.length = 0;
  ein["pull request number"].value = "8";                                    // a different pull request than the one that was checked
  await release().listeners.click(); await tick(60);
  assert.ok(!posts.some((p) => p.release) && /Run the dry run first/.test(document.body.textContent), "a changed target needs a new dry run");
  ein["pull request number"].value = "7";
  const txBeforeRelease = calls.filter((c) => c.method === "eth_sendTransaction").length;
  await release().listeners.click(); await tick(200);
  assert.deepStrictEqual(posts.pop(), { release: "/admin/escrows/1/milestones/0/release", body: { evidence_hash: "0x" + "ee".repeat(32), owner: "acme", repo: "app", pr_number: 7 } }, "the release carries the evidence hash the admin was shown");
  assert.strictEqual(calls.filter((c) => c.method === "eth_sendTransaction").length, txBeforeRelease, "the release is the agent's: the admin's wallet sends nothing");
  assert.ok(/Sent: the milestone now reads released/.test(root.textContent) && /Decision hash: f1f1/.test(root.textContent) && /hash matches its stored record/.test(root.textContent) && /on-chain event found/.test(root.textContent), "the result and the record check are shown");
  await release().listeners.click(); await tick(60);
  assert.ok(!posts.some((p) => p.release), "one dry run allows one release attempt");
  posts.length = 0;

  // limits tab: add a category (a server call), rename it, then set its limit with the owner's own wallet using its id
  await findBtn(root, "Limits and safety").listeners.click(); await tick();
  assert.ok(/Needs a limit/.test(root.textContent) && /Ready/.test(root.textContent) && /Unknown/.test(root.textContent), "each category shows its on-chain state, and an unreadable one is shown as unknown");
  assert.ok(/no private data/.test(root.textContent), "the name hint warns against private data");
  const txBeforeCats = calls.filter((c) => c.method === "eth_sendTransaction").length;
  let lim = {}; walk(root, (x) => { if (x.tag === "input" && x.attrs.placeholder) lim[x.attrs.placeholder] = x; });
  lim["Hosting"].value = "Marketing";
  await findBtn(root, "Add category").listeners.click(); await tick(60);
  assert.deepStrictEqual(posts.pop(), { category_added: { name: "Marketing" } });
  lim = {}; walk(root, (x) => { if (x.tag === "input" && x.attrs.placeholder) lim[x.attrs.placeholder] = x; });   // the tab was redrawn
  let selects = []; walk(root, (x) => { if (x.tag === "select") selects.push(x); });
  selects[0].value = "0";                                                  // the first select is the rename picker
  lim["New name"].value = "Hosting and domains";
  await findBtn(root, "Rename").listeners.click(); await tick(60);
  assert.deepStrictEqual(posts.pop(), { category_renamed: "/admin/categories/0", body: { name: "Hosting and domains" } });
  assert.strictEqual(calls.filter((c) => c.method === "eth_sendTransaction").length, txBeforeCats, "adding or renaming a category sends no transaction");
  lim = {}; walk(root, (x) => { if (x.tag === "input" && x.attrs.placeholder) lim[x.attrs.placeholder] = x; });
  let limSel = null; walk(root, (x) => { if (x.tag === "select" && /Software/.test(x.textContent)) limSel = x; });
  limSel.value = "4"; lim["20.00"].value = "15";
  await findBtn(root, "Set limit").listeners.click(); await tick(200);
  const limTx = calls.filter((c) => c.method === "eth_sendTransaction").pop().params[0];
  assert.strictEqual(limTx.data, W.CALLS.setCategoryDailyLimit(4, 15000000n), "the transaction carries the chosen category's id");
  assert.strictEqual(limTx.to, ACME_CONTRACT);
  assert.strictEqual(limTx.from, ADMIN, "the limit is signed by the owner's wallet");
  posts.length = 0;

  // no form sends a hidden amount: the sources have no default and no fixed 0.01
  const src = require("fs").readFileSync(__dirname + "/admin.js", "utf8");
  assert.ok(!/amount_usdc:\s*String\(po\./.test(src), "no invoice body takes the amount straight from the purchase order");
  assert.ok(!/(amount_usdc|\.value)\s*[:=]\s*["']?0\.01\b/.test(src), "no fixed 0.01 amount is put into a field or a request");

  // applications tab: review, accept (no wallet transaction: it only creates the vendor record)
  let apptab = []; walk(root, (x) => { if (x.attrs && x.attrs["data-tab"] === "applications") apptab.push(x); });
  await apptab[0].listeners.click(); await tick();
  assert.ok(/Acme Data/.test(root.textContent) && /acme@example.com/.test(root.textContent));
  const txBefore = calls.filter((c) => c.method === "eth_sendTransaction").length;
  await findBtn(root, "Accept").listeners.click(); await tick(100);
  assert.deepStrictEqual(posts[posts.length - 1], { accepted: 4 });
  assert.strictEqual(calls.filter((c) => c.method === "eth_sendTransaction").length, txBefore, "accepting an application sends no transaction");

  // businesses tab (operator only): accept sends no wallet transaction; the outside mark needs a confirmation
  await findBtn(root, "Businesses").listeners.click(); await tick();
  assert.ok(/Beta Ltd/.test(root.textContent) && /beta@example.com/.test(root.textContent) && /Your own/.test(root.textContent));
  const txMid = calls.filter((c) => c.method === "eth_sendTransaction").length;
  await findBtn(root, "Accept").listeners.click(); await tick(100);
  assert.deepStrictEqual(posts[posts.length - 1], { business_accepted: 7 });
  await findBtn(root, "Mark as outside").listeners.click(); await tick(100);
  assert.deepStrictEqual(posts[posts.length - 1], { external: true });
  assert.strictEqual(calls.filter((c) => c.method === "eth_sendTransaction").length, txMid, "accepting or marking a business sends no transaction");
  // archiving hides a business and makes it read-only: it needs a confirmation, offers the button only for another business, and sends no transaction
  let archiveButtons = 0; walk(root, (x) => { if (x.tag === "button" && x.textContent === "Archive") archiveButtons++; });
  assert.strictEqual(archiveButtons, 1, "one Archive button (Acme), none for the operator's own business");
  await findBtn(root, "Archive").listeners.click(); await tick(100);
  assert.deepStrictEqual(posts[posts.length - 1], { archived: true });
  assert.strictEqual(calls.filter((c) => c.method === "eth_sendTransaction").length, txMid, "archiving sends no transaction");

  // funding: a USDC token transfer from the owner's wallet to the business's contract, never a plain send
  await findBtn(root, "Overview").listeners.click(); await tick();
  assert.ok(byId("fund-amount") && findBtn(root, "Add funds") && /normal Send does not work/.test(root.textContent));
  const fundTxBefore = calls.filter((c) => c.method === "eth_sendTransaction").length;
  byId("fund-amount").value = "abc";
  await findBtn(root, "Add funds").listeners.click(); await tick(60);
  assert.ok(/Enter an amount/.test(document.body.textContent), "a bad amount is refused");
  byId("fund-amount").value = "0";
  await findBtn(root, "Add funds").listeners.click(); await tick(60);
  assert.strictEqual(calls.filter((c) => c.method === "eth_sendTransaction").length, fundTxBefore, "nothing is sent for a bad or zero amount");
  byId("fund-amount").value = "12.5";
  await findBtn(root, "Add funds").listeners.click(); await tick(2800);
  const fund = calls.filter((c) => c.method === "eth_sendTransaction").pop().params[0];
  assert.strictEqual(fund.to, W.CONFIG.usdc, "the transaction goes to the USDC token, not to the business contract");
  assert.strictEqual(fund.from, ADMIN);
  assert.strictEqual(fund.data, W.CALLS.usdcTransfer(ACME_CONTRACT, 12500000n), "it transfers to the contract of the business signed in to");
  assert.strictEqual(fund.value, undefined, "no native value is attached");

  // maker-checker: an invoice this wallet submitted has a disabled Approve, a chip, and never asks the server for a release key
  await invTab[0].listeners.click(); await tick();
  let approves = []; walk(root, (x) => { if (x.tag === "button" && x.textContent === "Approve and pay") approves.push(x); });
  const mine = approves.filter((b) => b.attrs.disabled === "disabled");
  assert.strictEqual(mine.length, 1, "exactly one Approve is disabled: the invoice you submitted");
  assert.ok(/you submitted this/.test(root.textContent) && /cannot release it/.test(mine[0].attrs.title));
  escalationUrls.length = 0; calls.length = 0;
  const keyApprove = approves.filter((b) => b.attrs.disabled !== "disabled").pop();       // ESC-KEY (the shadow row's button comes first)
  await keyApprove.listeners.click(); await tick(200);
  assert.deepStrictEqual(escalationUrls, ["/admin/invoices/15/escalation?purpose=approve"], "the release key is asked for with purpose=approve for the other invoice only");
  assert.ok(/held: payments paused/.test(root.textContent), "a pause hold is labelled with its cause");
  posts.length = 0;
  await findBtn(root, "Re-run").listeners.click(); await tick(60);
  assert.deepStrictEqual(posts.filter((p) => p.rerun).map((p) => p.rerun), ["/admin/invoices/16/rerun"], "a paused hold can be returned to the agent");

  // pause needs a reason first, sends it with the state, and the banner follows the public pause status
  await findBtn(root, "Limits and safety").listeners.click(); await tick();
  const txBeforePause = calls.filter((c) => c.method === "eth_sendTransaction").length;
  posts.length = 0;
  await findBtn(root, "Pause all payments").listeners.click(); await tick(100);
  assert.strictEqual(calls.filter((c) => c.method === "eth_sendTransaction").length, txBeforePause, "no transaction without a reason");
  assert.ok(/reason of at least 3/.test(document.body.textContent));
  walk(root, (x) => { if (x.tag === "input" && /^Why/.test(x.attrs.placeholder || "")) x.value = "vendor wallet under review"; });
  await findBtn(root, "Pause all payments").listeners.click(); await tick(200);
  const pauseTx = calls.filter((c) => c.method === "eth_sendTransaction").pop().params[0];
  assert.strictEqual(pauseTx.data, W.CALLS.setPaused(true));
  assert.deepStrictEqual(posts.pop(), { action: "set_paused", tx_hash: "0x" + "cd".repeat(32), paused: true, reason: "vendor wallet under review" });
  assert.ok(/The reason is public/.test(root.textContent));
  pauseState = { paused: true, reason: "vendor wallet under review", by: ADMIN };
  await findBtn(root, "Overview").listeners.click(); await tick();
  assert.ok(/Payments are PAUSED: vendor wallet under review/.test(byId("pause-banner").textContent), "the banner shows the reason");
  pauseState = { paused: null, reason: null, by: null };
  await findBtn(root, "Vendors").listeners.click(); await tick();
  assert.ok(/could not be read/.test(byId("pause-banner").textContent), "an unreadable state is said, not hidden");
  pauseState = { paused: false, reason: null, by: null };
  await findBtn(root, "Overview").listeners.click(); await tick();
  assert.strictEqual(byId("pause-banner").textContent, "", "no banner while payments are active");

  // a session the server ends sends the admin back to the gate
  expireSession = true;
  await tabs[0].listeners.click(); await tick();
  assert.ok(/session ended/i.test(root.textContent) && /Connect wallet/.test(root.textContent) && !/Pool balance/.test(root.textContent));
  // a wallet that is not the admin sees nothing but a refusal
  account = OTHER;
  await findBtn(root, "Connect wallet").listeners.click(); await tick();
  assert.ok(/not the admin wallet/.test(root.textContent) && !/Pool balance/.test(root.textContent));
  await findBtn(root, "Use another wallet").listeners.click(); await tick();

  // the proposed new approver gets the one-button handover
  state = { approver: ADMIN, pending_approver: OTHER };   // no contract in the state: fall back to the built-in one
  calls.length = 0;
  await findBtn(root, "Connect wallet").listeners.click(); await tick();
  assert.ok(/proposed as the new admin/.test(root.textContent));
  await findBtn(root, "Accept admin role").listeners.click(); await tick(200);
  const accept = calls.find((c) => c.method === "eth_sendTransaction").params[0];
  assert.strictEqual(accept.data, W.CALLS.acceptApprover());
  assert.strictEqual(accept.to, W.CONFIG.enforcer, "without a contract in the state the built-in one is used");
  assert.ok(findBtn(root, "Sign in"));

  // a business admin that is not the operator never sees the Businesses tab, and the server would refuse it anyway
  operator = false; expireSession = false;
  await findBtn(root, "Sign in").listeners.click(); await tick();
  assert.ok(findBtn(root, "Overview") && findBtn(root, "Vendors") && !findBtn(root, "Businesses"), "no Businesses tab for a business admin");
  console.log("ADMIN_SMOKE_OK");
})().catch((e) => { console.error(e); process.exit(1); });
