/* Page wiring for index.html. Depends on verify.js. Decision text is untrusted input, so nothing here uses innerHTML. */
(function () {
  "use strict";
  var V = window.CeedeVerify;
  var C = V.CONFIG;
  var DOMAIN_HASH = "46f65d7786a368e6e0814c938b8af9a7f6bed20bb1196478ac0a32708fa6f14a";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function anchor(text, href) {
    var a = el("a", null, text);
    a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
    return a;
  }
  function sleep(ms) { return reduce ? Promise.resolve() : new Promise(function (r) { setTimeout(r, ms); }); }
  function shortHash(h) { return h.slice(0, 10) + "..." + h.slice(-6); }
  function when(ts) { return new Date(Number(ts) * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC"; }
  function usdc(n) { return Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 }) + " USDC"; }
  async function getJson(path) {
    var res = await fetch(C.api + path);
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  // ---------- the check: returns what happened, rendering is separate so the hero and the verifier share it
  async function runCheck(hash) {
    var rec;
    try { rec = await getJson("/decisions/" + hash); }
    catch (e) { return { missing: true }; }

    var steps = [{ s: "ok", t: "Found the record in the audit log." }];
    var local = await V.checkRecord(rec, hash);
    steps.push({ s: local.hashMatches ? "ok" : "bad", t: local.hashMatches
      ? "The fingerprint matches. SHA-256 of the saved text, calculated in your browser, equals the hash."
      : "The fingerprint does not match. The saved text does not hash to this value." });
    steps.push({ s: local.fieldsMatch ? "ok" : "bad", t: local.fieldsMatch
      ? "Nothing was edited. The record shown is exactly the text that was hashed."
      : "The record shown is not what was hashed." });

    var chain = "none";
    if (V.isTxHash(rec.chain_tx_hash)) {
      var tx = { text: shortHash(rec.chain_tx_hash), href: C.explorer + "/tx/" + rec.chain_tx_hash };
      try {
        var got = await V.readReceipt(rec.chain_tx_hash, window.fetch.bind(window));
        var ev = V.findEvent(got.receipt, hash);
        if (ev.found && ev.success) {
          chain = "ok";
          steps.push({ s: "ok", t: "Found on-chain. A " + ev.event + " event in block " + ev.block + " carries the same hash. Read directly from " + new URL(got.rpc).host + ".", link: tx });
        } else {
          chain = "bad";
          steps.push({ s: "bad", t: "That transaction does not contain an event with this hash.", link: tx });
        }
      } catch (e) {
        chain = "unreachable";
        steps.push({ s: "note", t: "This browser could not reach a public Arc node, so the on-chain match did not run here. You can check the transaction yourself:", link: tx });
        try {
          var srv = (await getJson("/decisions/" + hash + "/verify")).onchain;
          if (srv && srv.checked) steps.push({ s: "note", t: "The CeedeBooks server's own lookup says " + (srv.match ? "it matches" : "it does not match") + ". That is not independent of the server." });
        } catch (e2) { /* keep the note above */ }
      }
    } else {
      steps.push({ s: "skip", t: "No blockchain transaction is linked to this decision. It exists in the off-chain audit log only." });
    }

    var failed = !local.hashMatches || !local.fieldsMatch || chain === "bad";
    var verdict = failed
      ? { k: "bad", t: "Failed. Do not trust this record." }
      : chain === "ok" ? { k: "ok", t: "Verified. The record is unchanged and its hash is on the blockchain." }
      : chain === "unreachable" ? { k: "warn", t: "Partly verified. The record is unchanged, but this browser could not match it on-chain." }
      : { k: "warn", t: "Recorded off-chain only. The record is unchanged, but no blockchain transaction backs it." };
    return { rec: rec, steps: steps, verdict: verdict };
  }

  function stepNode(st) {
    var li = el("li", "s-" + st.s);
    li.appendChild(el("span", "mark", st.s === "ok" ? "PASS" : st.s === "bad" ? "FAIL" : st.s === "skip" ? "N/A" : "NOTE"));
    var body = el("span", null, st.t);
    if (st.link) { body.appendChild(document.createTextNode(" ")); body.appendChild(anchor(st.link.text, st.link.href)); }
    li.appendChild(body);
    return li;
  }

  async function render(list, verdictBox, result) {
    list.textContent = "";
    for (var i = 0; i < result.steps.length; i++) {
      list.appendChild(stepNode(result.steps[i]));
      await sleep(380);
    }
    verdictBox.hidden = false;
    verdictBox.className = "verdict " + result.verdict.k;
    verdictBox.textContent = result.verdict.t;
  }

  // ---------- hero: runs the real check on the domain payment, once, on load
  async function heroCheck() {
    var list = $("hero-checks"), verdict = $("hero-verdict"), state = $("hero-state");
    try {
      var result = await runCheck(DOMAIN_HASH);
      if (result.missing) { state.textContent = "Record unavailable"; list.appendChild(stepNode({ s: "note", t: "The audit log did not return this record just now. Try again in a moment." })); return; }
      await render(list, verdict, result);
      state.textContent = "Checked just now";
    } catch (e) {
      state.textContent = "Could not run";
      list.appendChild(stepNode({ s: "note", t: "The live check could not run. Use the verifier below." }));
    }
  }

  // ---------- verifier
  async function runVerify() {
    var out = $("result");
    out.textContent = "";
    var hash = V.normalizeHash($("hash").value);
    if (!hash) { out.appendChild(el("p", "bad-text", "Enter a 64-character hexadecimal hash.")); return; }
    out.appendChild(el("p", "note", "Checking…"));
    var result = await runCheck(hash);
    out.textContent = "";
    if (result.missing) { out.appendChild(el("p", "bad-text", "No decision with that hash exists in the audit log. Check for a missing or extra character.")); return; }
    var box = el("div", "box");
    var list = el("ol", "checks");
    var verdict = el("p", "verdict");
    verdict.hidden = true;
    box.appendChild(list); box.appendChild(verdict);
    out.appendChild(box);
    var rec = result.rec;
    var card = el("div", "rec");
    card.appendChild(el("div", "meta", rec.action.replace("INVOICE_", "") + " for " + rec.subject + ", " + usdc(rec.amount_usdc) + ", " +
      (rec.model_used === "manual" ? "manual entry, not an agent decision" : "decided by " + rec.model_used) + ", " + when(rec.timestamp)));
    card.appendChild(el("p", null, rec.reasoning));
    out.appendChild(card);
    await render(list, verdict, result);
  }

  // ---------- stats
  async function loadStats() {
    try {
      var s = await getJson("/stats");
      $("n-paid").textContent = s.paid;
      $("n-held").textContent = s.held;
      $("n-esc").textContent = s.escalated;
      $("n-rate").textContent = s.refusal_rate == null ? "n/a" : Math.round(s.refusal_rate * 100) + "%";
      var total = s.decisions || 0;
      var segs = $("bar").children;
      [s.paid, s.held, s.escalated].forEach(function (n, i) { segs[i].style.width = total ? (100 * n / total) + "%" : "0%"; });
      var note = total === 0
        ? "No agent decisions yet. The first one will appear here."
        : "Of " + total + " decisions, the agent refused " + s.refused + ". A refusal is a decision the agent made not to pay. All of them so far come from the builder's own invoices.";
      if (s.manual_paid > 0) note += " " + (s.manual_paid === 1 ? "One payment" : s.manual_paid + " payments") + " (" + usdc(s.manual_paid_usdc) + ") run by hand through the contract " + (s.manual_paid === 1 ? "is" : "are") + " shown apart from the agent's decisions.";
      $("stats-note").textContent = note;
      var sub = s.submissions;
      if (sub && sub.by_origin) {
        var a = sub.by_origin.agent, m = sub.by_origin.manual, d = sub.by_origin.demo;
        $("n-inv").textContent = a.invoices_processed;
        $("n-vol").textContent = usdc(a.payment_volume_usdc);
        $("n-dup").textContent = a.duplicates_caught;
        var apart = [];
        if (m.invoices_processed) apart.push(m.invoices_processed + " run by hand (" + usdc(m.payment_volume_usdc) + ")");
        if (d.invoices_processed || d.duplicates_caught) apart.push(d.invoices_processed + " demo runs (" + usdc(d.payment_volume_usdc) + ")");
        $("traffic-note").textContent = "Testnet USDC has no market value. A duplicate caught is a repeated invoice number the system refused to take twice."
          + (apart.length ? " Shown apart, not counted above: " + apart.join(", ") + "." : "");
      }
    } catch (e) {
      $("stats-note").textContent = "The live counts could not be loaded just now.";
    }
  }

  // ---------- feed
  async function loadFeed() {
    var box = $("feed");
    try {
      var list = (await getJson("/decisions?limit=8")).decisions;
      box.textContent = "";
      if (!list.length) { box.textContent = "No decisions yet."; return; }
      list.forEach(function (d) {
        var kind = d.action.replace("INVOICE_", "").toLowerCase();
        var row = el("div", "item");
        row.appendChild(el("span", "chip " + kind, kind.toUpperCase()));
        var t = el("div", "t");
        t.appendChild(el("strong", null, d.subject));
        t.appendChild(el("span", null, usdc(d.amount_usdc) + ", " + (d.model_used === "manual" ? "manual entry" : "agent decision") + ", " + when(d.timestamp) + (d.chain_tx_hash ? ", on-chain" : ", off-chain only")));
        row.appendChild(t);
        var btn = el("button", "mini", "Verify");
        btn.type = "button";
        btn.addEventListener("click", function () {
          $("hash").value = d.reasoning_hash;
          runVerify();
          $("verify").scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
        });
        row.appendChild(btn);
        box.appendChild(row);
      });
    } catch (e) {
      box.textContent = "The decision feed could not be loaded just now.";
    }
  }

  $("go").addEventListener("click", runVerify);
  $("hash").addEventListener("keydown", function (e) { if (e.key === "Enter") runVerify(); });
  Array.prototype.forEach.call(document.querySelectorAll("[data-hash]"), function (b) {
    b.addEventListener("click", function () { $("hash").value = b.getAttribute("data-hash"); runVerify(); });
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-explorer]"), function (a) {
    a.href = C.explorer + a.getAttribute("data-explorer"); a.target = "_blank"; a.rel = "noopener noreferrer";
  });
  heroCheck();
  loadStats();
  loadFeed();
  // A link from the vendor portal (#check=<64 hex>) opens straight onto that decision's check.
  var deep = /^#check=([0-9a-f]{64})$/.exec(location.hash || "");
  if (deep) { $("hash").value = deep[1]; $("verify").scrollIntoView(); runVerify(); }
})();
