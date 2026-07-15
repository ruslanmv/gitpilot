#!/usr/bin/env node
/*
 * Lightweight local lint gate for the VS Code extension.
 *
 * This intentionally avoids a global eslint dependency so `npm run lint` works
 * on Windows after `npm install` with the dependencies already in package-lock.
 * It performs the highest-signal local checks that are always available:
 * TypeScript no-emit validation and a few repository hygiene checks.
 */
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const srcRoot = path.join(root, "src");
const isWindows = process.platform === "win32";
const tscBin = path.join(
  root,
  "node_modules",
  ".bin",
  isWindows ? "tsc.cmd" : "tsc"
);

function walk(dir, files = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === "out") {
      continue;
    }

    const absolute = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(absolute, files);
    } else if (entry.isFile() && absolute.endsWith(".ts")) {
      files.push(absolute);
    }
  }
  return files;
}

function fail(message) {
  console.error(`[lint] ${message}`);
  process.exitCode = 1;
}

if (!fs.existsSync(tscBin)) {
  fail("TypeScript is not installed. Run `npm install` in extensions/vscode first.");
} else {
  const result = spawnSync(tscBin, ["--noEmit", "-p", root], {
    cwd: root,
    stdio: "inherit",
    shell: false,
  });
  if (result.status !== 0) {
    fail("TypeScript no-emit validation failed.");
  }
}

for (const file of walk(srcRoot)) {
  const text = fs.readFileSync(file, "utf8");
  const relative = path.relative(root, file).replace(/\\/g, "/");

  if (/\r\n/.test(text)) {
    fail(`${relative}: use LF line endings.`);
  }

  if (/[ \t]+$/m.test(text)) {
    fail(`${relative}: remove trailing whitespace.`);
  }

  if (!text.endsWith("\n")) {
    fail(`${relative}: add a trailing newline.`);
  }

  if (/try\s*\{\s*(?:import\s|(?:const|let|var)\s+[^=]+\s*=\s*require\()/m.test(text)) {
    fail(`${relative}: do not wrap imports in try/catch blocks.`);
  }
}

if (process.exitCode) {
  process.exit(process.exitCode);
}

console.log("[lint] TypeScript and repository hygiene checks passed.");
