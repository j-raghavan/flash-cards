// SPDX-License-Identifier: AGPL-3.0-only
// Prints a Markdown summary of the last run (from the JSON reporter) for the CI
// job summary, naming every test that only passed on retry.
import { readFileSync } from "node:fs";

let report;
try {
  report = JSON.parse(readFileSync(new URL("../.run/results.json", import.meta.url), "utf8"));
} catch {
  console.log("### End-to-end tests\n\nNo results: the suite did not run to the end.");
  process.exit(0);
}

const flaky = [];
const failed = [];
function walk(suite, path) {
  for (const spec of suite.specs ?? []) {
    for (const test of spec.tests ?? []) {
      const name = [...path, spec.title].filter(Boolean).join(" › ") + ` [${test.projectName}]`;
      if (test.status === "flaky") flaky.push(name);
      if (test.status === "unexpected") failed.push(name);
    }
  }
  for (const child of suite.suites ?? []) walk(child, [...path, child.title]);
}
for (const suite of report.suites ?? []) walk(suite, [suite.title]);

const { expected = 0, unexpected = 0, flaky: flakyCount = 0, skipped = 0, duration = 0 } = report.stats ?? {};
const lines = [
  "### End-to-end tests",
  "",
  `Passed: ${expected}, failed: ${unexpected}, flaky: ${flakyCount}, skipped: ${skipped}, in ${(duration / 1000).toFixed(1)} s.`,
];
if (flaky.length > 0) {
  lines.push("", "**Flaky (passed only on retry):**", ...flaky.map((name) => `- ${name}`));
}
if (failed.length > 0) {
  lines.push("", "**Failed:**", ...failed.map((name) => `- ${name}`));
}
console.log(lines.join("\n"));
