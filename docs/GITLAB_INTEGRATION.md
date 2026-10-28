# GitLab.com connection — first release

GitPilot now offers Continue with GitLab on /auth and discovers GitLab.com repositories in the web workspace. Existing GitHub and email/password accounts remain unchanged.

## Configure

1. Create a confidential OAuth application under GitLab.com Preferences > Applications.
2. Register the frontend callback URL https://your-gitpilot-host/auth .
3. Allow scopes read_user and api. The api scope permits repository operations and is broad, so only approve this application on trusted installations.
4. Configure these backend environment variables:

    GITLAB_CLIENT_ID=your_application_id
    GITLAB_CLIENT_SECRET=your_application_secret
    GITLAB_REDIRECT_URI=https://your-gitpilot-host/auth

If GITLAB_REDIRECT_URI is omitted, GITPILOT_PUBLIC_BASE_URL + /auth is used. Restart the backend after changing variables. OAuth configuration belongs on the backend only.

## Available in this release

- OAuth web sign-in with CSRF state and S256 PKCE.
- GitLab identity validation and web session restoration.
- Project search/pagination, including nested group paths.
- Branch listing, repository tree browsing and source file previews.
- GitLab API for creating and updating repository files.
- Separate GitHub and GitLab choices in the login and repository selector.

Not yet supported: GitLab merge requests, issues, multi-agent planning or code execution, self-managed GitLab servers, secure server-side token vault. The web chat composer is deliberately disabled for GitLab projects until GitLab-aware agent tools exist. The repository editor API is available but there is not yet a full GitLab file-editing UI.

## API

GET /api/gitlab/auth/url - begin login
POST /api/gitlab/auth/callback - exchange OAuth code
POST /api/gitlab/auth/validate - restore identity
GET /api/gitlab/projects - list projects
GET /api/gitlab/projects/{id}/tree - list repository tree
GET /api/gitlab/projects/{id}/branches - list branches
GET /api/gitlab/projects/{id}/file - read file
POST /api/gitlab/projects/{id}/file - create or edit file

GitLab project numeric IDs are used to support arbitrary nested groups.

## Enterprise hardening and next steps

The first implementation follows GitPilot's existing GitHub pattern of storing access tokens in browser localStorage. For enterprise-grade authentication, migrate both providers to short-lived HttpOnly sessions and encrypted server-side per-user credentials. OAuth state is currently stored in process memory: use shared state storage in multi-worker deployments. GitLab.com is the only allowed API host for this first release.

Next introduce a repository-provider interface that supports get_tree, read_file, write_file, create_branch, list_issues, create_merge_request and review_diff. Require explicit provider selection and permission checks at each agent tool. This avoids accidentally sending GitLab credentials to GitHub-specific endpoints.
