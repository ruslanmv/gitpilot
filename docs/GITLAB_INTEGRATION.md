# GitLab integration

GitPilot works with GitLab the same way it works with GitHub:
- sign in with GitLab;
- browse the projects you can access, including private projects and projects in nested groups;
- pick a project and a branch, and explore its files;
- ask the agent for a change and review the plan;
- approve it, so GitPilot commits to a working branch and opens a **merge request**;
- reopen the session later.

GitHub and GitLab can be connected at the same time, and one workspace can mix
repositories from both.

## Set up

### GitLab.com

1. In GitLab, open **Preferences → Applications**. For a group-owned application, use the group's **Settings → Applications**. Create an application:
   - **Redirect URI**: your frontend's sign-in page, e.g. `https://gitpilot.example.com/auth`. For local development, use `http://localhost:5173/auth`.
   - **Confidential**: yes. The secret stays on the GitPilot server.
   - **Scopes**: `api` and `read_user`.
     - `api` is needed to create branches, commits, merge requests and issues.
     - `read_user` is needed to show who is signed in.
2. Configure the backend. Do not put these variables in the frontend; they are server-side only.

   ```bash
   GITLAB_CLIENT_ID=<application id>
   GITLAB_CLIENT_SECRET=<application secret>
   GITLAB_REDIRECT_URI=https://gitpilot.example.com/auth   # or set GITPILOT_PUBLIC_BASE_URL
   GITPILOT_TOKEN_ENCRYPTION_KEY=<fernet key>              # see docs/auth.md
   ```

3. Restart the backend. **Continue with GitLab** becomes active on `/auth`.
   `GET /api/auth/providers` reports `"gitlab": {"web": true}`.

### Self-managed GitLab

Set `GITLAB_BASE_URL` to the instance root, e.g. `https://gitlab.example.com`, and
register the application on that instance. Every OAuth, API and `git` request then
goes to that host.

- The host comes from the server configuration only. It is never taken from a
  request, so a client cannot redirect the server's credentials to another host.
- The URL must be `https://` with no credentials, query or fragment. Plain `http://`
  is accepted only for `localhost`, for local testing.
- One GitLab instance per GitPilot server.

### Single-user installs

Without OAuth, the backend can use one personal access token from the environment:
`GITPILOT_GITLAB_TOKEN` (or `GITLAB_TOKEN`). This mirrors `GITPILOT_GITHUB_TOKEN`.
Use it only on a machine where everyone who reaches GitPilot may use that token.

## What works

| Capability | GitHub | GitLab |
|---|---|---|
| OAuth sign-in, server-side encrypted token, refresh, disconnect | ✓ | ✓ (tokens refresh automatically every ~2 h) |
| Repository discovery and search | ✓ | ✓ membership projects, nested groups, private projects; `GET /api/gitlab/groups` |
| Branches: list, create, switch | ✓ | ✓ |
| Tree, file read | ✓ | ✓ |
| File create / update / delete (one commit each, on a named branch) | ✓ | ✓ (Commits API) |
| Commit history, compare / diff | ✓ | ✓ |
| Issues: list, read, create, update, comment, labels, assignees | ✓ | ✓ (`number` = issue iid) |
| Pull / merge requests: list, read, create, update, merge, files, review, comment | PRs | MRs (`number` = MR iid; approve, request changes as a note, comment) |
| AI chat, plan, execute, auto-PR / auto-MR | ✓ | ✓ |
| v4 agent loop tools | `github.*` | `gitlab.issue.*`, `gitlab.mr.*`, `gitlab.search.*` |
| Persistent sessions, multi-repository workspaces | ✓ | ✓ |
| Local clone for the streaming engine | ✓ | ✓ |

Differences that come from GitLab itself:
- Code search works inside the selected project. GitLab's basic search has no
  language filter.
- `rebase` is a per-project merge setting on GitLab, so merges support `merge` and
  `squash`.
- "Request changes" is posted as a note, because GitLab's free tier has no
  request-changes review state.

## How it is built

```text
React SPA ──(session cookie / header, CSRF)──► FastAPI
                                                │
             agents · CrewAI tools · toolkit ───┤ gitpilot/scm.py   provider dispatch
                                                ├─► gitpilot/github_api.py, github_pulls, github_issues, github_search
                                                └─► gitpilot/gitlab_api.py  (GitLab REST v4 → GitHub-shaped payloads)
```

- **One agent implementation.** Agents, CrewAI tools (`agent_tools`, `pr_tools`,
  `issue_tools`, `search_tools`), toolkit tools (`fs.*`, forge) and the HTTP routes
  call `gitpilot.scm`. It dispatches to GitHub or GitLab. `gitlab_api` returns the
  same payload shapes as the GitHub helpers: merge requests come back as pull
  requests with `number` = iid and `head.ref` = source branch. So nothing downstream
  is duplicated.
- **Explicit provider and stable id.** The repository objects the frontend uses carry
  `provider` and `id`. Agent requests send `provider`, `repo_owner` (on GitLab this
  may be a nested namespace), `repo_name` and `repo_id`. GitLab API calls use the
  numeric project id when it is bound to the request, which survives renames, and
  the URL-encoded full path otherwise. Sessions store `provider` and `repo_id`.
