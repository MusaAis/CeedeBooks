// Run: node admin/wallet.test.js
const assert = require("assert");
const W = require("./wallet.js");
const A = "0x" + "ab".repeat(20), K = "0x" + "12".repeat(32);
const w = (h) => h.padStart(64, "0");

assert.strictEqual(W.CALLS.setVendor(A, true), "0x74604260" + w("ab".repeat(20)) + w("1"));
assert.strictEqual(W.CALLS.setVendor(A.toUpperCase().replace("0X", "0x"), false), "0x74604260" + w("ab".repeat(20)) + w("0"));
assert.strictEqual(W.CALLS.setCategoryDailyLimit(2, 10000000n), "0x333b3ffa" + w("2") + w((10000000).toString(16)));
assert.strictEqual(W.CALLS.setPaused(true), "0x16c38b3c" + w("1"));
assert.strictEqual(W.CALLS.approveEscalation(K), "0xf0b70f2b" + "12".repeat(32));
assert.strictEqual(W.CALLS.rejectEscalation(K), "0x7163a16e" + "12".repeat(32));
assert.strictEqual(W.CALLS.acceptApprover(), "0x1d4f222c");

// Funding: a USDC token transfer to the business's contract (a plain wallet Send to a contract with no receive function reverts)
{
  const fs = require("fs"), path = require("path");
  const artifact = path.join(__dirname, "..", "out", "MockUSDC.sol", "MockUSDC.json");
  assert.ok(fs.existsSync(artifact), "run forge build first: the selector is checked against the compiled token");
  assert.strictEqual(W.SEL.erc20Transfer, JSON.parse(fs.readFileSync(artifact, "utf8")).methodIdentifiers["transfer(address,uint256)"]);
  assert.strictEqual(W.CONFIG.usdc, "0x3600000000000000000000000000000000000000");
  assert.strictEqual(W.CALLS.usdcTransfer(A, 12500000n), "0xa9059cbb" + w("ab".repeat(20)) + w((12500000).toString(16)));
  assert.throws(() => W.CALLS.usdcTransfer(A, 0n));
  assert.throws(() => W.CALLS.usdcTransfer("0x123", 1n));
  assert.throws(() => W.CALLS.usdcTransfer(A, -1n));
}

assert.throws(() => W.CALLS.setVendor("0x123", true));                 // bad address
assert.throws(() => W.CALLS.setCategoryDailyLimit(256, 1n));           // uint8 overflow
assert.throws(() => W.CALLS.setCategoryDailyLimit(1, -1n));            // negative
assert.throws(() => W.CALLS.approveEscalation("0x12"));                // bad key

assert.strictEqual(W.usdcToUnits("12.5"), 12500000n);
assert.strictEqual(W.usdcToUnits(" 0.000001 "), 1n);
assert.strictEqual(W.usdcToUnits("7"), 7000000n);
for (const bad of ["", "-1", "1.1234567", "abc", "1e3", "1,5", "0x10"]) assert.throws(() => W.usdcToUnits(bad), bad);

assert.strictEqual(W.utf8Hex("Hi \u00e9"), "0x486920c3a9");
assert.strictEqual(W.short("0x" + "ab".repeat(32)), "0xababab...ababab");
console.log("WALLET_TESTS_OK");
