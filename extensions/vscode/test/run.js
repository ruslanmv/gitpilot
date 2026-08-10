#!/usr/bin/env node
/**
 * Test runner for the settings pages.
 *
 * Two suites, run in separate processes so a crash in one still reports the
 * other, and so the `vscode` module stub in the host suite cannot leak into
 * the webview suite's module cache.
 *
 * Both suites need `npm run compile` first — they load the compiled panel and
 * the copied template from out/, which is what actually ships.
 */
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const HERE = __dirname;
const EXT_ROOT = path.resolve(HERE, "..");

const SUITES = [
  ["Connection procedure", "connection.test.js"],
  ["Diagnostics", "diagnostics.test.js"],
  ["Error translation", "errorTranslator.test.js"],
  ["LLM request deadlines", "llmTimeout.test.js"],
  ["Navigation sidebar", "navView.test.js"],
  ["Landing page", "landing.test.js"],
  ["Session commands", "chatSession.test.js"],
  ["Chat panel presentation", "chatPanel.test.js"],
  ["Composer and transcript", "composer.test.js"],
  ["Command wiring", "commandWiring.test.js"],
  ["Settings webview", "settingsWebview.test.js"],
  ["Settings panel host", "settingsPanel.test.js"],
];

// The suites exercise what ships, so they need a build. Failing here with a
// clear instruction beats a stack trace about a missing module.
const compiled = path.join(EXT_ROOT, "out", "ui", "webview", "GitPilotSettingsPanel.js");
if (!fs.existsSync(compiled)) {
  console.error("✗ Not compiled yet. Run `npm run compile` first.");
  process.exit(1);
}

let failed = 0;
for (const [label, file] of SUITES) {
  console.log(`\n▸ ${label}`);
  const result = spawnSync(process.execPath, [path.join(HERE, file)], {
    stdio: "inherit",
    cwd: EXT_ROOT,
  });
  if (result.status !== 0) {
    failed++;
  }
}

console.log("");
if (failed) {
  console.error(`✗ ${failed} of ${SUITES.length} suite(s) failed.`);
  process.exit(1);
}
console.log(`✓ All ${SUITES.length} suites passed.`);
