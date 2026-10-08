// Run: node portal/business.test.js   (fake DOM, mocked wallet and API: apply, wait, create the contract, register)
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
const hasLink = (text) => { let hit = null; walk(root, (x) => { if (!hit && x.tag === "a" && x.textContent === text) hit = x; }); return hit; };
const root = mk("div");
global.document = { getElementById: (id) => (id === "root" ? root : byId(id)), createElement: mk, createTextNode: (t) => ({ textContent: String(t), children: [] }), body: mk("body") };

const OWNER = "0x" + "aa".repeat(20), AGENT = "0x" + "a1".repeat(20), CONTRACT = "0x" + "c3".repeat(20), TX = "0x" + "e5".repeat(32);
const wallet = [], posts = [];
let apps = [], registerFails = 0;
const provider = { request: async ({ method, params }) => {
  wallet.push({ method, params });
  if (method === "eth_requestAccounts") return [OWNER];
  if (method === "personal_sign") return "0x" + "11".repeat(65);
  if (method === "eth_chainId") return "0x4cef52";
  if (method === "eth_sendTransaction") return TX;
  if (method === "eth_getTransactionReceipt") return { status: "0x1" };
  throw new Error("unexpected wallet call " + method);
}, on: () => {} };
global.window = global; global.ethereum = provider;
global.addEventListener = () => {}; global.removeEventListener = () => {}; global.dispatchEvent = () => true;
global.CeedePortalLib = L;
global.fetch = async (url, opt = {}) => {
  const path = url.replace(L.CONFIG.api, ""), body = opt.body ? JSON.parse(opt.body) : null;
  const ok = (o) => ({ ok: true, status: 200, json: async () => o });
  const no = (status, detail) => ({ ok: false, status, json: async () => ({ detail }) });
  if (path.endsWith("/challenge")) { assert.strictEqual(body.address, OWNER); return ok({ nonce: "n".repeat(32), message: "sign me" }); }
  if (path === "/business/status") return ok({ wallet: OWNER, applications: apps, factory: L.CONFIG.factory, custody: "x" });
  if (path === "/business/apply") { posts.push({ apply: body }); apps = [{ id: 5, name: body.name, slug: body.slug, status: "pending" }]; return ok({ id: 5, status: "pending" }); }
  if (path === "/business/register") {
    posts.push({ register: body });
    if (registerFails-- > 0) return no(503, "Could not read the chain right now; try again in a minute");
    return ok({ slug: "acme-traders", name: "Acme Traders", status: "active", contract: CONTRACT, agent_address: AGENT, admin_url: "https://admin.ceedebooks.xyz/?business=acme-traders" });
  }
  throw new Error("unexpected fetch " + path);
};
const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));

(async () => {
  require("./business.js");
  await tick(400);
  assert.ok(/Connect wallet/.test(root.textContent) && !/Sign and apply/.test(root.textContent), "nothing but the connect button before a wallet is connected");

  // connect: a signed status read, then the apply form with the custody line shown BEFORE applying
  await findBtn(root, "Connect wallet").listeners.click(); await tick(50);
  assert.ok(findBtn(root, "Sign and apply") && /We run your agent's wallet/.test(root.textContent) && /you can revoke it any time/.test(root.textContent), "the custody line is shown at application");
  byId("biz-name").value = "Acme Traders"; byId("biz-name").listeners.input();
  assert.strictEqual(byId("biz-slug").value, "acme-traders", "the web address name is suggested from the business name");
  await findBtn(root, "Sign and apply").listeners.click(); await tick(80);
  assert.strictEqual(posts[0].apply.slug, "acme-traders");
  assert.strictEqual(posts[0].apply.wallet_address, OWNER);
  assert.strictEqual(posts[0].apply.signature.length, 132);
  assert.ok(/waiting for review/.test(root.textContent), "after applying the owner sees that it is under review");
  assert.ok(!wallet.some((c) => c.method === "eth_sendTransaction"), "applying sends no transaction");

  // accepted: the agent wallet and the limits form
  apps = [{ id: 5, name: "Acme Traders", slug: "acme-traders", status: "accepted", agent_address: AGENT, contract: null }];
  await findBtn(root, "Check again (sign to confirm)").listeners.click(); await tick(80);
  assert.ok(/Create your contract/.test(root.textContent) && root.textContent.includes(AGENT) && findBtn(root, "Create my contract"));

  // limits the contract would revert on are refused before any fee is spent
  byId("lim-daily").value = "100"; byId("lim-week").value = "500"; byId("lim-tx").value = "200";   // (a real browser reads the default values; the fake DOM does not)
  await findBtn(root, "Create my contract").listeners.click(); await tick(50);
  assert.ok(/cannot be more than the daily limit/.test(root.textContent));
  assert.ok(!wallet.some((c) => c.method === "eth_sendTransaction"), "a bad limit sends nothing");

  // the real call: one transaction to the factory with the issued agent and the chosen limits; the server then verifies it
  byId("lim-tx").value = "25"; byId("lim-daily").value = "150"; byId("lim-week").value = "600";
  registerFails = 1;                                    // the server's node lags once: the page retries
  await findBtn(root, "Create my contract").listeners.click(); await tick(3200);
  const tx = wallet.find((c) => c.method === "eth_sendTransaction").params[0];
  assert.strictEqual(tx.to, L.CONFIG.factory);
  assert.strictEqual(tx.from, OWNER);
  assert.strictEqual(tx.data, L.createBusinessData(AGENT, 150000000n, 25000000n, 600000000n));
  assert.deepStrictEqual(posts.filter((p) => p.register).map((p) => p.register), [{ application_id: 5, tx_hash: TX }, { application_id: 5, tx_hash: TX }]);
  assert.ok(/Your business is live/.test(root.textContent) && root.textContent.includes(CONTRACT) && root.textContent.includes(AGENT));
  assert.ok(hasLink("Open my admin page").attrs.href === "https://admin.ceedebooks.xyz/?business=acme-traders");
  assert.ok(hasLink("Vendor link").attrs.href === "https://portal.ceedebooks.xyz/?business=acme-traders");
  assert.ok(/use Add funds to put USDC into your contract/.test(root.textContent) && /agent wallet so it can pay network fees/.test(root.textContent));
  console.log("BUSINESS_SMOKE_OK");
})().catch((e) => { console.error(e); process.exit(1); });
