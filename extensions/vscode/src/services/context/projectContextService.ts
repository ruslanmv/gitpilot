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
  // Everything below was missing, and the tree budget is small enough that
  // a few hundred cache entries are the difference between the model seeing
  // your source and describing your tooling. `.pytest_cache` and
  // `.ruff_cache` alone took 14 of 300 slots in the repository this was
  // diagnosed on.
  "__pycache__",
  ".pytest_cache",
  ".ruff_cache",
  ".mypy_cache",
  ".tox",
  ".eggs",
  ".gradle",
  ".idea",
  ".svelte-kit",
  ".parcel-cache",
  ".terraform",
  "out",
  "target",
  "vendor",
  "htmlcov",
  "site-packages",
  "env",
  ".env",
]);

/** Depth-1 directories are always worth showing; deep ones compete. */
const ROOT_TREE_RESERVE = 0.4;

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

  /**
   * A breadth-first sample of the tree, capped at `treeLimit`.
   *
   * Breadth-first is the whole point. The previous walk recursed into each
   * directory the moment it found one and stopped dead at the limit, so the
   * budget went to whichever directory `readdir` happened to return first.
   * Measured on the repository this was diagnosed against: `extensions/`
   * took 171 of 300 entries and `docs/` another 65, which left no room for
   * the Python package that *is* the application — `pyproject.toml` and
   * every file under `gitpilot/` were absent from the context entirely.
   *
   * A model handed that listing does not know it is looking at a fraction
   * of a repository. It answers confidently about what it was shown, which
   * is how "explain this project's architecture" came back describing a
   * dependency instead of the project.
   *
   * Level by level, the root is always represented, and truncation costs
   * depth rather than whole top-level subtrees.
   */
  private scanTree(root: string): ProjectTreeEntry[] {
    const results: ProjectTreeEntry[] = [];
    const seen = new Set<string>();
    let frontier: string[] = [root];

    const push = (relative: string, type: "dir" | "file"): boolean => {
      if (!relative || seen.has(relative)) return true;
      seen.add(relative);
      results.push({ path: relative, type });
      return results.length < this.treeLimit;
    };

    while (frontier.length > 0 && results.length < this.treeLimit) {
      const nextFrontier: string[] = [];

      for (const current of frontier) {
        let entries: fs.Dirent[];
        try {
          entries = fs.readdirSync(current, { withFileTypes: true });
        } catch {
          // Unreadable directory (permissions, a broken symlink, a mount
          // that went away). One bad directory must not cost the scan.
          continue;
        }

        // Files before directories at each level: a manifest or README is
        // worth more to a reader than one more folder name, and `keyFiles`
        // and `readmePreview` are both derived from this list.
        const dirs = entries.filter((e) => e.isDirectory());
        const files = entries.filter((e) => e.isFile());

        for (const entry of [...files, ...dirs]) {
          if (DEFAULT_IGNORES.has(entry.name)) continue;
          const absolute = path.join(current, entry.name);
          const relative = path.relative(root, absolute).replace(/\\/g, "/");
          if (!relative) continue;

          if (entry.isDirectory()) {
            if (!push(relative, "dir")) return results;
            nextFrontier.push(absolute);
          } else if (!push(relative, "file")) {
            return results;
          }
        }
      }

      frontier = nextFrontier;
    }

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
