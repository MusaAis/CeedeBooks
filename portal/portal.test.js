// Run: node portal/portal.test.js   (fake DOM, mocked wallet and API: apply, sign in, dry run and submit end to end)
const assert = require("assert");
const L = require("./lib.js");

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
const byId = (id) => { let hit = null; walk(root, (x) => { if (!hit && x.attrs && x.attrs.id === id) hit = x; }); return hit; };
const root = mk("div");
global.document = { getElementById: (id) => (id === "root" ? root : byId(id)), createElement: mk, createTextNode: (t) => ({ textContent: String(t), children: [] }), body: mk("body") };

// ---- business registration helpers (v1.2.8.2)
{
  const fs = require("fs"), path = require("path");
  const artifact = path.join(__dirname, "..", "out", "BudgetFactory.sol", "BudgetFactory.json");
  assert.ok(fs.existsSync(artifact), "run forge build first: the selector is checked against the compiled factory");
  const ids = JSON.parse(fs.readFileSync(artifact, "utf8")).methodIdentifiers;
  assert.strictEqual(L.SEL_CREATE_BUSINESS, ids["createBusiness(address,uint256,uint256,uint256)"], "createBusiness selector matches the compiled factory");
  assert.strictEqual(L.CONFIG.chainIdHex, "0x" + (5042002).toString(16));
  const A = "0x" + "ab".repeat(20), w = (h) => h.padStart(64, "0");
  assert.strictEqual(L.createBusinessData(A, 100000000n, 20000000n, 500000000n),
    "0x" + L.SEL_CREATE_BUSINESS + w("ab".repeat(20)) + w((100000000).toString(16)) + w((20000000).toString(16)) + w((500000000).toString(16)));
  assert.throws(() => L.createBusinessData("0x123", 1n, 1n, 1n));
  assert.throws(() => L.createBusinessData(A, -1n, 1n, 1n));
  assert.deepStrictEqual(L.validateLimits("100", "20", "500"), { daily: 100000000n, perTx: 20000000n, weekly: 500000000n });
  assert.deepStrictEqual(L.validateLimits("20", "20", "20"), { daily: 20000000n, perTx: 20000000n, weekly: 20000000n }); // equal is allowed, as in the contract
  for (const [d, p, wk] of [["10", "20", "500"], ["100", "20", "50"], ["100", "0", "500"], ["abc", "1", "2"], ["1", "1.1234567", "2"], ["", "1", "2"]]) assert.throws(() => L.validateLimits(d, p, wk), d + "/" + p + "/" + wk);
  assert.strictEqual(L.cleanSlug("  Acme Traders Ltd! "), "acme-traders-ltd");
  assert.strictEqual(L.cleanSlug("my_shop--2"), "my-shop-2");
  for (const bad of ["", "a", "!!", "x".repeat(65)]) assert.throws(() => L.cleanSlug(bad), bad);
}

const ME = "0x" + "aa".repeat(20);
const calls = [], posts = [];
let expire = false, approved = true, received = true;
const provider = { request: async ({ method, params }) => {
  calls.push({ method, params });
  if (method === "eth_requestAccounts") return [ME];
  if (method === "personal_sign") return "0x" + "11".repeat(65);
  throw new Error("unexpected wallet call " + method);   // a vendor never sends a transaction
}, on: () => {} };
global.window = global; global.ethereum = provider;
global.addEventListener = () => {}; global.removeEventListener = () => {}; global.dispatchEvent = () => true;
global.CeedePortalLib = L;
const ok = (o) => ({ ok: true, status: 200, json: async () => o });
const PO = { id: 1, po_number: "PO-1", amount_usdc: 5, category: 2, category_name: "Research", received: 1, invoiced: 0 };
global.fetch = async (url, opt = {}) => {
  const path = url.replace(L.CONFIG.api, ""), method = opt.method || "GET", auth = (opt.headers || {}).Authorization;
  if (path.endsWith("/challenge")) return ok({ nonce: "n".repeat(32), message: "sign me" });
  if (path === "/apply") { posts.push({ path, body: JSON.parse(opt.body) }); return ok({ id: 1, status: "pending" }); }
  if (path === "/vendor/auth/verify") { assert.strictEqual(JSON.parse(opt.body).signature.length, 132); return ok({ token: "tok" }); }
  if (auth !== "Bearer tok" || expire) return { ok: false, status: 401, json: async () => ({ detail: "no" }) };
  if (path === "/vendor/me") return ok({ vendor: { id: 7, name: "Acme", wallet_address: ME }, approved_onchain: approved,
    purchase_orders: [{ ...PO, received: received ? 1 : 0 }], invoices: [{ id: 3, invoice_number: "OLD-1", amount_usdc: 2, status: "paid", reasoning_hash: "ab".repeat(32), created_at: 1 }] });
  if (path === "/invoices/preflight") { posts.push({ path, body: JSON.parse(opt.body) }); return ok({ outcome: "hold", reasons: ["No receipt on file"], dry_run: true }); }
  if (path === "/invoices" && method === "POST") { posts.push({ path, body: JSON.parse(opt.body) }); return ok({ invoice_id: 9, decision: "pay" }); }
  if (path === "/invoices/9") return ok({ id: 9, status: "paid", reasoning_hash: "cd".repeat(32) });
  throw new Error("unexpected fetch " + method + " " + path);
};
const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));

