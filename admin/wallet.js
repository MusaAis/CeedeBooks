/* CeedeBooks admin: configuration and contract call encoding. No dependencies; testable in Node.
   Every call the admin can send is a fixed selector plus 32-byte words, built here and nowhere else. */
(function (root) {
  "use strict";

  var CONFIG = {
    api: "https://api.ceedebooks.xyz",
    enforcer: "0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB",
    chainIdHex: "0x4cef52", // Arc Testnet, 5042002
    chainName: "Arc Testnet",
    rpc: "https://rpc.testnet.arc.network",
    explorer: "https://explorer.testnet.arc.io",
    nativeCurrency: { name: "USDC", symbol: "USDC", decimals: 18 }
  };

  // First 4 bytes of keccak256(signature), checked against the contract ABI in admin/wallet.test.js.
  var SEL = {
    setVendor: "74604260",            // setVendor(address,bool)
    setCategoryDailyLimit: "333b3ffa", // setCategoryDailyLimit(uint8,uint256)
    setPaused: "16c38b3c",            // setPaused(bool)
    approveEscalation: "f0b70f2b",    // approveEscalation(bytes32)
    rejectEscalation: "7163a16e",     // rejectEscalation(bytes32)
    acceptApprover: "1d4f222c"        // acceptApprover()
  };

  function address(a) {
    if (!/^0x[0-9a-fA-F]{40}$/.test(a)) throw new Error("That is not a valid wallet address.");
    return a.slice(2).toLowerCase().padStart(64, "0");
  }
  function bool(b) { return (b ? "1" : "0").padStart(64, "0"); }
  function uint(n, bits) {
    var v = BigInt(n);
    if (v < 0n || v >= (1n << BigInt(bits))) throw new Error("Number out of range.");
    return v.toString(16).padStart(64, "0");
  }
  function bytes32(h) {
    if (!/^0x[0-9a-fA-F]{64}$/.test(h)) throw new Error("That is not a valid 32-byte key.");
    return h.slice(2).toLowerCase();
  }

  // "12.5" -> 12500000n (USDC has 6 decimals). Rejects anything that is not a plain decimal.
  function usdcToUnits(text) {
    var m = /^(\d{1,12})(?:\.(\d{1,6}))?$/.exec(String(text).trim());
    if (!m) throw new Error("Enter an amount such as 12.5 (at most 6 decimals).");
    return BigInt(m[1]) * 1000000n + BigInt((m[2] || "").padEnd(6, "0"));
  }

  var CALLS = {
    setVendor: function (vendor, approved) { return "0x" + SEL.setVendor + address(vendor) + bool(approved); },
    setCategoryDailyLimit: function (category, units) { return "0x" + SEL.setCategoryDailyLimit + uint(category, 8) + uint(units, 256); },
    setPaused: function (paused) { return "0x" + SEL.setPaused + bool(paused); },
    approveEscalation: function (key) { return "0x" + SEL.approveEscalation + bytes32(key); },
    rejectEscalation: function (key) { return "0x" + SEL.rejectEscalation + bytes32(key); },
    acceptApprover: function () { return "0x" + SEL.acceptApprover; }
  };

  function short(h) { return h && h.length > 14 ? h.slice(0, 8) + "..." + h.slice(-6) : (h || ""); }
  function utf8Hex(text) {
    return "0x" + Array.prototype.map.call(new TextEncoder().encode(text), function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }

  var api = { CONFIG: CONFIG, SEL: SEL, CALLS: CALLS, usdcToUnits: usdcToUnits, short: short, utf8Hex: utf8Hex };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.CeedeWallet = api;
})(typeof window !== "undefined" ? window : globalThis);
