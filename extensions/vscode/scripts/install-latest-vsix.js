#!/usr/bin/env node
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const vsix = fs
  .readdirSync(root)
  .filter((name) => name.endsWith(".vsix"))
  .map((name) => ({ name, mtime: fs.statSync(path.join(root, name)).mtimeMs }))
  .sort((a, b) => b.mtime - a.mtime)[0];

if (!vsix) {
  console.error("No .vsix file found. Run `npm run package` first.");
  process.exit(1);
}

const vsixPath = path.join(root, vsix.name);
console.log(`Installing ${vsixPath} ...`);

const result = spawnSync("code", ["--install-extension", vsixPath, "--force"], {
  cwd: root,
  stdio: "inherit",
  shell: process.platform === "win32",
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

process.exit(result.status ?? 0);
