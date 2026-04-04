import * as fs from "fs";
import * as path from "path";

export type ProjectTreeEntry = { path: string; type: "file" | "dir" };

export type ProjectContext = {
  mode: "folder" | "local_git" | "github";
  workspaceRoot?: string;
  repoRoot?: string;
  repoName?: string;
  branch?: string;
  languages: string[];
  manifests: string[];
  keyFiles: string[];
  recentFiles: string[];
  readmePreview?: string;
  treeSummary: ProjectTreeEntry[];
  indexedAt: string;
};

const DEFAULT_IGNORES = new Set([
  ".git",
  "node_modules",
  "dist",
  "build",
  ".next",
  ".venv",
  "venv",
  "coverage",
  ".turbo",
  ".cache",
]);

const MANIFESTS = new Set([
  "package.json",
  "package-lock.json",
  "pnpm-lock.yaml",
  "yarn.lock",
  "pyproject.toml",
  "poetry.lock",
  "requirements.txt",
  "Pipfile",
  "Cargo.toml",
  "go.mod",
  "pom.xml",
  "build.gradle",
  "Dockerfile",
  "docker-compose.yml",
  "Makefile",
  "README.md",
]);

const LANGUAGE_BY_EXTENSION: Record<string, string> = {
  ts: "TypeScript",
  tsx: "TypeScript",
  js: "JavaScript",
  jsx: "JavaScript",
  py: "Python",
  md: "Markdown",
  json: "JSON",
  yml: "YAML",
  yaml: "YAML",
  html: "HTML",
  css: "CSS",
  scss: "SCSS",
  java: "Java",
  go: "Go",
  rs: "Rust",
  cpp: "C++",
  c: "C",
  cs: "C#",
  php: "PHP",
  rb: "Ruby",
  sh: "Shell",
};

export class ProjectContextService {
  constructor(
    private readonly treeLimit = 300,
    private readonly readmePreviewMaxChars = 4000,
  ) {}

  build(input: {
    workspaceRoot?: string;
    repoRoot?: string;
    repoName?: string;
    branch?: string;
    mode?: "folder" | "local_git" | "github";
  }): ProjectContext | undefined {
    const root = input.repoRoot || input.workspaceRoot;
    if (!root || !fs.existsSync(root)) return undefined;

    const treeSummary = this.scanTree(root);
    const manifests = treeSummary
      .filter((entry) => entry.type === "file" && MANIFESTS.has(path.basename(entry.path)))
      .map((entry) => entry.path)
      .slice(0, 20);

    const readmeEntry = treeSummary.find((entry) => entry.type === "file" && /^readme\.(md|txt|rst)$/i.test(path.basename(entry.path)));
    const readmePreview = readmeEntry ? this.readTextSafe(path.join(root, readmeEntry.path), this.readmePreviewMaxChars) : undefined;

    const keyFiles = treeSummary
      .filter((entry) => entry.type === "file")
      .map((entry) => entry.path)
      .filter((p) => {
        const base = path.basename(p).toLowerCase();
        return (
          base === "readme.md" ||
          base === "pyproject.toml" ||
          base === "package.json" ||
          base === "vite.config.js" ||
          base === "vite.config.ts" ||
          base === "tsconfig.json" ||
          base === "dockerfile" ||
          base === "docker-compose.yml" ||
          /app\.(tsx?|jsx?)$/i.test(base) ||
          /main\.(tsx?|jsx?|py)$/i.test(base) ||
          /index\.(tsx?|jsx?|py|html)$/i.test(base)
        );
      })
      .slice(0, 30);

    const languageSet = new Set<string>();
    const recentFiles = treeSummary
      .filter((entry) => entry.type === "file")
      .map((entry) => ({
        path: entry.path,
        absolute: path.join(root, entry.path),
        mtimeMs: this.safeStatMtime(path.join(root, entry.path)),
      }))
      .sort((a, b) => b.mtimeMs - a.mtimeMs)
      .slice(0, 12)
      .map((entry) => entry.path);

    for (const entry of treeSummary) {
      if (entry.type !== "file") continue;
      const ext = path.extname(entry.path).replace(/^\./, "").trim().toLowerCase();
      const language = LANGUAGE_BY_EXTENSION[ext];
      if (language) languageSet.add(language);
    }

    return {
      mode: input.mode || (input.repoRoot ? "local_git" : "folder"),
      workspaceRoot: input.workspaceRoot,
      repoRoot: input.repoRoot,
      repoName: input.repoName,
      branch: input.branch,
      languages: Array.from(languageSet).sort().slice(0, 20),
      manifests,
      keyFiles,
      recentFiles,
      readmePreview,
      treeSummary,
      indexedAt: new Date().toISOString(),
    };
  }

  private scanTree(root: string): ProjectTreeEntry[] {
    const results: ProjectTreeEntry[] = [];
    const walk = (current: string) => {
      if (results.length >= this.treeLimit) return;
      const entries = fs.readdirSync(current, { withFileTypes: true });
      for (const entry of entries) {
        if (results.length >= this.treeLimit) return;
        if (DEFAULT_IGNORES.has(entry.name)) continue;
        const absolute = path.join(current, entry.name);
        const relative = path.relative(root, absolute).replace(/\\/g, "/");
        if (!relative) continue;
        if (entry.isDirectory()) {
          results.push({ path: relative, type: "dir" });
          walk(absolute);
        } else if (entry.isFile()) {
          results.push({ path: relative, type: "file" });
        }
      }
    };
    walk(root);
    return results;
  }

  private readTextSafe(filePath: string, maxChars: number): string | undefined {
    try {
      const raw = fs.readFileSync(filePath, "utf8");
      return raw.length > maxChars ? `${raw.slice(0, maxChars)}\n...[truncated]` : raw;
    } catch {
      return undefined;
    }
  }

  private safeStatMtime(filePath: string): number {
    try {
      return fs.statSync(filePath).mtimeMs;
    } catch {
      return 0;
    }
  }
}
