/* CeedeBooks admin site. Everything here is inert until the approver wallet signs in: the server refuses every /admin
   request without a session, so hiding the interface is a courtesy and the checks on the server are the protection.
   Data is written with textContent only (no innerHTML). The session token lives in this variable, never in storage. */
(function () {
  "use strict";
  var W = window.CeedeWallet;
  var C = W.CONFIG;
  var root = document.getElementById("root");
  var S = { providers: [], provider: null, account: null, token: null, overview: null, tab: "overview", bound: false };
  var TABS = [["overview", "Overview"], ["vendors", "Vendors"], ["pos", "Purchase orders"], ["invoices", "Invoices"], ["limits", "Limits and safety"], ["activity", "Activity"]];

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
  function when(ts) { return new Date(Number(ts) * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC"; }
  function usdc(n) { return Number(n).toLocaleString("en-US", { maximumFractionDigits: 6 }) + " USDC"; }
  function errText(e) { return e && e.code === 4001 ? "You cancelled in your wallet." : (e && e.message) || "Something went wrong."; }
  function txLink(hash) {
    var a = h("a", { href: C.explorer + "/tx/" + hash, target: "_blank", rel: "noopener noreferrer" }, [W.short(hash)]);
    return a;
  }

  // ---------- status bar and confirmation dialog
  var statusEl = null;
  function say(text, kind, hash) {
    if (statusEl) { statusEl.remove ? statusEl.remove() : null; statusEl = null; }
    if (!text) return;
    statusEl = h("div", { class: "status" + (kind ? " " + kind : ""), role: "status" }, [text, hash ? " " : null, hash ? txLink(hash) : null]);
    document.body.appendChild(statusEl);
    if (kind === "ok") setTimeout(function () { if (statusEl && statusEl.textContent.indexOf(text) === 0) say(null); }, 6000);
  }
  function confirmAction(title, lines) {
    var dlg = document.getElementById("dlg");
    return new Promise(function (resolve) {
      dlg.textContent = "";
      var done = function (v) { if (dlg.close) dlg.close(); resolve(v); };
      dlg.appendChild(h("h3", { id: "dlg-title" }, [title]));
      lines.forEach(function (l) { dlg.appendChild(h("p", null, [l])); });
      dlg.appendChild(h("div", { class: "acts" }, [
        h("button", { class: "btn ghost sm", onclick: function () { done(false); } }, ["Cancel"]),
        h("button", { class: "btn sm", onclick: function () { done(true); } }, ["Continue to wallet"])
      ]));
      if (dlg.showModal) dlg.showModal();
    });
  }
  function guard(fn) {
    return function () { return Promise.resolve().then(fn).catch(function (e) { if (e && e.message !== "session ended") say(errText(e), "err"); }); };
  }

  // ---------- API (the session token is attached when there is one)
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

  // ---------- wallet
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
  async function ensureChain() {
    var id = await S.provider.request({ method: "eth_chainId" });
    if (String(id).toLowerCase() === C.chainIdHex) return;
    try {
      await S.provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: C.chainIdHex }] });
    } catch (e) {
      await S.provider.request({ method: "wallet_addEthereumChain", params: [{
        chainId: C.chainIdHex, chainName: C.chainName, nativeCurrency: C.nativeCurrency, rpcUrls: [C.rpc], blockExplorerUrls: [C.explorer] }] });
    }
  }
  async function waitReceipt(hash) {
    for (var i = 0; i < 60; i++) {
      var rc = await S.provider.request({ method: "eth_getTransactionReceipt", params: [hash] });
      if (rc) return rc;
      await sleep(2000);
    }
    throw new Error("Still pending after two minutes. Check the explorer, then refresh this page.");
  }
  async function sendTx(data) {
    say("Confirm the transaction in your wallet...");
    var hash = await S.provider.request({ method: "eth_sendTransaction", params: [{ from: S.account, to: C.enforcer, data: data }] });
    say("Waiting for the chain to confirm...", null, hash);
    var rc = await waitReceipt(hash);
    if (rc.status !== "0x1") throw new Error("The transaction failed on-chain.");
    return hash;
  }
  // The server re-checks the transaction on-chain; its node can lag the wallet's, so retry briefly.
  async function record(action, hash, invoiceId) {
    for (var i = 0; i < 6; i++) {
      try { return await api("/admin/actions", { method: "POST", body: { action: action, tx_hash: hash, invoice_id: invoiceId } }); }
      catch (e) { if (!/not confirmed/.test(e.message) || i === 5) throw e; await sleep(2000); }
    }
  }
  async function onchain(action, confirmTitle, lines, data, invoiceId) {
    if (!(await confirmAction(confirmTitle, lines))) return false;
    var hash = await sendTx(data);
    await record(action, hash, invoiceId);
    say("Done. Recorded in the activity log.", "ok", hash);
    return true;
  }

  // ---------- gate: the only thing visible before the admin signs in
  function brand() {
    return h("div", { class: "brand" }, [
      h("span", { "aria-hidden": "true" }, ["\u25A0 "]), "CeedeBooks admin"]);
  }
  function mountGate(parts) {
    root.textContent = "";
    root.appendChild(h("div", { class: "gate" }, [h("div", { class: "gate-in" }, [brand()].concat(parts))]));
  }
  function gateIdle(msg, isErr) {
    var parts = [];
    if (msg) parts.push(h("p", { class: isErr ? "err" : "" }, [msg]));
    if (!S.providers.length) {
      parts.push(h("p", null, ["No wallet found. Open this page in the MetaMask or Rabby app's browser, or in a browser with the MetaMask or Rabby extension."]));
    } else {
      S.providers.forEach(function (p) {
        var label = S.providers.length > 1 ? "Connect " + p.info.name : "Connect wallet";
        parts.push(h("button", { class: "btn", onclick: guardGate(function () { return connect(p); }) }, [label]));
      });
    }
    mountGate(parts);
  }
  function guardGate(fn) { return function () { return Promise.resolve().then(fn).catch(function (e) { gateIdle(errText(e), true); }); }; }

  async function connect(p) {
    var accounts = await p.provider.request({ method: "eth_requestAccounts" });
    S.provider = p.provider;
    S.account = String(accounts[0]).toLowerCase();
    await ensureChain();
    if (S.provider.on && !S.bound) { S.bound = true; S.provider.on("accountsChanged", function () { signedOut("Wallet changed. Connect again."); }); }
    var st = await fetch(C.api + "/admin/auth/state").then(function (r) { if (!r.ok) throw new Error("Could not read the chain right now."); return r.json(); });
    if (S.account === st.approver) return gateSign();
    if (S.account === st.pending_approver) return gateAccept();
    mountGate([
      h("p", { class: "err" }, ["This wallet is not the admin wallet."]),
      h("p", { class: "addr" }, [S.account]),
      h("button", { class: "btn", onclick: function () { gateIdle(); } }, ["Use another wallet"])
    ]);
  }
  function gateSign() {
    mountGate([
      h("p", null, ["Admin wallet connected."]), h("p", { class: "addr" }, [S.account]),
      h("button", { class: "btn", onclick: guardGate(signIn) }, ["Sign in"]),
      h("p", { class: "note" }, ["Signing a message sends no transaction and costs nothing."])
    ]);
  }
  function gateAccept() {
    mountGate([
      h("p", null, ["This wallet has been proposed as the new admin."]), h("p", { class: "addr" }, [S.account]),
      h("button", { class: "btn", onclick: guardGate(acceptRole) }, ["Accept admin role"]),
      h("p", { class: "note" }, ["This sends one transaction. After it confirms, this wallet is the only admin."])
    ]);
  }
  async function acceptRole() {
    if (!(await confirmAction("Accept the admin role", ["Your wallet becomes the contract's approver.", "The previous approver key loses all admin powers."]))) return;
    await sendTx(W.CALLS.acceptApprover());
    say("You are now the admin. Sign in.", "ok");
    gateSign();
  }
  async function signIn() {
    var ch = await api("/admin/auth/challenge", { method: "POST", body: { address: S.account } });
    var sig = await S.provider.request({ method: "personal_sign", params: [W.utf8Hex(ch.message), S.account] });
    var out = await api("/admin/auth/verify", { method: "POST", body: { nonce: ch.nonce, signature: sig } });
    S.token = out.token;
    say(null);
    await renderApp();
  }
  function signedOut(msg) {
    S.token = null; S.overview = null;
    say(null);
    gateIdle(msg, !!msg);
  }

  // ---------- app shell
  async function renderApp() {
    root.textContent = "";
    var tabs = h("div", { class: "tabs", role: "tablist" }, TABS.map(function (t) {
      return h("button", { role: "tab", "data-tab": t[0], onclick: guard(function () { return show(t[0]); }) }, [t[1]]);
    }));
    var main = h("main", { id: "view" });
    root.appendChild(h("header", { class: "top" }, [
      h("div", { class: "top-in" }, [brand(), h("span", { class: "who" }, [W.short(S.account)]),
        h("button", { class: "btn ghost sm", onclick: guard(logout) }, ["Sign out"])]),
      tabs]));
    root.appendChild(main);
    await show(S.tab);
  }
  async function logout() {
    try { await api("/admin/auth/logout", { method: "POST" }); } catch (e) { /* already gone */ }
    signedOut("Signed out.");
  }
  async function show(tab) {
    S.tab = tab;
    Array.prototype.forEach.call(root.querySelectorAll ? root.querySelectorAll("[data-tab]") : [], function (b) {
      b.setAttribute("aria-current", String(b.getAttribute("data-tab") === tab));
    });
    var view = document.getElementById("view");
    view.textContent = "";
    view.appendChild(h("p", { class: "note" }, ["Loading..."]));
    var node = await VIEWS[tab]();
    view.textContent = "";
    view.appendChild(node);
  }

  function table(head, rows) {
    return h("div", { class: "scroll" }, [h("table", { class: "tbl" }, [
      h("thead", null, [h("tr", null, head.map(function (c) { return h("th", null, [c]); }))]),
      h("tbody", null, rows.map(function (r) { return h("tr", null, r.map(function (c, i) { return h("td", head[i] ? { "data-label": head[i] } : null, [c]); })); }))])]);
  }
  function chip(text, kind) { return h("span", { class: "chip " + kind }, [text]); }
  function card(label, value) { return h("div", { class: "card" }, [h("div", { class: "l" }, [label]), h("div", { class: "v" }, [String(value)])]); }
  function field(label, input) { return h("label", null, [label, input]); }
  async function overview() { S.overview = await api("/admin/overview"); return S.overview; }
  function categories() { return (S.overview && S.overview.chain.categories) || []; }

  // ---------- views
  var VIEWS = {
    overview: async function () {
      var o = await overview(), c = o.chain, inv = o.invoices;
      var wrap = h("div", null, [h("h2", null, ["Overview"])]);
      if (!c.chain_ok) wrap.appendChild(h("p", { class: "note" }, ["The chain could not be read just now. Refresh in a moment."]));
      else {
        wrap.appendChild(h("div", { class: "cards" }, [
          card("Pool balance", usdc(c.balance_usdc)), card("Payments", c.paused ? "PAUSED" : "Active"),
          card("Daily limit", usdc(c.daily_limit_usdc)), card("Per-payment limit", usdc(c.per_tx_limit_usdc)),
          card("Escalations waiting", inv.escalated || 0), card("Paid invoices", inv.paid || 0),
          card("Vendors", o.vendors), card("Purchase orders", o.purchase_orders)]));
        wrap.appendChild(h("h3", null, ["Spending categories"]));
        wrap.appendChild(table(["Category", "Daily limit", "Left today"], c.categories.map(function (x) { return [x.name, usdc(x.daily_limit_usdc), usdc(x.remaining_usdc)]; })));
        wrap.appendChild(h("p", { class: "note" }, ["Admin wallet: " + c.approver]));
      }
      return wrap;
    },

    vendors: async function () {
      var data = await api("/admin/vendors");
      var name = h("input", { maxlength: "120", autocomplete: "off" }), wallet = h("input", { placeholder: "0x...", autocomplete: "off", spellcheck: "false" });
      var form = h("div", { class: "panel" }, [h("div", { class: "row" }, [
        field("Vendor name", name), field("Vendor wallet", wallet),
        h("button", { class: "btn sm", onclick: guard(async function () {
          await api("/vendors", { method: "POST", body: { name: name.value.trim(), wallet_address: wallet.value.trim() } });
          say("Vendor added. Approve it on-chain to let it be paid.", "ok");
          await show("vendors");
        }) }, ["Add vendor"])])]);
      var rows = data.vendors.map(function (v) {
        var state = v.approved_onchain === true ? chip("Approved on-chain", "ok") : v.approved_onchain === false ? chip("Not approved", "warn") : chip("Unknown", "bad");
        var act = v.approved_onchain === null ? "" : h("button", { class: "btn ghost sm", onclick: guard(async function () {
          var approve = v.approved_onchain === false;
          if (await onchain("set_vendor", approve ? "Approve vendor" : "Revoke vendor",
            [v.name, v.wallet_address, approve ? "The agent will be able to pay this wallet within the limits." : "The contract will refuse every payment to this wallet."],
            W.CALLS.setVendor(v.wallet_address, approve))) await show("vendors");
        }) }, [v.approved_onchain ? "Revoke" : "Approve on-chain"]);
        return [String(v.id), v.name, h("span", { class: "mono" }, [v.wallet_address]), state, act];
      });
      return h("div", null, [h("h2", null, ["Vendors"]), form, h("h3", null, ["Registered vendors"]), table(["ID", "Name", "Wallet", "Status", ""], rows)]);
    },

    pos: async function () {
      var results = await Promise.all([api("/admin/purchase-orders"), api("/admin/vendors"), overview()]);
      var pos = results[0].purchase_orders, vendors = results[1].vendors;
      var num = h("input", { maxlength: "64", autocomplete: "off" }), amt = h("input", { inputmode: "decimal", placeholder: "5.00" });
      var vsel = h("select", null, vendors.map(function (v) { return h("option", { value: String(v.id) }, [v.name]); }));
      var csel = h("select", null, categories().map(function (c) { return h("option", { value: String(c.id) }, [c.name]); }));
      var form = h("div", { class: "panel" }, [h("div", { class: "row" }, [
        field("PO number", num), field("Vendor", vsel), field("Amount (USDC)", amt), field("Category", csel),
        h("button", { class: "btn sm", onclick: guard(async function () {
          await api("/purchase-orders", { method: "POST", body: { po_number: num.value.trim(), vendor_id: Number(vsel.value), amount_usdc: amt.value.trim(), category: Number(csel.value) } });
          say("Purchase order created.", "ok");
          await show("pos");
        }) }, ["Create PO"])])]);
      var names = {}; categories().forEach(function (c) { names[c.id] = c.name; });
      var rows = pos.map(function (p) {
        var act = p.received ? chip("Receipt confirmed", "ok") : h("button", { class: "btn ghost sm", onclick: guard(async function () {
          if (!(await confirmAction("Confirm receipt", ["You confirm " + p.vendor_name + " delivered what " + p.po_number + " ordered.", "Only the buyer can do this; it lets the agent pay."]))) return;
          await api("/receipts", { method: "POST", body: { po_id: p.id } });
          say("Receipt confirmed.", "ok");
          await show("pos");
        }) }, ["Confirm receipt"]);
        return [p.po_number, p.vendor_name, usdc(p.amount_usdc), names[p.category] || String(p.category), act];
      });
      return h("div", null, [h("h2", null, ["Purchase orders"]), form, h("h3", null, ["Orders"]), table(["PO", "Vendor", "Amount", "Category", "Receipt"], rows)]);
    },

    invoices: async function () {
      var data = await api("/admin/invoices");
      var kinds = { paid: "ok", held: "warn", escalated: "warn", rejected: "bad", error: "bad" };
      var rows = data.invoices.map(function (i) {
        var acts = [];
        if (i.status === "escalated") {
          ["approve", "reject"].forEach(function (verb) {
            acts.push(h("button", { class: "btn ghost sm", onclick: guard(async function () {
              var key = (await api("/admin/invoices/" + i.id + "/escalation")).invoice_key;
              var ok = await onchain(verb + "_escalation", verb === "approve" ? "Approve and pay" : "Reject payment",
                [i.invoice_number + " from " + i.vendor_name, usdc(i.amount_usdc), verb === "approve" ? "USDC leaves the pool to the vendor." : "The invoice will not be paid."],
                verb === "approve" ? W.CALLS.approveEscalation(key) : W.CALLS.rejectEscalation(key), i.id);
              if (ok) await show("invoices");
            }) }, [verb === "approve" ? "Approve and pay" : "Reject"]));
          });
        }
        return [i.invoice_number, i.vendor_name, usdc(i.amount_usdc), chip(i.status, kinds[i.status] || "warn"),
          h("span", { class: "mono" }, [i.reasoning_hash ? W.short(i.reasoning_hash) : ""]), h("div", { class: "actions" }, acts)];
      });
      return h("div", null, [h("h2", null, ["Invoices"]), table(["Invoice", "Vendor", "Amount", "Status", "Reasoning hash", ""], rows),
        h("p", { class: "note" }, ["Escalations from before v1.2.4 have no recorded transaction and cannot be settled here."])]);
    },

    limits: async function () {
      var o = await overview(), c = o.chain;
      var csel = h("select", null, categories().map(function (x) { return h("option", { value: String(x.id) }, [x.name]); }));
      var amt = h("input", { inputmode: "decimal", placeholder: "20.00" });
      var form = h("div", { class: "panel" }, [h("div", { class: "row" }, [
        field("Category", csel), field("Daily limit (USDC)", amt),
        h("button", { class: "btn sm", onclick: guard(async function () {
          var units = W.usdcToUnits(amt.value);
          if (await onchain("set_category_limit", "Set category limit", [csel.options ? "Category " + csel.value : "", "New daily limit: " + amt.value.trim() + " USDC"],
            W.CALLS.setCategoryDailyLimit(Number(csel.value), units))) await show("limits");
        }) }, ["Set limit"])])]);
      var pause = h("div", { class: "panel" }, [
        h("p", null, [c.paused ? "Payments are PAUSED. The agent cannot pay anyone." : "Payments are active."]),
        h("button", { class: "btn sm" + (c.paused ? "" : " warn"), onclick: guard(async function () {
          var next = !c.paused;
          if (await onchain("set_paused", next ? "Pause all payments" : "Resume payments",
            [next ? "The agent cannot pay anyone until you resume." : "The agent can pay again within the limits."], W.CALLS.setPaused(next))) await show("limits");
        }) }, [c.paused ? "Resume payments" : "Pause all payments"])]);
      return h("div", null, [h("h2", null, ["Limits and safety"]),
        table(["Category", "Daily limit", "Left today"], (c.categories || []).map(function (x) { return [x.name, usdc(x.daily_limit_usdc), usdc(x.remaining_usdc)]; })),
        h("h3", null, ["Set a category limit"]), form, h("h3", null, ["Emergency stop"]), pause]);
    },

    activity: async function () {
      var data = await api("/admin/actions");
      var rows = data.actions.map(function (a) {
        return [when(a.created_at), a.action, a.ref || "", a.tx_hash ? txLink(a.tx_hash) : ""];
      });
      return h("div", null, [h("h2", null, ["Activity"]), table(["When", "Action", "Reference", "Transaction"], rows)]);
    }
  };

  // ---------- start
  discover().then(function (found) { S.providers = found; gateIdle(); });
})();
