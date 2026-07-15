#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");

const outDir = path.resolve(__dirname, "..", "out");
let removed = 0;

function walk(dir) {
  if (!fs.existsSync(dir)) {
    return;
  }

  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const absolute = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(absolute);
    } else if (entry.isFile() && entry.name.endsWith(".map")) {
      fs.rmSync(absolute, { force: true });
      removed += 1;
    }
  }
}

walk(outDir);
console.log(`Removed ${removed} source map file(s) from ${path.relative(process.cwd(), outDir) || outDir}.`);
