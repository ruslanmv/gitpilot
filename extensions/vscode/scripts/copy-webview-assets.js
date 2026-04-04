const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const filesToCopy = [
  {
    from: path.join(root, "src", "ui", "webview", "gitpilotWorkspaceTemplate.html"),
    to: path.join(root, "out", "ui", "webview", "gitpilotWorkspaceTemplate.html"),
  },
  {
    from: path.join(root, "src", "ui", "webview", "gitpilotWorkspace.css"),
    to: path.join(root, "out", "ui", "webview", "gitpilotWorkspace.css"),
  },
];

for (const file of filesToCopy) {
  fs.mkdirSync(path.dirname(file.to), { recursive: true });

  if (!fs.existsSync(file.from)) {
    throw new Error(`Source file not found: ${file.from}`);
  }

  fs.copyFileSync(file.from, file.to);
  console.log(`Copied ${path.relative(root, file.from)} -> ${path.relative(root, file.to)}`);
}