- **Local data stays apart.** Clones, context packs and memory for GitLab projects
  live under `~/.gitpilot/workspaces/_gitlab/…`, so `acme/app` on GitHub and on
  GitLab never share a directory.
- **Clone credentials.** The local clone used by the streaming engine has a
  credential-free remote URL. Fetch and push authenticate with an HTTP header passed
  through the git process environment and scoped to the provider host. Older clones
  that embedded a token have it removed.

## Provider isolation

A credential only ever reaches its own provider:

1. **Routes** resolve the credential *for the repository's provider*: the session's
   GitLab connection for a GitLab project, its GitHub connection for a GitHub
   repository.
2. **Request scope.** `scm.execution_context(provider, token, …)` binds the provider
   together with its credential. Inside a GitLab scope, `github_api` refuses to run,
   even with the server's own `GITHUB_TOKEN`. Inside a GitHub scope, `gitlab_api`
   refuses to run.
3. **Repository context.** CrewAI tools read the repository from a context variable
   that stores the provider next to the token, so a tool thread cannot pair one
   provider's token with the other's API. Concurrent runs no longer share a global.
4. **Token prefixes.** A token with the other provider's visible prefix (`ghp_`,
   `gho_`, `github_pat_`, `glpat-`, …) is refused by the API helpers and ignored by
   header resolution.
5. **Topologies.** A topology that grants `github.pr.create: ask` grants
   `gitlab.mr.create: ask` too, unless it names `gitlab.*` keys itself. A run is only
   offered its own provider's forge tools.

`tests/test_scm_provider_isolation.py` checks each of these, and
`tests/test_gitlab_agent_workflow.py` asserts that a whole GitLab execution sends
nothing to `api.github.com`.

## Permissions

- **Reading** a project needs at least Guest/Reporter access, depending on the
  project's visibility.
- **Writing** (branches, commits, merge requests) needs **Developer** or higher. The
  workspace panel shows "Read Only (needs Developer role)" otherwise.
- GitLab's own errors are passed through:
  - 401 asks the user to reconnect;
  - 403 and 404 name the project;
  - 429 keeps `Retry-After`.
- Protected branches follow the project's GitLab settings. GitPilot always writes to
  a working branch and opens a merge request into the default branch.

## API reference (`/api/gitlab/...`)

All routes take the numeric project id and use the session's GitLab connection.

| Method | Path | Purpose |
|---|---|---|
| GET | `/auth/url` · POST `/auth/callback` · POST `/auth/validate` | OAuth (aliases of `/api/auth/oauth/gitlab/*`) and token validation for API clients |
| GET | `/projects?query&page&per_page&min_access_level` | Projects you are a member of |
| GET | `/groups` · `/groups/{id}/projects` | Groups/subgroups and their projects (subgroups included) |
| GET | `/projects/{id}` | Project with your effective permissions |
| GET | `/projects/{id}/tree?ref&include_dirs` | Recursive tree |
| GET / POST / DELETE | `/projects/{id}/file` | Read · create or update · delete (one commit, named branch) |
| GET / POST | `/projects/{id}/branches` | List · create |
| GET | `/projects/{id}/commits` · `/projects/{id}/compare?base&head` | History · diff |
| GET / POST | `/projects/{id}/merge_requests` | List · open |
| GET / PUT | `/projects/{id}/merge_requests/{iid}` | Read · edit, close or reopen |
| PUT | `/projects/{id}/merge_requests/{iid}/merge` | Merge or squash-merge |
| GET | `/projects/{id}/merge_requests/{iid}/changes` | Changed files with patches |
| POST | `/projects/{id}/merge_requests/{iid}/reviews` · `/notes` | Approve, request changes or comment · comment |
| GET / POST | `/projects/{id}/issues` | List · open |
| GET / PATCH | `/projects/{id}/issues/{iid}` | Read · edit, close or reopen |
| GET / POST | `/projects/{id}/issues/{iid}/notes` | Comments |

The agent routes (`/api/chat/plan`, `/api/chat/execute`, `/api/chat/message`,
`/api/chat/execute-with-pr`) accept `provider` and `repo_id`. `/api/sessions` accepts
and filters by `provider`.

## Troubleshooting

- **"GitLab sign-in is not configured on this server."** `GITLAB_CLIENT_ID`,
  `GITLAB_CLIENT_SECRET` and a redirect URI (`GITLAB_REDIRECT_URI` or
  `GITPILOT_PUBLIC_BASE_URL`) are all required.
- **"The redirect URI included is not valid."** The GitLab application's redirect
  URI must equal the backend's redirect URI exactly, including scheme, host, port
  and `/auth`.
- **"This sign-in link has expired or was already used."** The authorization took
  more than ten minutes, or the callback page was reloaded. Start again.
- **The GitLab connection disappears after a while.** The refresh token was revoked
  in GitLab (Applications → Authorized applications), or the vault key changed.
  Reconnect from Admin → Integrations.
