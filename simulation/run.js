#!/usr/bin/env node
/**
 * Simulation runner for WalletBlocklistConsensus.
 *
 * Runs all scenarios from scenarios.js against the in-memory BlocklistSim.
 * No network, no algosdk, no compilation required.
 *
 * Usage:
 *   node simulation/run.js
 *   node simulation/run.js --verbose      # print each step
 *   node simulation/run.js --scenario 3   # run a single scenario by index
 */

import { SCENARIOS } from "./scenarios.js";

// ---------------------------------------------------------------------------
// CLI flags
// ---------------------------------------------------------------------------

const args = process.argv.slice(2);
const verbose = args.includes("--verbose") || args.includes("-v");
const onlyIdx = (() => {
  const i = args.indexOf("--scenario");
  if (i !== -1 && args[i + 1] !== undefined) return parseInt(args[i + 1], 10) - 1;
  return null;
})();

// ---------------------------------------------------------------------------
// Runner
// ---------------------------------------------------------------------------

const scenarios = onlyIdx !== null
  ? [SCENARIOS[onlyIdx]].filter(Boolean)
  : SCENARIOS;

if (scenarios.length === 0) {
  console.error(`No scenario at index ${onlyIdx + 1}. There are ${SCENARIOS.length} scenarios.`);
  process.exit(1);
}

const LINE = "-".repeat(60);
let passed = 0;
let failed = 0;

console.log("\nWalletBlocklistConsensus — Simulation Tests");
console.log(`${"=".repeat(60)}\n`);

for (let i = 0; i < scenarios.length; i++) {
  const scenario = scenarios[i];
  const label = `[${i + 1}/${scenarios.length}] ${scenario.name}`;
  let result;

  try {
    result = scenario.run();
  } catch (err) {
    result = { passed: false, steps: [], detail: `Uncaught: ${err.message}` };
  }

  if (result.passed) {
    console.log(`  PASS  ${label}`);
    passed++;
    if (verbose && result.steps.length) {
      result.steps.forEach(s => console.log(`          ${s}`));
    }
  } else {
    console.log(`  FAIL  ${label}`);
    if (result.detail) console.log(`          Reason: ${result.detail}`);
    result.steps.forEach(s => console.log(`          ${s}`));
    failed++;
  }
}

console.log(`\n${LINE}`);
console.log(`Results: ${passed} passed, ${failed} failed, ${scenarios.length} total`);
console.log(LINE);

if (failed > 0) {
  console.log("\nSome scenarios failed. Review output above.\n");
  process.exit(1);
} else {
  console.log("\nAll scenarios passed.\n");
}
