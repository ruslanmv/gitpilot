/**
 * Provider-aware repository API.
 *
 * A repository object carries an explicit `provider` ("github" | "gitlab") and a
 * stable `id`. GitHub repositories are addressed by owner/name; GitLab projects
 * by their numeric project id, which works for any depth of nested groups and
 * survives renames. Every component builds repository URLs through this module
 * so the provider decision is made in one place.
 *
 * The functions that only build URLs or payloads are pure (and unit-tested
 * with `node --test`); the request helpers use authFetch, which sends the
 * GitPilot session — never a GitHub or GitLab token.
 */
import { authFetch } from "./api.js";

export const PROVIDERS = ["github", "gitlab"];

/** "github" unless the repository says "gitlab" (older objects have no provider). */
export function repoProvider(repo) {
  return repo && repo.provider === "gitlab" ? "gitlab" : "github";
}

export function isGitLab(repo) {
  return repoProvider(repo) === "gitlab";
}

export function providerLabel(repoOrProvider) {
  const p = typeof repoOrProvider === "string" ? repoOrProvider : repoProvider(repoOrProvider);
  return p === "gitlab" ? "GitLab" : "GitHub";
}

/** "merge request" on GitLab, "pull request" on GitHub. */
export function changeRequestLabel(repo, { short = false } = {}) {
  if (isGitLab(repo)) return short ? "MR" : "merge request";
  return short ? "PR" : "pull request";
}

/** Full path ("group/subgroup/project" or "owner/repo"). */
export function repoFullName(repo) {
  if (!repo) return "";
  return repo.full_name || (repo.owner && repo.name ? `${repo.owner}/${repo.name}` : "");
}

/** Split a full path into the namespace (may contain "/") and the final segment. */
export function splitFullName(fullName) {
  const clean = String(fullName || "").replace(/^\/+|\/+$/g, "");
  const i = clean.lastIndexOf("/");
  if (i <= 0) return { owner: "", name: clean };
  return { owner: clean.slice(0, i), name: clean.slice(i + 1) };
}

/**
 * Stable key for a repository in the workspace context. GitHub keeps its
 * historic "owner/repo" key; GitLab is keyed by project id so a rename does not
 * orphan the entry, and the provider prefix keeps "acme/app" on both apart.
 */
export function repoKey(repo) {
  if (!repo) return null;
  const fullName = repoFullName(repo);
  if (!fullName) return null;
  return isGitLab(repo) ? `gitlab:${repo.id}:${fullName}` : fullName;
}

/** Fields the agent routes (plan / execute / message) need to find the repository. */
export function repoIdentity(repo) {
  const { owner, name } = splitFullName(repoFullName(repo));
  const identity = { provider: repoProvider(repo), repo_owner: owner, repo_name: name };
  if (repo && repo.id !== undefined && repo.id !== null) identity.repo_id = String(repo.id);
  return identity;
}

function base(repo) {
  if (isGitLab(repo)) {
    if (repo.id === undefined || repo.id === null || repo.id === "") {
      throw new Error("A GitLab project needs its numeric id.");
    }
    return `/api/gitlab/projects/${encodeURIComponent(repo.id)}`;
  }
  const { owner, name } = splitFullName(repoFullName(repo));
  return `/api/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`;
}

function qs(params) {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

export function treeUrl(repo, ref) {
  return `${base(repo)}/tree${qs({ ref: ref || undefined })}`;
}

export function fileUrl(repo, path, ref) {
  return `${base(repo)}/file${qs({ path, ref: ref || "HEAD" })}`;
}

export function branchesUrl(repo, { query, page, perPage = 100 } = {}) {
  return `${base(repo)}/branches${qs({ query, page, per_page: perPage })}`;
}

export function commitsUrl(repo, { ref, path, page } = {}) {
  return `${base(repo)}/commits${qs({ ref, path, page })}`;
}

export function compareUrl(repo, baseRef, headRef) {
  return `${base(repo)}/compare${qs({ base: baseRef, head: headRef })}`;
}

/** URL + JSON body that open a pull request (GitHub) or merge request (GitLab). */
export function changeRequestRequest(repo, { title, head, base: target, body, draft = false }) {
  if (isGitLab(repo)) {
    return {
      url: `${base(repo)}/merge_requests`,
      body: { title, source_branch: head, target_branch: target, description: body, draft },
    };
  }
  return { url: `${base(repo)}/pulls`, body: { title, head, base: target, body, draft } };
}

/** URL + JSON body that create or update one file on a branch. */
export function writeFileRequest(repo, { path, content, message, branch }) {
  return { url: `${base(repo)}/file`, body: { path, content, message, branch } };
}

export function deleteFileUrl(repo, { path, branch, message }) {
  return `${base(repo)}/file${qs({ path, branch, message })}`;
}

/** Where the user can see the repository (or a branch of it) on its provider. */
export function webUrl(repo, branch) {
  const root = repo && repo.html_url
    ? repo.html_url.replace(/\/+$/, "")
    : isGitLab(repo)
      ? `https://${(repo && repo.host) || "gitlab.com"}/${repoFullName(repo)}`
      : `https://github.com/${repoFullName(repo)}`;
  if (!branch) return root;
  return isGitLab(repo) ? `${root}/-/tree/${branch}` : `${root}/tree/${branch}`;
}

// ─── requests ──────────────────────────────────────────────────────────────

async function json(res) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || data.message || data.error || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export async function getTree(repo, ref) {
  return json(await authFetch(treeUrl(repo, ref)));
}

export async function getFile(repo, path, ref) {
  return json(await authFetch(fileUrl(repo, path, ref)));
}

export async function writeFile(repo, change) {
  const { url, body } = writeFileRequest(repo, change);
  return json(await authFetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }));
}

export async function deleteFile(repo, change) {
  return json(await authFetch(deleteFileUrl(repo, change), { method: "DELETE" }));
}

export async function createBranch(repo, name, ref = "HEAD") {
  return json(await authFetch(`${base(repo)}/branches`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, ref }),
  }));
}

export async function createChangeRequest(repo, change) {
  const { url, body } = changeRequestRequest(repo, change);
  return json(await authFetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }));
}

/**
 * Whether the signed-in user may push to the repository.
 * @returns {Promise<{canWrite:boolean, appInstalled:?boolean, authType:string}>}
 */
export async function getRepoAccess(repo) {
  if (isGitLab(repo)) {
    const project = await json(await authFetch(base(repo)));
    const perms = project.permissions || {};
    return { canWrite: !!perms.push, appInstalled: null, authType: "gitlab_oauth" };
  }
  const { owner, name } = splitFullName(repoFullName(repo));
  const data = await json(await authFetch(`/api/auth/repo-access${qs({ owner, repo: name })}`));
  return { canWrite: !!data.can_write, appInstalled: !!data.app_installed, authType: data.auth_type };
}
