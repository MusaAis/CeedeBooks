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
