/* CeedeBooks vendor portal: configuration and pure helpers. No dependencies; testable in Node. */
(function (root) {
  "use strict";
  var CONFIG = {
    api: "https://api.ceedebooks.xyz",
    proof: "https://ceedebooks.xyz",
    explorer: "https://explorer.testnet.arc.io",
    admin: "https://admin.ceedebooks.xyz",
    portal: "https://portal.ceedebooks.xyz",
    // Business registration (v1.2.8.2): the owner sends one transaction to the factory from their own wallet.
    factory: "0x97b9A3802bA6B258cBeF6070532a265656bb391C",
    chainIdHex: "0x4cef52", // Arc Testnet, 5042002
    chainName: "Arc Testnet",
    rpc: "https://rpc.testnet.arc.network",
    nativeCurrency: { name: "USDC", symbol: "USDC", decimals: 18 }
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
  // "12.5" -> 12500000n (USDC has 6 decimals). Rejects anything that is not a plain decimal.
  function usdcToUnits(text) {
    var m = /^(\d{1,12})(?:\.(\d{1,6}))?$/.exec(String(text).trim());
    if (!m) throw new Error("Enter an amount such as 12.5 (at most 6 decimals).");
    return BigInt(m[1]) * 1000000n + BigInt((m[2] || "").padEnd(6, "0"));
  }
  // The contract requires 0 < perTx <= daily <= weekly. Say so here, before the owner pays a fee for a revert.
  function validateLimits(daily, perTx, weekly) {
    var d = usdcToUnits(daily), p = usdcToUnits(perTx), w = usdcToUnits(weekly);
    if (p <= 0n) throw new Error("The limit per payment must be more than zero.");
    if (p > d) throw new Error("The limit per payment cannot be more than the daily limit.");
    if (d > w) throw new Error("The daily limit cannot be more than the weekly limit.");
    return { daily: d, perTx: p, weekly: w };
  }
  function address(a) {
    if (!/^0x[0-9a-fA-F]{40}$/.test(a)) throw new Error("That is not a valid wallet address.");
    return a.slice(2).toLowerCase().padStart(64, "0");
  }
  function uint(n) {
    var v = BigInt(n);
    if (v < 0n || v >= (1n << 256n)) throw new Error("Number out of range.");
    return v.toString(16).padStart(64, "0");
  }
  var SEL_CREATE_BUSINESS = "774b745b"; // createBusiness(address,uint256,uint256,uint256); checked against the compiled factory in portal.test.js
  function createBusinessData(agent, daily, perTx, weekly) {
    return "0x" + SEL_CREATE_BUSINESS + address(agent) + uint(daily) + uint(perTx) + uint(weekly);
  }
  // The name in the URL: lower case letters, digits and hyphens, 2 to 64 characters.
  function cleanSlug(text) {
    var t = String(text || "").trim().toLowerCase().replace(/[\s_]+/g, "-").replace(/[^a-z0-9-]/g, "").replace(/-+/g, "-").replace(/^-+|-+$/g, "");
    if (t.length < 2 || t.length > 64) throw new Error("The name in the web address needs 2 to 64 letters, digits or hyphens.");
    return t;
  }
  var api = { CONFIG: CONFIG, usdcToUnits: usdcToUnits, validateLimits: validateLimits, createBusinessData: createBusinessData,
              SEL_CREATE_BUSINESS: SEL_CREATE_BUSINESS, cleanSlug: cleanSlug, short: short, utf8Hex: utf8Hex, sha256Hex: sha256Hex, cleanDocHash: cleanDocHash,
              cleanInvoiceNumber: cleanInvoiceNumber, describeOutcome: describeOutcome };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.CeedePortalLib = api;
})(typeof window !== "undefined" ? window : globalThis);
