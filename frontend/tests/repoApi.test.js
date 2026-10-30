// frontend/tests/repoApi.test.js — provider-aware repository API.
//
// Every component builds repository URLs through utils/repoApi.js, so these
// tests pin the one place where GitHub and GitLab diverge: GitHub by
// owner/name, GitLab by numeric project id (any depth of nested groups).
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  branchesUrl, changeRequestLabel, changeRequestRequest, compareUrl, deleteFileUrl, fileUrl,
  isGitLab, providerLabel, repoFullName, repoIdentity, repoKey, repoProvider, splitFullName,
  treeUrl, webUrl, writeFileRequest,
} from "../utils/repoApi.js";

const GH = { id: 7, name: "app", owner: "acme", full_name: "acme/app", default_branch: "main" };
const GL = { id: 113, name: "app", owner: "acme/platform", full_name: "acme/platform/app",
  default_branch: "main", provider: "gitlab", html_url: "https://gitlab.example.com/acme/platform/app" };

test("repositories without a provider are GitHub (older objects keep working)", () => {
  assert.equal(repoProvider(GH), "github");
  assert.equal(repoProvider(GL), "gitlab");
  assert.equal(isGitLab(null), false);
  assert.equal(providerLabel(GL), "GitLab");
  assert.equal(providerLabel("github"), "GitHub");
});

test("GitHub URLs use owner/name; GitLab URLs use the stable project id", () => {
  assert.equal(treeUrl(GH, "dev"), "/api/repos/acme/app/tree?ref=dev");
  assert.equal(treeUrl(GL, "dev"), "/api/gitlab/projects/113/tree?ref=dev");
  assert.equal(fileUrl(GL, "src/a b.py"), "/api/gitlab/projects/113/file?path=src%2Fa%20b.py&ref=HEAD");
  assert.equal(branchesUrl(GH, { query: "feat" }), "/api/repos/acme/app/branches?query=feat&per_page=100");
  assert.equal(compareUrl(GL, "main", "feat"), "/api/gitlab/projects/113/compare?base=main&head=feat");
  assert.equal(deleteFileUrl(GL, { path: "a.py", branch: "feat", message: "rm" }),
    "/api/gitlab/projects/113/file?path=a.py&branch=feat&message=rm");
});

test("a GitLab project without an id is refused rather than guessed", () => {
  assert.throws(() => treeUrl({ provider: "gitlab", full_name: "a/b" }), /numeric id/);
});

test("nested GitLab namespaces split on the last slash", () => {
  assert.deepEqual(splitFullName("acme/platform/app"), { owner: "acme/platform", name: "app" });
  assert.deepEqual(splitFullName("acme/app"), { owner: "acme", name: "app" });
  assert.equal(repoFullName({ owner: "o", name: "r" }), "o/r");
});

test("agent requests carry provider, namespace, name and the stable id", () => {
  assert.deepEqual(repoIdentity(GL), { provider: "gitlab", repo_owner: "acme/platform", repo_name: "app", repo_id: "113" });
  assert.deepEqual(repoIdentity(GH), { provider: "github", repo_owner: "acme", repo_name: "app", repo_id: "7" });
});

test("change requests are pull requests on GitHub and merge requests on GitLab", () => {
  const change = { title: "T", head: "feat", base: "main", body: "B" };
  assert.deepEqual(changeRequestRequest(GH, change), {
    url: "/api/repos/acme/app/pulls", body: { title: "T", head: "feat", base: "main", body: "B", draft: false },
  });
  assert.deepEqual(changeRequestRequest(GL, change), {
    url: "/api/gitlab/projects/113/merge_requests",
    body: { title: "T", source_branch: "feat", target_branch: "main", description: "B", draft: false },
  });
  assert.equal(changeRequestLabel(GL), "merge request");
  assert.equal(changeRequestLabel(GH, { short: true }), "PR");
});

test("file writes always name the branch", () => {
  assert.deepEqual(writeFileRequest(GL, { path: "a", content: "x", message: "m", branch: "feat" }), {
    url: "/api/gitlab/projects/113/file", body: { path: "a", content: "x", message: "m", branch: "feat" },
  });
});

test("workspace keys keep GitHub's historic form and separate the providers", () => {
  assert.equal(repoKey(GH), "acme/app");
  assert.equal(repoKey(GL), "gitlab:113:acme/platform/app");
  assert.notEqual(repoKey({ ...GH, full_name: "acme/platform/app" }), repoKey(GL));
});

test("browser links point at the right provider and branch syntax", () => {
  assert.equal(webUrl(GH, "feat"), "https://github.com/acme/app/tree/feat");
  assert.equal(webUrl(GL, "feat"), "https://gitlab.example.com/acme/platform/app/-/tree/feat");
});
