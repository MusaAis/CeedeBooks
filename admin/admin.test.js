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
let operator = true, stateUrls = [], challengeBodies = [];
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
  if (path === "/admin/auth/state") { stateUrls.push(full); return ok(state); }
  if (path === "/admin/auth/challenge") { challengeBodies.push(JSON.parse(opt.body)); return ok({ nonce: "n".repeat(32), message: "sign me" }); }
  if (path === "/admin/auth/verify") { assert.strictEqual(JSON.parse(opt.body).signature.length, 132); return ok({ token: "tok", address: ADMIN }); }
  if (path.startsWith("/admin/") || path.startsWith("/operator/") || ["/vendors", "/receipts", "/invoices"].includes(path)) {
    if (auth !== "Bearer tok" || expireSession) return { ok: false, status: 401, json: async () => ({ detail: "no" }) };
    if (path === "/admin/onboarding") return ok({ steps: [{ key: "pool_funded", label: "Fund your pool", done: false }, { key: "agent_gas", label: "Agent wallet gas", done: null }, { key: "vendor", label: "Add a vendor", done: true }], custody: "We run your agent's wallet." });
    if (path === "/operator/business-applications" && method === "GET") {
      if (!operator) return { ok: false, status: 403, json: async () => ({ detail: "no" }) };
      return ok({ applications: [{ id: 7, name: "Beta Ltd", slug: "beta", owner_wallet: "0x" + "ab".repeat(20), contact: "beta@example.com", status: "pending", created_at: 1760000000 }],
        businesses: [{ id: 1, name: "CeedeBooks", slug: "ceedebooks", status: "active", external: 0, enforcer_address: "0x" + "47".repeat(20), agent_address: "" }, { id: 2, name: "Acme", slug: "acme", status: "active", external: 0, enforcer_address: ACME_CONTRACT, agent_address: "0x" + "a1".repeat(20) }] });
    }
    if (path === "/operator/business-applications/7/accept" && method === "POST") { posts.push({ business_accepted: 7 }); return ok({ business: { agent_address: "0x" + "a1".repeat(20) } }); }
    if (path === "/operator/businesses/2/external" && method === "POST") { posts.push({ external: JSON.parse(opt.body).external }); return ok({ id: 2 }); }
    if (path === "/admin/overview") return ok({ operator: operator, business: { name: "CeedeBooks", slug: "ceedebooks" }, chain: { chain_ok: true, approver: ADMIN, paused: false, balance_usdc: 108.79, daily_limit_usdc: 100, per_tx_limit_usdc: 20, categories: [{ id: 1, name: "Infrastructure", daily_limit_usdc: 20, remaining_usdc: 20 }] }, invoices: { escalated: 3 }, vendors: 1, purchase_orders: 0 });
    if (path === "/admin/vendors") return ok({ vendors: [{ id: 1, name: "Ops", wallet_address: "0x" + "d7".repeat(20), approved_onchain: false }] });
    if (path === "/admin/invoices") return ok({ invoices: [
      { id: 11, invoice_number: "SH-1", vendor_name: "Lagos Hosting", amount_usdc: 0.5, status: "awaiting_owner", mode: "shadow", real_amount: "1200.50", real_currency: "NGN", reasoning_hash: "a1".repeat(32), verdict: null },
      { id: 12, invoice_number: "SH-2", vendor_name: "Lagos Hosting", amount_usdc: 0.5, status: "held", mode: "shadow", real_amount: "99", real_currency: "KES", reasoning_hash: "b2".repeat(32), verdict: null },
      { id: 13, invoice_number: "LV-1", vendor_name: "Ops", amount_usdc: 2, status: "paid", mode: "live", real_amount: null, real_currency: null, reasoning_hash: "c3".repeat(32), verdict: null }] });
    if (path === "/admin/purchase-orders") return ok({ purchase_orders: [{ id: 5, po_number: "PO-5", vendor_id: 1, vendor_name: "Lagos Hosting", amount_usdc: 0.5, category: 1, received: 1 }] });
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
  walk(root, (x) => { if (x.tag === "select" && /PO-5/.test(x.textContent)) x.value = "5"; });   // a real select starts on its first option
  inputs["INV-001"].value = "SH-9"; inputs["1200.50"].value = "5000"; inputs["NGN"].value = "ngn"; inputs["64-character SHA-256, or pick the file"].value = "ab".repeat(32);
  await findBtn(root, "Submit shadow invoice").listeners.click(); await tick(60);
  assert.deepStrictEqual(posts.find((p) => p.invoice).invoice, { invoice_number: "SH-9", vendor_id: 1, amount_usdc: "0.5", category: 1, doc_hash: "ab".repeat(32),
    po_number: "PO-5", mode: "shadow", real_amount: "5000", real_currency: "NGN" }, "the form sends the PO's mirrored amount and the real bill, currency capitalised");
  posts.length = 0;

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
