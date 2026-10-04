// Run: node admin/admin.test.js   (fake DOM, mocked wallet and API: checks the sign-in and approval flows end to end)
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
let account = ADMIN, state = { approver: ADMIN, pending_approver: "0x" + "00".repeat(20) }, calls = [], posts = [], expireSession = false;
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
  const path = url.replace(W.CONFIG.api, ""), method = opt.method || "GET", auth = (opt.headers || {}).Authorization;
  const ok = (o) => ({ ok: true, status: 200, json: async () => o });
  if (path === "/admin/auth/state") return ok(state);
  if (path === "/admin/auth/challenge") return ok({ nonce: "n".repeat(32), message: "sign me" });
  if (path === "/admin/auth/verify") { assert.strictEqual(JSON.parse(opt.body).signature.length, 132); return ok({ token: "tok", address: ADMIN }); }
  if (path.startsWith("/admin/") || ["/vendors", "/receipts"].includes(path)) {
    if (auth !== "Bearer tok" || expireSession) return { ok: false, status: 401, json: async () => ({ detail: "no" }) };
    if (path === "/admin/overview") return ok({ chain: { chain_ok: true, approver: ADMIN, paused: false, balance_usdc: 108.79, daily_limit_usdc: 100, per_tx_limit_usdc: 20, categories: [{ id: 1, name: "Infrastructure", daily_limit_usdc: 20, remaining_usdc: 20 }] }, invoices: { escalated: 3 }, vendors: 1, purchase_orders: 0 });
    if (path === "/admin/vendors") return ok({ vendors: [{ id: 1, name: "Ops", wallet_address: "0x" + "d7".repeat(20), approved_onchain: false }] });
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
  assert.ok(calls.some((c) => c.method === "personal_sign" && c.params[0] === W.utf8Hex("sign me") && c.params[1] === ADMIN));

  // vendors tab: approve on-chain
  let tabs = []; walk(root, (x) => { if (x.attrs && x.attrs["data-tab"] === "vendors") tabs.push(x); });
  await tabs[0].listeners.click(); await tick();
  assert.ok(/Not approved/.test(root.textContent));
  let labels = []; walk(root, (x) => { if (x.tag === "td" && x.attrs && x.attrs["data-label"]) labels.push(x.attrs["data-label"]); });
  assert.ok(["Name", "Wallet", "Status"].every((l) => labels.includes(l)), "table cells carry their column label for the phone card layout");
  assert.ok(!labels.includes(""), "the unlabelled action column gets no empty label");
  await findBtn(root, "Approve on-chain").listeners.click(); await tick(200);
  const tx = calls.find((c) => c.method === "eth_sendTransaction").params[0];
  assert.strictEqual(tx.to, W.CONFIG.enforcer);
  assert.strictEqual(tx.data, W.CALLS.setVendor("0x" + "d7".repeat(20), true));
  assert.strictEqual(tx.from, ADMIN);
  assert.deepStrictEqual(posts[0], { action: "set_vendor", tx_hash: "0x" + "cd".repeat(32) });

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
  state = { approver: ADMIN, pending_approver: OTHER };
  calls.length = 0;
  await findBtn(root, "Connect wallet").listeners.click(); await tick();
  assert.ok(/proposed as the new admin/.test(root.textContent));
  await findBtn(root, "Accept admin role").listeners.click(); await tick(200);
  assert.strictEqual(calls.find((c) => c.method === "eth_sendTransaction").params[0].data, W.CALLS.acceptApprover());
  assert.ok(findBtn(root, "Sign in"));
  console.log("ADMIN_SMOKE_OK");
})().catch((e) => { console.error(e); process.exit(1); });
