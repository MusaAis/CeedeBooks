/* CeedeBooks: register a business. Apply with a wallet signature, wait for the operator to accept, then create your own
   contract with ONE transaction from your own wallet; the server records the business only after the chain proves it.
   Data is written with textContent only (no innerHTML). Nothing is kept in browser storage. */
(function () {
  "use strict";
  var L = window.CeedePortalLib;
  var C = L.CONFIG;
  var root = document.getElementById("root");
  var S = { providers: [], provider: null, account: null, bound: false };
  var CUSTODY = "We run your agent's wallet. It can only pay your approved vendors within your limits, and you can revoke it any time. You keep the owner wallet that controls the money and the limits.";

  function h(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (k === "class") n.className = v;
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    });
    (kids || []).forEach(function (c) { if (c != null) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function errText(e) { return e && e.code === 4001 ? "You cancelled in your wallet." : (e && e.message) || "Something went wrong."; }
  function field(label, input) { return h("label", null, [label, input]); }
  function link(href, text) { return h("a", { href: href, target: "_blank", rel: "noopener noreferrer" }, [text]); }

  async function api(path, opts) {
    opts = opts || {};
    var res = await fetch(C.api + path, { method: opts.method || "GET", headers: opts.body ? { "Content-Type": "application/json" } : {}, body: opts.body ? JSON.stringify(opts.body) : undefined });
    var data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) { var err = new Error(data && typeof data.detail === "string" ? data.detail : "Request failed (" + res.status + ")"); err.status = res.status; throw err; }
    return data;
  }
  function discover() {
    return new Promise(function (resolve) {
      var found = [];
      function onAnnounce(e) { if (e.detail && e.detail.provider) found.push(e.detail); }
      window.addEventListener("eip6963:announceProvider", onAnnounce);
      window.dispatchEvent(new Event("eip6963:requestProvider"));
      setTimeout(function () {
        window.removeEventListener("eip6963:announceProvider", onAnnounce);
        if (!found.length && window.ethereum) found.push({ info: { name: "Browser wallet" }, provider: window.ethereum });
        resolve(found);
      }, 300);
    });
  }
  async function sign(base) {
    var ch = await api(base + "/challenge", { method: "POST", body: { address: S.account } });
    var sig = await S.provider.request({ method: "personal_sign", params: [L.utf8Hex(ch.message), S.account] });
    return { nonce: ch.nonce, signature: sig };
  }
  async function ensureChain() {
    var id = await S.provider.request({ method: "eth_chainId" });
    if (String(id).toLowerCase() === C.chainIdHex) return;
    try { await S.provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: C.chainIdHex }] }); }
    catch (e) {
      if (e && e.code === 4902) await S.provider.request({ method: "wallet_addEthereumChain", params: [{ chainId: C.chainIdHex, chainName: C.chainName, nativeCurrency: C.nativeCurrency, rpcUrls: [C.rpc], blockExplorerUrls: [C.explorer] }] });
      else throw e;
    }
  }
  async function waitReceipt(hash) {
    for (var i = 0; i < 60; i++) {
      var rc = await S.provider.request({ method: "eth_getTransactionReceipt", params: [hash] });
      if (rc) return rc;
      await sleep(2000);
    }
    throw new Error("Still pending after two minutes. Check the explorer, then come back to this page: it will pick up where you left off.");
  }

  // ---------- layout
  function page(title, step, kids) {
    var copy = h("div", { class: "hc" }, [
      h("span", { class: "eyebrow" }, [h("i", { "aria-hidden": "true" }), "For businesses that pay vendors"]),
      h("h1", null, ["Your money in your own contract. An agent that shows its work."]),
      h("p", { class: "lead" }, ["You create a contract that holds your USDC and enforces your limits. The agent can only pay vendors you approve, and every decision is written down before money moves."])]);
    var points = h("ul", { class: "points" }, [
      h("li", null, ["You sign messages and one transaction, from your own wallet."]),
      h("li", null, ["Your funds are in your contract. We cannot withdraw them."]),
      h("li", null, ["Anyone can check a decision against the blockchain."]),
      h("li", null, ["Shadow mode: give it a real bill and the agent decides on it. A testnet payment mirrors the bill only after you approve, so no real money moves. Shadow records are labelled shadow, and the real amount and currency are shown publicly."])]);
    var receipt = h("div", { class: "receipt" }, [
      h("div", { class: "rc-head" }, [h("h2", null, [title]), h("span", { class: "rc-run" }, [step])]),
      h("div", { class: "rc-body" }, kids)]);
    root.textContent = "";
    root.appendChild(h("header", { class: "top" }, [h("div", { class: "wrap top-in" }, [
      h("a", { class: "brand", href: C.proof }, [h("img", { src: "logo.png", alt: "", width: "30", height: "30" }), h("span", null, ["CeedeBooks"]), h("span", { class: "tag" }, ["Businesses"])]),
      h("div", { class: "top-end" }, [h("span", { class: "pill net" }, [h("i", { "aria-hidden": "true" }), "Arc Testnet"])])])]));
    root.appendChild(h("main", { id: "top" }, [h("section", { class: "hero" }, [h("div", { class: "wrap hero-in" }, [copy, receipt, points])])]));
    root.appendChild(h("footer", { class: "foot" }, [h("div", { class: "wrap foot-in" }, [
      h("span", null, ["Testnet USDC has no market value."]),
      h("span", { class: "links" }, [link(C.proof, "Proof page"), link("https://github.com/MusaAis/CeedeBooks", "Source"), link(C.api + "/businesses", "Businesses")])])]));
  }
  function guarded(fn, back) {
    return function () { return Promise.resolve().then(fn).catch(function (e) { back(errText(e)); }); };
  }
  function note(msg) { return msg ? h("p", { class: "err" }, [msg]) : null; }

  // ---------- steps
  function connectScreen(msg) {
    var kids = [note(msg), h("p", null, ["Connect the wallet that will own the business. It controls the money, the limits and the vendor list."])];
    if (!S.providers.length) kids.push(h("p", { class: "small" }, ["No wallet found. Open this page in the MetaMask or Rabby app's browser, or in a browser with the MetaMask or Rabby extension."]));
    S.providers.forEach(function (p) {
      kids.push(h("button", { class: "btn primary block", onclick: guarded(function () { return connect(p); }, connectScreen) }, [S.providers.length > 1 ? "Connect " + p.info.name : "Connect wallet"]));
    });
    page("Register your business", "Step 1", kids);
  }
  async function connect(p) {
    var accounts = await p.provider.request({ method: "eth_requestAccounts" });
    S.provider = p.provider;
    S.account = String(accounts[0]).toLowerCase();
    if (S.provider.on && !S.bound) { S.bound = true; S.provider.on("accountsChanged", function () { S.account = null; connectScreen("Wallet changed. Connect again."); }); }
    await loadStatus();
  }
  async function loadStatus(msg) {
    var s = await sign("/business/status");
    var st = await api("/business/status", { method: "POST", body: s });
    S.status = st;
    var live = st.applications.filter(function (a) { return a.status !== "rejected"; })[0] || st.applications[0];
    if (!live) return applyScreen(msg);
    if (live.status === "pending" || live.status === "accepting") return waitScreen(live);
    if (live.status === "accepted") return createScreen(live, msg);
    if (live.status === "registered") return doneScreen(live);
    return rejectedScreen(live);
  }
  function applyScreen(msg) {
    var name = h("input", { id: "biz-name", maxlength: "80", autocomplete: "organization", placeholder: "Acme Traders" });
    var slug = h("input", { id: "biz-slug", maxlength: "64", autocomplete: "off", spellcheck: "false", placeholder: "acme-traders" });
    var contact = h("input", { id: "biz-contact", maxlength: "120", autocomplete: "email", placeholder: "email or handle (optional)" });
    name.addEventListener("input", function () { if (!slug.dataset || !slug.dataset.touched) { try { slug.value = L.cleanSlug(name.value); } catch (e) { slug.value = ""; } } });
    slug.addEventListener("input", function () { slug.dataset = slug.dataset || {}; slug.dataset.touched = "1"; });
    page("Apply", "Free to sign", [
      note(msg), h("p", null, ["Applying as the owner with:"]), h("p", { class: "addr" }, [S.account]),
      h("p", { class: "okbox" }, [CUSTODY]),
      field("Business name", name), field("Name in the web address (letters, digits, hyphens)", slug), field("Contact (only the operator sees it)", contact),
      h("div", { class: "stack" }, [h("button", { class: "btn primary block", onclick: guarded(async function () {
        var n = name.value.trim();
        if (n.length < 2) throw new Error("Enter your business name.");
        var body = { name: n, slug: L.cleanSlug(slug.value || n), wallet_address: S.account };
        var s = await sign("/business/apply");
        body.nonce = s.nonce; body.signature = s.signature;
        if (contact.value.trim().length >= 3) body.contact = contact.value.trim();
        await api("/business/apply", { method: "POST", body: body });
        await loadStatus();
      }, applyScreen) }, ["Sign and apply"])]),
      h("p", { class: "small" }, ["Signing a message sends no transaction and costs nothing."])]);
  }
  function waitScreen(a) {
    page("Application received", "Under review", [
      h("p", { class: "okbox" }, [a.name + " (" + a.slug + ") is waiting for review."]),
      h("p", null, ["Once it is accepted, come back to this page with the same wallet. You will create your contract here."]),
      h("button", { class: "btn dark block", onclick: guarded(function () { return loadStatus(); }, waitScreen.bind(null, a)) }, ["Check again (sign to confirm)"])]);
  }
  function rejectedScreen(a) {
    page("Not accepted", "Closed", [h("p", { class: "err" }, ["The application for " + a.name + " was not accepted."]), h("p", null, ["You can apply again with a different name."]),
      h("button", { class: "btn dark block", onclick: function () { applyScreen(); } }, ["Apply again"])]);
  }
  function createScreen(a, msg) {
    var daily = h("input", { id: "lim-daily", value: "100", inputmode: "decimal", autocomplete: "off" });
    var perTx = h("input", { id: "lim-tx", value: "20", inputmode: "decimal", autocomplete: "off" });
    var weekly = h("input", { id: "lim-week", value: "500", inputmode: "decimal", autocomplete: "off" });
    page("Create your contract", "One transaction", [
      note(msg), h("p", { class: "okbox" }, ["Accepted. Your agent wallet is ready:"]), h("p", { class: "addr" }, [a.agent_address]),
      h("p", { class: "small" }, [CUSTODY]),
      h("p", null, ["Choose your limits in USDC. The contract enforces them, and only your wallet can change them."]),
      field("Most the agent can pay in one day", daily), field("Most in a single payment", perTx), field("Most in a week", weekly),
      h("div", { class: "stack" }, [h("button", { class: "btn primary block", onclick: guarded(async function () {
        var lim = L.validateLimits(daily.value, perTx.value, weekly.value);
        await ensureChain();
        var hash = await S.provider.request({ method: "eth_sendTransaction", params: [{ from: S.account, to: C.factory, data: L.createBusinessData(a.agent_address, lim.daily, lim.perTx, lim.weekly) }] });
        pendingScreen(a, hash);
        var rc = await waitReceipt(hash);
        if (rc.status !== "0x1") throw new Error("The transaction failed on-chain. Nothing was created.");
        await register(a, hash);
      }, function (m) { createScreen(a, m); }) }, ["Create my contract"])]),
      h("p", { class: "small" }, ["Your wallet asks you to confirm. You need a little USDC for the network fee (faucet.circle.com)."])]);
  }
  function pendingScreen(a, hash) {
    page("Creating your contract", "Waiting for the chain", [h("p", null, ["Transaction sent. Waiting for it to confirm..."]), h("p", { class: "addr" }, [hash]), link(C.explorer + "/tx/" + hash, "View on the explorer")]);
  }
  // The server's node can lag the wallet's, so retry briefly before giving up.
  async function register(a, hash) {
    var last;
    for (var i = 0; i < 8; i++) {
      try { var out = await api("/business/register", { method: "POST", body: { application_id: a.id, tx_hash: hash } }); return doneScreen(Object.assign({}, a, out)); }
      catch (e) { last = e; if (e.status !== 503 && e.status !== 422) throw e; await sleep(2500); }
    }
    throw last;
  }
  function doneScreen(a) {
    var contract = a.contract || (S.status && (S.status.applications.filter(function (x) { return x.id === a.id; })[0] || {}).contract);
    var admin = C.admin + "/?business=" + encodeURIComponent(a.slug);
    var vendors = C.portal + "/?business=" + encodeURIComponent(a.slug);
    page("Your business is live", "Done", [
      h("p", { class: "okbox" }, [a.name + " is registered."]),
      h("p", null, ["Contract:"]), h("p", { class: "addr" }, [contract || ""]), contract ? link(C.explorer + "/address/" + contract, "View on the explorer") : null,
      h("p", null, ["Agent wallet:"]), h("p", { class: "addr" }, [a.agent_address || ""]),
      h("p", null, ["Next, in this order:"]),
      h("ol", null, [h("li", null, ["Open your admin page and use Add funds to put USDC into your contract. Do not use your wallet's normal Send for this: it fails."]), h("li", null, ["Send about 0.5 USDC to the agent wallet so it can pay network fees."]),
        h("li", null, ["Open your admin page, add your vendors and approve them on-chain."]), h("li", null, ["Share the vendor link so they can apply."])]),
      h("div", { class: "stack" }, [h("a", { class: "btn primary block", href: admin }, ["Open my admin page"]), h("a", { class: "btn dark block", href: vendors }, ["Vendor link"])]),
      h("p", { class: "small" }, [CUSTODY])]);
  }

  discover().then(function (found) { S.providers = found; connectScreen(); });
})();