(async () => {
  // helpers
  assert.strictEqual(L.cleanDocHash("0x" + "AB".repeat(32)), "ab".repeat(32));
  assert.throws(() => L.cleanDocHash("not a hash")); assert.throws(() => L.cleanInvoiceNumber("  "));
  assert.strictEqual(await L.sha256Hex(new TextEncoder().encode("abc")), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");

  require("./portal.js");
  await tick(400);
  assert.ok(/Connect wallet/.test(root.textContent) && !/purchase orders/i.test(root.textContent), "before connect only the connect button shows");
  await findBtn(root, "Connect wallet").listeners.click(); await tick();
  assert.ok(/Wallet connected/.test(root.textContent));

  // apply: one signature, one POST, no transaction
  await findBtn(root, "Apply to become a vendor").listeners.click(); await tick();
  byId("app-name").value = "Acme Data"; byId("app-contact").value = "acme@example.com";
  await findBtn(root, "Sign and apply").listeners.click(); await tick();
  assert.deepStrictEqual(posts[0], { path: "/apply", body: { business_name: "Acme Data", wallet_address: ME, nonce: "n".repeat(32), signature: "0x" + "11".repeat(65), contact: "acme@example.com" } });
  assert.ok(/Application received/.test(root.textContent));
  await findBtn(root, "Back").listeners.click(); await tick();

  // sign in: own data appears
  await findBtn(root, "Sign in as a vendor").listeners.click(); await tick();
  assert.ok(/Acme/.test(root.textContent) && /PO-1/.test(root.textContent) && /Approved to be paid/.test(root.textContent) && /OLD-1/.test(root.textContent));
  assert.ok(calls.every((c) => c.method !== "eth_sendTransaction"), "a vendor never sends a transaction");

  // invoice: PO values are fixed by the server's record, dry run first, then submit
  await findBtn(root, "Invoice this").listeners.click(); await tick();
  byId("inv-number").value = "INV-9"; byId("inv-hash").value = "AB".repeat(32);
  await findBtn(root, "Check first (dry run)").listeners.click(); await tick();
  assert.deepStrictEqual(posts[1], { path: "/invoices/preflight", body: { invoice_number: "INV-9", vendor_id: 7, amount_usdc: "5", category: 2, doc_hash: "ab".repeat(32), po_number: "PO-1" } });
  assert.ok(/Would be held/.test(root.textContent) && /No receipt on file/.test(root.textContent) && /nothing was saved/.test(root.textContent));
  await findBtn(root, "Submit invoice").listeners.click(); await tick();
  assert.strictEqual(posts[2].path, "/invoices"); assert.ok(!("origin" in posts[2].body), "the portal never labels its own origin");
  assert.ok(/Decision: pay/.test(root.textContent) && /Check this decision on the proof page/.test(root.textContent));
  let links = []; walk(root, (x) => { if (x.tag === "a" && x.attrs.href) links.push(x.attrs.href); });
  assert.ok(links.includes(L.CONFIG.proof + "/#check=" + "cd".repeat(32)));

  // a bad hash is refused in the browser, before any request
  const before = posts.length;
  await findBtn(root, "Invoice this").listeners.click(); await tick();
  byId("inv-number").value = "INV-10"; byId("inv-hash").value = "not a hash";
  await findBtn(root, "Check first (dry run)").listeners.click(); await tick();
  assert.strictEqual(posts.length, before, "no request is sent for a malformed document hash");

  // submit is disabled until the receipt is confirmed
  received = false; approved = false;
  await findBtn(root, "Sign out").listeners.click(); await tick();
  await findBtn(root, "Connect wallet").listeners.click(); await tick();
  await findBtn(root, "Sign in as a vendor").listeners.click(); await tick();
  assert.ok(/Waiting for on-chain approval/.test(root.textContent));
  await findBtn(root, "Invoice this").listeners.click(); await tick();
  assert.strictEqual(findBtn(root, "Submit invoice").attrs.disabled, "disabled");

  // a session the server ends sends the vendor back to the gate
  expire = true;
  await findBtn(root, "Sign out").listeners.click(); await tick();
  assert.ok(/Connect wallet/.test(root.textContent) && !/PO-1/.test(root.textContent));
  console.log("PORTAL_SMOKE_OK");
})().catch((e) => { console.error(e); process.exit(1); });
