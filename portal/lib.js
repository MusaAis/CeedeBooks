/* CeedeBooks vendor portal: configuration and pure helpers. No dependencies; testable in Node. */
(function (root) {
  "use strict";
  var CONFIG = {
    api: "https://api.ceedebooks.xyz",
    proof: "https://ceedebooks.xyz",
    explorer: "https://explorer.testnet.arc.io"
  };
  function short(h) { return h && h.length > 14 ? h.slice(0, 8) + "..." + h.slice(-6) : (h || ""); }
  function utf8Hex(text) {
    return "0x" + Array.prototype.map.call(new TextEncoder().encode(text), function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }
  function hex(buf) { return Array.prototype.map.call(new Uint8Array(buf), function (b) { return b.toString(16).padStart(2, "0"); }).join(""); }
  // SHA-256 of a file's bytes, hex. The file never leaves the browser: only this hash is sent.
  async function sha256Hex(bytes) { return hex(await crypto.subtle.digest("SHA-256", bytes)); }
  function cleanDocHash(text) {
    var t = String(text || "").trim().toLowerCase().replace(/^0x/, "");
    if (!/^[0-9a-f]{64}$/.test(t)) throw new Error("The document hash must be 64 hex characters (a SHA-256). Pick the file to hash it here.");
    return t;
  }
  function cleanInvoiceNumber(text) {
    var t = String(text || "").trim();
    if (t.length < 1 || t.length > 64) throw new Error("Enter an invoice number of 1 to 64 characters.");
    return t;
  }
  var OUTCOMES = {
    pay: ["ok", "Would be paid now"], hold: ["warn", "Would be held"], escalate: ["warn", "Would be sent to the admin for review"],
    already_paid: ["bad", "This invoice number was already paid"]
  };
  function describeOutcome(o) { return OUTCOMES[o] || ["warn", "Result: " + String(o)]; }
  var api = { CONFIG: CONFIG, short: short, utf8Hex: utf8Hex, sha256Hex: sha256Hex, cleanDocHash: cleanDocHash,
              cleanInvoiceNumber: cleanInvoiceNumber, describeOutcome: describeOutcome };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.CeedePortalLib = api;
})(typeof window !== "undefined" ? window : globalThis);
