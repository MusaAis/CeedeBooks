/* CeedeBooks vendor portal. Apply with a wallet signature, sign in with one, then see only your own purchase orders and
   invoices. Nothing here sends a transaction: a signature costs nothing. Data is written with textContent only (no
   innerHTML). The session token lives in this variable, never in storage. The server enforces every rule. */
(function () {
  "use strict";
  var L = window.CeedePortalLib;
  var C = L.CONFIG;
  var root = document.getElementById("root");
  var S = { providers: [], provider: null, account: null, token: null, me: null, bound: false, po: null };

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
  function when(ts) { return new Date(Number(ts) * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC"; }
  function usdc(n) { return Number(n).toLocaleString("en-US", { maximumFractionDigits: 6 }) + " USDC"; }
  function errText(e) { return e && e.code === 4001 ? "You cancelled in your wallet." : (e && e.message) || "Something went wrong."; }
  function chip(text, kind) { return h("span", { class: "chip " + kind }, [text]); }
  function field(label, input) { return h("label", null, [label, input]); }

  var statusEl = null;
  function say(text, kind) {
    if (statusEl) { if (statusEl.remove) statusEl.remove(); statusEl = null; }
    if (!text) return;
    statusEl = h("div", { class: "status" + (kind ? " " + kind : ""), role: "status" }, [text]);
    document.body.appendChild(statusEl);
    if (kind === "ok") setTimeout(function () { if (statusEl && statusEl.textContent === text) say(null); }, 6000);
  }
  function guard(fn) {
    return function () { return Promise.resolve().then(fn).catch(function (e) { if (e && e.message !== "session ended") say(errText(e), "err"); }); };
  }

  async function api(path, opts) {
    opts = opts || {};
    var headers = {};
    if (opts.body) headers["Content-Type"] = "application/json";
    if (S.token) headers["Authorization"] = "Bearer " + S.token;
    var res = await fetch(C.api + path, { method: opts.method || "GET", headers: headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
    var data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (res.status === 401 && S.token) { signedOut("Your session ended. Sign in again."); throw new Error("session ended"); }
    if (!res.ok) throw new Error(data && typeof data.detail === "string" ? data.detail : "Request failed (" + res.status + ")");
    return data;
  }

  // ---------- wallet (signatures only)
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
  async function signChallenge(base) {
    var ch = await api(base + "/challenge", { method: "POST", body: { address: S.account } });
    var sig = await S.provider.request({ method: "personal_sign", params: [L.utf8Hex(ch.message), S.account] });
    return { nonce: ch.nonce, signature: sig };
  }

  // ---------- shared chrome
  function brand() {
    return h("a", { class: "brand", href: C.proof, "aria-label": "CeedeBooks" }, [
      h("img", { src: "logo.png", alt: "", width: "30", height: "30" }), h("span", null, ["CeedeBooks"]), h("span", { class: "tag" }, ["Vendors"])]);
  }
  function netPill() { return h("span", { class: "pill net" }, [h("i", { "aria-hidden": "true" }), "Arc Testnet"]); }
  function footer() {
    return h("footer", { class: "foot" }, [h("div", { class: "wrap foot-in" }, [
      h("span", null, ["Testnet USDC has no market value."]),
      h("span", { class: "links" }, [
        h("a", { href: C.proof, target: "_blank", rel: "noopener noreferrer" }, ["Proof page"]),
        h("a", { href: "https://github.com/MusaAis/CeedeBooks", target: "_blank", rel: "noopener noreferrer" }, ["Source"]),
        h("a", { href: C.api + "/.well-known/agent.json", target: "_blank", rel: "noopener noreferrer" }, ["Agent manifest"])])])]);
  }
  function topbar(end) {
    return h("header", { class: "top" }, [h("div", { class: "wrap top-in" }, [brand(), h("div", { class: "top-end" }, [netPill()].concat(end || []))])]);
  }
  function mountPage(nodes) {
    root.textContent = "";
    nodes.forEach(function (n) { root.appendChild(n); });
    root.appendChild(footer());
  }

  // ---------- gate: the hero explains, the receipt acts
  function mountGate(title, step, bodyKids) {
    var copy = h("div", { class: "hc" }, [
      h("span", { class: "eyebrow" }, [h("i", { "aria-hidden": "true" }), "For vendors paid by CeedeBooks"]),
      h("h1", null, ["Get paid by an agent that shows its work."]),
      h("p", { class: "lead" }, ["Prove you control your payee wallet, see what you are owed, and check an invoice before you send it. Every decision is written down before any money moves."])]);
    var points = h("ul", { class: "points" }, [
      h("li", null, ["You sign a message. No transaction, no fee, no password."]),
      h("li", null, ["You see only your own orders and invoices."]),
      h("li", null, ["Anyone can check a decision against the blockchain."])]);
    var receipt = h("div", { class: "receipt" }, [
      h("div", { class: "rc-head" }, [h("h2", null, [title]), h("span", { class: "rc-run" }, [step])]),
      h("div", { class: "rc-body" }, bodyKids)]);
    mountPage([topbar(), h("main", { id: "top" }, [h("section", { class: "hero" }, [h("div", { class: "wrap hero-in" }, [copy, receipt, points])])])]);
  }
  function gateIdle(msg, isErr) {
    var kids = [];
    if (msg) kids.push(h("p", { class: isErr ? "err" : "small" }, [msg]));
    kids.push(h("p", null, ["Connect the wallet you want to be paid to. Your wallet asks before sharing anything."]));
    if (!S.providers.length) {
      kids.push(h("p", { class: "small" }, ["No wallet found. Open this page in the MetaMask or Rabby app's browser, or in a browser with the MetaMask or Rabby extension."]));
    } else {
      S.providers.forEach(function (p) {
        kids.push(h("button", { class: "btn primary block", onclick: guardGate(function () { return connect(p); }) }, [S.providers.length > 1 ? "Connect " + p.info.name : "Connect wallet"]));
      });
    }
    mountGate("Vendor access", "Step 1 of 2", kids);
  }
  function guardGate(fn) { return function () { return Promise.resolve().then(fn).catch(function (e) { gateIdle(errText(e), true); }); }; }
  async function connect(p) {
    var accounts = await p.provider.request({ method: "eth_requestAccounts" });
    S.provider = p.provider;
    S.account = String(accounts[0]).toLowerCase();
    if (S.provider.on && !S.bound) { S.bound = true; S.provider.on("accountsChanged", function () { signedOut("Wallet changed. Connect again."); }); }
    gateChoose();
  }
  function gateChoose(msg, isErr) {
    var kids = [];
    if (msg) kids.push(h("p", { class: isErr ? "err" : "small" }, [msg]));
    kids.push(h("p", null, ["Wallet connected."]), h("p", { class: "addr" }, [S.account]),
      h("div", { class: "stack" }, [
        h("button", { class: "btn primary block", onclick: guardChoose(signIn) }, ["Sign in as a vendor"]),
        h("button", { class: "btn dark block", onclick: function () { gateApply(); } }, ["Apply to become a vendor"])]),
      h("p", { class: "small" }, ["Signing a message sends no transaction and costs nothing."]));
    mountGate("Vendor access", "Step 2 of 2", kids);
  }
  function guardChoose(fn) { return function () { return Promise.resolve().then(fn).catch(function (e) { if (e && e.message !== "session ended") gateChoose(errText(e), true); }); }; }
  async function signIn() {
    var s = await signChallenge("/vendor/auth");
    var out = await api("/vendor/auth/verify", { method: "POST", body: s });
    S.token = out.token;
    say(null);
    await renderApp();
  }
  function gateApply(msg, isErr) {
    var name = h("input", { id: "app-name", maxlength: "120", autocomplete: "organization", placeholder: "Acme Data Ltd" });
    var contact = h("input", { id: "app-contact", maxlength: "120", autocomplete: "email", placeholder: "email or handle (optional)" });
    var kids = [];
    if (msg) kids.push(h("p", { class: isErr ? "err" : "small" }, [msg]));
    kids.push(h("p", null, ["Applying with:"]), h("p", { class: "addr" }, [S.account]),
      field("Business name", name), field("Contact (only the admin sees it)", contact),
      h("div", { class: "stack" }, [
        h("button", { class: "btn primary block", onclick: guardApply(async function () {
          var business = name.value.trim();
          if (business.length < 2) throw new Error("Enter your business name.");
          var s = await signChallenge("/apply");
          var body = { business_name: business, wallet_address: S.account, nonce: s.nonce, signature: s.signature };
          if (contact.value.trim().length >= 3) body.contact = contact.value.trim();
          await api("/apply", { method: "POST", body: body });
          mountGate("Application received", "Under review", [
            h("p", { class: "okbox" }, ["Application received."]),
            h("p", null, ["The CeedeBooks admin reviews it, then approves your wallet on-chain. Come back and sign in with this wallet."]),
            h("button", { class: "btn dark block", onclick: function () { gateChoose(); } }, ["Back"])]);
        }) }, ["Sign and apply"]),
        h("button", { class: "btn dark block", onclick: function () { gateChoose(); } }, ["Back"])]));
    mountGate("Apply as a vendor", "Free to sign", kids);
  }
  function guardApply(fn) { return function () { return Promise.resolve().then(fn).catch(function (e) { gateApply(errText(e), true); }); }; }
  function signedOut(msg) {
    S.token = null; S.me = null; S.po = null;
    say(null);
    gateIdle(msg, !!msg);
  }

  // ---------- dashboard
  async function renderApp() {
    S.me = await api("/vendor/me");
    var me = S.me;
    var open = me.purchase_orders.filter(function (p) { return !p.invoiced; });
    var waiting = open.filter(function (p) { return !p.received; });
    var paid = me.invoices.filter(function (i) { return i.status === "paid"; });
    var approval = me.approved_onchain === true ? chip("Approved to be paid", "ok")
      : me.approved_onchain === false ? chip("Waiting for on-chain approval", "warn") : chip("Approval unknown right now", "bad");
    var stat = function (label, n) { return h("div", { class: "stat" }, [h("dt", null, [label]), h("dd", null, [String(n)])]); };

    var hero = h("section", { class: "hero compact" }, [h("div", { class: "wrap hero-in" }, [
      h("span", { class: "eyebrow" }, [h("i", { "aria-hidden": "true" }), "Signed in"]),
      h("h1", null, [me.vendor.name]),
      h("div", { class: "row" }, [approval, h("span", { class: "pill mono" }, [L.short(me.vendor.wallet_address)])]),
      me.approved_onchain === false ? h("p", { class: "lead" }, ["The contract refuses payments to a wallet the admin has not approved. You can still check an invoice below."]) : null,
      h("dl", { class: "stats" }, [stat("Open orders", open.length), stat("Awaiting receipt", waiting.length), stat("Invoices paid", paid.length)])])]);

    var orders = h("section", { class: "sec wrap" }, [h("h2", { class: "h2" }, ["Your purchase orders"])]);
    if (!me.purchase_orders.length) orders.appendChild(h("p", { class: "empty" }, ["No purchase orders yet. CeedeBooks raises one before it pays you."]));
    var cards = h("div", { class: "cards" });
    me.purchase_orders.forEach(function (po) {
      var state = po.invoiced ? chip("Invoiced", "ok") : po.received ? chip("Receipt confirmed", "ok") : chip("Waiting for receipt", "warn");
      var act = po.invoiced ? null : h("button", { class: "btn primary sm", onclick: guard(function () { S.po = po; return renderApp().then(function () { var f = document.getElementById("inv-form"); if (f && f.scrollIntoView) f.scrollIntoView(); }); }) }, ["Invoice this"]);
      cards.appendChild(h("div", { class: "po " + (po.invoiced ? "done" : po.received ? "ready" : "waiting") }, [
        h("div", { class: "t" }, [h("strong", null, [po.po_number]), h("span", { class: "amt" }, [usdc(po.amount_usdc)])]),
        h("div", { class: "m" }, [po.category_name]),
        h("div", { class: "foot-row" }, [state, act])]));
    });
    if (me.purchase_orders.length) orders.appendChild(cards);

    var parts = [topbar([h("button", { class: "btn ghost sm", onclick: guard(logout) }, ["Sign out"])]), h("main", { id: "view" }, [hero, orders])];
    var main = parts[1];
    if (S.po) main.appendChild(h("section", { class: "form-sec wrap" }, [invoiceForm(S.po)]));

    var invoices = h("section", { class: "sec wrap" }, [h("h2", { class: "h2" }, ["Your invoices"])]);
    if (!me.invoices.length) invoices.appendChild(h("p", { class: "empty" }, ["No invoices yet."]));
    var feed = h("div", { class: "feed" });
    var kind = { paid: "ok", held: "warn", escalated: "warn", rejected: "bad", error: "bad", pending: "warn" };
    me.invoices.forEach(function (i) {
      var link = i.reasoning_hash ? h("a", { class: "mini", href: C.proof + "/#check=" + i.reasoning_hash, target: "_blank", rel: "noopener noreferrer" }, ["Check on the proof page"]) : null;
      feed.appendChild(h("div", { class: "item" }, [chip(i.status, kind[i.status] || "warn"),
        h("div", { class: "t" }, [h("strong", null, [i.invoice_number]), h("span", null, [usdc(i.amount_usdc) + " \u00B7 " + when(i.created_at)])]), link]));
    });
    if (me.invoices.length) invoices.appendChild(feed);
    main.appendChild(invoices);
    mountPage(parts);
  }
  async function logout() {
    try { await api("/vendor/auth/logout", { method: "POST" }); } catch (e) { /* already gone */ }
    signedOut("Signed out.");
  }

  function invoiceForm(po) {
    var num = h("input", { id: "inv-number", maxlength: "64", autocomplete: "off", placeholder: "INV-001" });
    var docHash = h("input", { id: "inv-hash", autocomplete: "off", spellcheck: "false", placeholder: "64-character SHA-256, or pick the file" });
    var file = h("input", { id: "inv-file", type: "file" });
    var out = h("div", { id: "inv-out" });
    file.addEventListener("change", guard(async function () {
      var f = file.files && file.files[0];
      if (!f) return;
      docHash.value = await L.sha256Hex(await f.arrayBuffer());
      say("Hashed in your browser. The file itself is not uploaded.", "ok");
    }));
    function body() {
      return { invoice_number: L.cleanInvoiceNumber(num.value), vendor_id: S.me.vendor.id, amount_usdc: String(po.amount_usdc),
               category: po.category, doc_hash: L.cleanDocHash(docHash.value), po_number: po.po_number };
    }
    function show(node) { out.textContent = ""; out.appendChild(node); }
    var submit = h("button", { id: "inv-submit", class: "btn primary sm", onclick: guard(async function () {
      var b = body();
      var r = await api("/invoices", { method: "POST", body: b });
      var inv = await api("/invoices/" + r.invoice_id);
      var kinds = { paid: "ok", held: "warn", escalated: "warn", rejected: "bad" };
      var node = h("div", { class: "verdict " + (kinds[inv.status] || "warn") }, ["Decision: " + r.decision + " (status: " + inv.status + ")"]);
      if (inv.reasoning_hash) node.appendChild(h("p", { class: "hint" }, [h("a", { href: C.proof + "/#check=" + inv.reasoning_hash, target: "_blank", rel: "noopener noreferrer" }, ["Check this decision on the proof page"])]));
      show(node);
      S.po = null;
    }) }, ["Submit invoice"]);
    if (!po.received) submit.setAttribute("disabled", "disabled");
    var check = h("button", { id: "inv-check", class: "btn dark sm", onclick: guard(async function () {
      var r = await api("/invoices/preflight", { method: "POST", body: body() });
      var d = L.describeOutcome(r.outcome);
      var node = h("div", { class: "verdict " + d[0] }, [d[1] + " (dry run, nothing was saved)"]);
      if (r.reasons && r.reasons.length) node.appendChild(h("ul", null, r.reasons.map(function (x) { return h("li", null, [x]); })));
      show(node);
    }) }, ["Check first (dry run)"]);
    return h("div", { class: "receipt", id: "inv-form" }, [
      h("div", { class: "rc-head" }, [h("h2", null, ["Invoice for " + po.po_number]), h("span", { class: "rc-run" }, [usdc(po.amount_usdc)])]),
      h("div", { class: "rc-body" }, [
        h("p", { class: "small" }, [po.category_name + " \u00B7 amount and category are set by the purchase order"]),
        field("Invoice number", num), field("Document hash", docHash), field("Or pick the file to hash", file),
        h("div", { class: "stack" }, [check, submit]),
        po.received ? null : h("p", { class: "hint" }, ["You can submit once CeedeBooks confirms it received your delivery."]),
        out])]);
  }

  discover().then(function (found) { S.providers = found; gateIdle(); });
})();
