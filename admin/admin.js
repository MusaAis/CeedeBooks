/* CeedeBooks admin site. Everything here is inert until the approver wallet signs in: the server refuses every /admin
   request without a session, so hiding the interface is a courtesy and the checks on the server are the protection.
   Data is written with textContent only (no innerHTML). The session token lives in this variable, never in storage. */
(function () {
  "use strict";
  var W = window.CeedeWallet;
  var C = W.CONFIG;
  var root = document.getElementById("root");
  var S = { providers: [], provider: null, account: null, token: null, overview: null, tab: "overview", bound: false, contract: null };
  // Which business to sign in to: /?business=<slug> (default: the first business). Every business has its own contract.
  var BIZ = (function () {
    var q = typeof window.location !== "undefined" && window.location.search ? new URLSearchParams(window.location.search).get("business") : null;
    return q && /^[a-z0-9][a-z0-9-]*$/.test(q) ? q : null;
  })();
  var TABS = [["overview", "Overview"], ["applications", "Applications"], ["vendors", "Vendors"], ["pos", "Purchase orders"], ["invoices", "Invoices"], ["limits", "Limits and safety"], ["activity", "Activity"]];

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
  // The plain text for `arc-canteen update-traction`. No blank lines: an empty line ends the CLI's input.
  function tractionText(name, t) {
    var live = t.live, sh = t.shadow, ag = sh.agreement;
    var real = Object.keys(sh.real_totals).map(function (c) { return sh.real_totals[c] + " " + c; }).join(", ");
    var lines = [
      name + " traction update (Arc testnet)",
      "Live: " + live.paid + " paid, " + live.held + " held, " + live.escalated + " escalated, " + live.volume_usdc + " USDC paid",
      "Shadow (real bills, each mirrored as a testnet payment only after the owner approved): " + sh.paid + " paid, " + sh.mirrored_usdc + " USDC mirrored" +
        (real ? ", real bills " + real : "") + ", " + sh.held + " held, " + sh.escalated + " escalated, " + sh.awaiting_owner + " waiting for the owner",
      ag.n ? "Owner agreement with the agent: " + Math.round(ag.rate * 100) + "% (" + ag.agree + " agree, " + ag.disagree + " disagree, " + ag.n + " verdicts)" : "Owner agreement with the agent: no verdicts yet"
    ];
    t.latest_paid.slice(0, 3).forEach(function (p) { lines.push("Paid " + p.invoice + (p.mode === "shadow" ? " (shadow)" : "") + ": " + (p.tx_url || "no transaction link yet")); });
    return lines.join("\n");
  }
  async function sha256Hex(bytes) {
    var d = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    return Array.prototype.map.call(d, function (b) { return (b < 16 ? "0" : "") + b.toString(16); }).join("");
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
  function confirmAction(title, lines, okLabel) {
    var dlg = document.getElementById("dlg");
    return new Promise(function (resolve) {
      dlg.textContent = "";
      var done = function (v) { if (dlg.close) dlg.close(); resolve(v); };
      dlg.appendChild(h("h3", { id: "dlg-title" }, [title]));
      lines.forEach(function (l) { dlg.appendChild(h("p", null, [l])); });
      dlg.appendChild(h("div", { class: "acts" }, [
        h("button", { class: "btn ghost sm", onclick: function () { done(false); } }, ["Cancel"]),
        h("button", { class: "btn sm", onclick: function () { done(true); } }, [okLabel || "Continue to wallet"])
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
  async function sendTx(data, to) {
    say("Confirm the transaction in your wallet...");
    var hash = await S.provider.request({ method: "eth_sendTransaction", params: [{ from: S.account, to: to || S.contract || C.enforcer, data: data }] });
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
      h("img", { src: "logo.png", alt: "", width: "28", height: "28", class: "logo" }), "CeedeBooks admin"]);
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
    var st = await fetch(C.api + "/admin/auth/state" + (BIZ ? "?business=" + encodeURIComponent(BIZ) : "")).then(function (r) { if (!r.ok) throw new Error("Could not read the chain right now."); return r.json(); });
    S.contract = st.contract || C.enforcer;
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
    var ch = await api("/admin/auth/challenge", { method: "POST", body: BIZ ? { address: S.account, business: BIZ } : { address: S.account } });
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
    await overview();  // also tells us whether this session is the operator's (the home business's admin)
    var tabList = TABS.concat(S.overview.operator ? [["businesses", "Businesses"]] : []);
    var tabs = h("div", { class: "tabs", role: "tablist" }, tabList.map(function (t) {
      return h("button", { role: "tab", "data-tab": t[0], onclick: guard(function () { return show(t[0]); }) }, [t[1]]);
    }));
    var main = h("main", { id: "view" });
    root.appendChild(h("header", { class: "top" }, [
      h("div", { class: "top-in" }, [brand(), h("span", { class: "who" }, [(S.overview.chain && S.overview.chain.business ? S.overview.chain.business + " · " : "") + W.short(S.account)]),
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
  // Put USDC into this business's contract. It is a token transfer on the USDC contract; the wallet's normal Send button
  // does a native transfer, which the contract rejects.
  function fundPanel() {
    var amount = h("input", { id: "fund-amount", inputmode: "decimal", autocomplete: "off", placeholder: "20" });
    return h("div", { class: "panel" }, [
      h("h3", null, ["Add funds to your pool"]),
      h("p", { class: "note" }, ["Sends USDC from your own wallet into your contract, where the agent can pay approved vendors from it. Use this button: your wallet's normal Send does not work for a contract. Only your owner wallet can ever take the money out."]),
      h("label", null, ["Amount in USDC", amount]),
      h("button", { class: "btn", onclick: guard(async function () {
        var units = W.usdcToUnits(amount.value);
        if (units <= 0n) throw new Error("Enter an amount above zero.");
        var to = S.contract || C.enforcer;
        if (!(await confirmAction("Add funds to your pool", ["Amount: " + amount.value.trim() + " USDC", "From your wallet: " + S.account, "Into your contract: " + to, "This is a USDC token transfer. The funds sit in your contract."]))) return;
        var hash = await sendTx(W.CALLS.usdcTransfer(to, units), W.CONFIG.usdc);
        say("Funds added.", "ok", hash);
        await sleep(2500);  // let the server's node catch up before the balance is read again
        await show("overview");
      }) }, ["Add funds"])]);
  }
  // The owner's setup checklist. Unknown steps are shown as unknown, never as done. Hidden once everything is done.
  async function setupChecklist() {
    var data;
    try { data = await api("/admin/onboarding"); } catch (e) { return null; }
    if (!data || !data.steps || data.steps.every(function (x) { return x.done === true; })) return null;
    var items = data.steps.map(function (x) {
      return h("li", null, [chip(x.done === true ? "Done" : x.done === false ? "To do" : "Unknown", x.done === true ? "ok" : x.done === false ? "warn" : "bad"), " " + x.label]);
    });
    return h("div", { class: "panel" }, [h("h3", null, ["Finish setting up"]), h("ul", { class: "steps" }, items), h("p", { class: "note" }, [data.custody])]);
  }
  function copyPanel(biz) {
    var out = h("textarea", { readonly: "readonly", rows: "6", "aria-label": "Traction update text" });
    return h("div", { class: "panel" }, [h("p", null, ["Traction update for the Canteen CLI, built from your public numbers."]),
      h("button", { class: "btn ghost sm", onclick: guard(async function () {
        var t = await api("/businesses/" + biz.slug + "/traction");
        var text = tractionText(biz.name, t);
        out.value = text;
        try { await navigator.clipboard.writeText(text); say("Copied. Paste it into arc-canteen update-traction.", "ok"); }
        catch (e) { say("Select the text below and copy it.", "ok"); }
      }) }, ["Copy traction update"]), out]);
  }
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
          card("Vendors", o.vendors), card("Purchase orders", o.purchase_orders),
          card("Applications waiting", o.pending_applications || 0)]));
        wrap.appendChild(fundPanel());
        wrap.appendChild(copyPanel(o.business));
        wrap.appendChild(h("h3", null, ["Spending categories"]));
        wrap.appendChild(table(["Category", "Daily limit", "Left today"], c.categories.map(function (x) { return [x.name, usdc(x.daily_limit_usdc), usdc(x.remaining_usdc)]; })));
        wrap.appendChild(h("p", { class: "note" }, ["Admin wallet: " + c.approver]));
      }
      var setup = await setupChecklist();
      if (setup) wrap.insertBefore ? wrap.insertBefore(setup, wrap.children[1] || null) : wrap.appendChild(setup);
      return wrap;
    },

    applications: async function () {
      var data = await api("/admin/applications");
      var kind = { pending: "warn", accepted: "ok", rejected: "bad" };
      var decide = function (a, verb) {
        return h("button", { class: "btn ghost sm", onclick: guard(async function () {
          var accept = verb === "accept";
          if (!(await confirmAction(accept ? "Accept this application" : "Reject this application",
            [a.business_name, a.wallet, accept ? "A vendor record is created. It still cannot be paid until you approve its wallet on-chain in the Vendors tab." : "The applicant can apply again."],
            accept ? "Accept" : "Reject"))) return;
          await api("/admin/applications/" + a.id + "/" + verb, { method: "POST" });
          say(accept ? "Vendor added. Now approve it on-chain in the Vendors tab." : "Application rejected.", "ok");
          await show("applications");
        }) }, [verb === "accept" ? "Accept" : "Reject"]);
      };
      var rows = data.applications.map(function (a) {
        var act = a.status === "pending" ? h("span", { class: "actions" }, [decide(a, "accept"), " ", decide(a, "reject")]) : "";
        return [a.business_name, h("span", { class: "mono" }, [a.wallet]), a.contact || "", when(a.created_at), chip(a.status, kind[a.status] || "warn"), act];
      });
      return h("div", null, [h("h2", null, ["Applications"]),
        h("p", { class: "note" }, ["People who proved they control a payee wallet by signing a message. Contact details are visible only here."]),
        rows.length ? table(["Business", "Wallet", "Contact", "Applied", "Status", ""], rows) : h("p", { class: "note" }, ["No applications yet."])]);
    },

    businesses: async function () {
      var data = await api("/operator/business-applications");
      var kind = { pending: "warn", accepting: "warn", accepted: "ok", registered: "ok", rejected: "bad" };
      var decide = function (a, verb) {
        return h("button", { class: "btn ghost sm", onclick: guard(async function () {
          var accept = verb === "accept";
          if (!(await confirmAction(accept ? "Accept this business" : "Reject this business",
            [a.name + " (" + a.slug + ")", a.owner_wallet, accept ? "A hosted agent wallet is created for it under our Circle account. Its owner then creates the contract from their own wallet. It can only pay that business's own approved vendors within its own limits." : "The applicant can apply again."],
            accept ? "Accept" : "Reject"))) return;
          var out = await api("/operator/business-applications/" + a.id + "/" + verb, { method: "POST" });
          say(accept ? "Accepted. Agent wallet " + out.business.agent_address + ". Tell the owner to open the business page." : "Application rejected.", "ok");
          await show("businesses");
        }) }, [verb === "accept" ? "Accept" : "Reject"]);
      };
      var appRows = data.applications.map(function (a) {
        var act = a.status === "pending" ? h("span", { class: "actions" }, [decide(a, "accept"), " ", decide(a, "reject")]) : "";
        return [a.name, h("span", { class: "mono" }, [a.slug]), h("span", { class: "mono" }, [a.owner_wallet]), a.contact || "", when(a.created_at), chip(a.status, kind[a.status] || "warn"), act];
      });
      var bizRows = data.businesses.map(function (b) {
        var mark = (b.status === "active" && b.id !== 1) ? h("button", { class: "btn ghost sm", onclick: guard(async function () {
          var to = !b.external;
          if (!(await confirmAction(to ? "Mark as an outside business" : "Remove the outside mark",
            [b.name, to ? "Only do this after you have confirmed a real outside party owns it and real money is on the other side of its payments." : "It will no longer count as an outside business."],
            to ? "Mark as outside" : "Remove mark"))) return;
          await api("/operator/businesses/" + b.id + "/external", { method: "POST", body: { external: to } });
          say("Updated.", "ok");
          await show("businesses");
        }) }, [b.external ? "Remove outside mark" : "Mark as outside"]) : "";
        return [b.name, h("span", { class: "mono" }, [b.slug]), chip(b.status, kind[b.status] || "warn"), b.id === 1 ? "Your own" : (b.external ? "Outside" : "Not marked"),
          h("span", { class: "mono" }, [b.enforcer_address || "not created yet"]), h("span", { class: "mono" }, [b.agent_address || ""]), mark];
      });
      return h("div", null, [h("h2", null, ["Businesses"]),
        h("p", { class: "note" }, ["Only you see this tab. Accepting creates a hosted agent wallet for the business: say so to them (it can only pay their approved vendors within their limits, and they can replace it any time)."]),
        h("h3", null, ["Applications"]),
        appRows.length ? table(["Business", "Name in URL", "Owner wallet", "Contact", "Applied", "Status", ""], appRows) : h("p", { class: "note" }, ["No applications yet."]),
        h("h3", null, ["All businesses"]),
        table(["Business", "Name in URL", "Status", "Counts as", "Contract", "Agent wallet", ""], bizRows)]);
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
      var results = await Promise.all([api("/admin/invoices"), api("/admin/purchase-orders")]);
      var data = results[0], pos = results[1].purchase_orders.filter(function (p) { return p.received; });
      var kinds = { paid: "ok", held: "warn", escalated: "warn", rejected: "bad", error: "bad" };
      var labels = { awaiting_owner: "waiting for you", approving: "paying" };
      var psel = h("select", null, pos.map(function (p) { return h("option", { value: String(p.id) }, [p.po_number + " · " + p.vendor_name + " · " + usdc(p.amount_usdc)]); }));
      var num = h("input", { maxlength: "64", autocomplete: "off", placeholder: "INV-001" });
      var real = h("input", { inputmode: "decimal", placeholder: "1200.50" }), cur = h("input", { maxlength: "3", autocomplete: "off", placeholder: "NGN" });
      var docHash = h("input", { autocomplete: "off", spellcheck: "false", placeholder: "64-character SHA-256, or pick the file" });
      var file = h("input", { type: "file" });
      file.addEventListener("change", guard(async function () {
        var f = file.files && file.files[0];
        if (!f) return;
        docHash.value = await sha256Hex(await f.arrayBuffer());
        say("Hashed in your browser. The file itself is not uploaded.", "ok");
      }));
      var shadow = h("div", { class: "panel" }, [h("div", { class: "row" }, [
        field("Purchase order (receipt confirmed)", psel), field("Invoice number", num), field("Real amount of the bill", real), field("Currency", cur),
        field("Invoice file", file), field("Document hash", docHash),
        h("button", { class: "btn sm", onclick: guard(async function () {
          var po = pos.filter(function (p) { return String(p.id) === psel.value; })[0];
          if (!po) throw new Error("Create a purchase order and confirm its receipt first.");
          var hash = String(docHash.value || "").trim().toLowerCase().replace(/^0x/, "");
          if (!/^[0-9a-f]{64}$/.test(hash)) throw new Error("The document hash must be 64 hex characters (a SHA-256). Pick the file to hash it here.");
          var code = String(cur.value || "").trim().toUpperCase();
          if (!/^[A-Z]{3}$/.test(code)) throw new Error("The currency is three letters, for example NGN.");
          var r = await api("/invoices", { method: "POST", body: { invoice_number: num.value.trim(), vendor_id: po.vendor_id, amount_usdc: String(po.amount_usdc),
            category: po.category, doc_hash: hash, po_number: po.po_number, mode: "shadow", real_amount: real.value.trim(), real_currency: code } });
          say("Decision: " + r.decision + (r.decision === "await_owner" ? ". It waits below for your approval." : "."), "ok");
          await show("invoices");
        }) }, ["Submit shadow invoice"])]),
        h("p", { class: "note" }, ["The real amount and currency are shown publicly on your proof page. The testnet payment mirrors the PO amount in USDC and only goes out after you approve."])]);
      var rows = data.invoices.map(function (i) {
        var acts = [], shadowRow = i.mode === "shadow";
        async function judge(path, text, lines, done) {
          if (!(await confirmAction(text, lines))) return;
          await api(path, { method: "POST", body: path.indexOf("/verdict") > -1 ? { verdict: done } : undefined });
          say(done === "approved" ? "Paid on testnet. Recorded: you agreed with the agent." : done === "disagree" ? "Recorded: you would not have done the same." : "Recorded: you would have done the same.", "ok");
          await show("invoices");
        }
        if (shadowRow && i.status === "awaiting_owner") {
          acts.push(h("button", { class: "btn ghost sm", onclick: guard(function () {
            return judge("/admin/invoices/" + i.id + "/shadow-approve", "Approve and pay (testnet)",
              [i.invoice_number + " from " + i.vendor_name, "Real bill: " + i.real_amount + " " + i.real_currency, "Mirrored as " + usdc(i.amount_usdc) + " testnet USDC to the vendor.",
               "The contract still enforces your limits. You also record that you agree with the agent."], "approved");
          }) }, ["Approve and pay"]));
          acts.push(h("button", { class: "btn ghost sm", onclick: guard(function () {
            return judge("/admin/invoices/" + i.id + "/shadow-reject", "Reject payment",
              [i.invoice_number + " from " + i.vendor_name, "Nothing is paid. You also record that you would not have done the same."], "disagree");
          }) }, ["Reject"]));
        }
        if (shadowRow && (i.status === "held" || i.status === "escalated") && !i.verdict && i.reasoning_hash) {
          [["agree", "I would do the same"], ["disagree", "I would not"]].forEach(function (v) {
            acts.push(h("button", { class: "btn ghost sm", onclick: guard(function () {
              return judge("/admin/decisions/" + i.reasoning_hash + "/verdict", "Record your verdict",
                [i.invoice_number + ": the agent did not pay this one.", v[0] === "agree" ? "You record that you would have done the same." : "You record that you would not have done the same.", "A verdict cannot be changed."], v[0]);
            }) }, [v[1]]));
          });
        }
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
        var kind = shadowRow ? chip("shadow", "warn") : "";
        var status = [chip(labels[i.status] || i.status, kinds[i.status] || "warn")];
        if (i.verdict) status.push(chip(i.verdict === "agree" ? "you agreed" : "you disagreed", i.verdict === "agree" ? "ok" : "bad"));
        return [i.invoice_number, i.vendor_name, usdc(i.amount_usdc), shadowRow ? i.real_amount + " " + i.real_currency : "", h("span", null, [kind].concat(status)),
          h("span", { class: "mono" }, [i.reasoning_hash ? W.short(i.reasoning_hash) : ""]), h("div", { class: "actions" }, acts)];
      });
      return h("div", null, [h("h2", null, ["Invoices"]), h("h3", null, ["Submit a shadow invoice"]), shadow,
        h("h3", null, ["All invoices"]), table(["Invoice", "Vendor", "Amount", "Real bill", "Status", "Reasoning hash", ""], rows),
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
