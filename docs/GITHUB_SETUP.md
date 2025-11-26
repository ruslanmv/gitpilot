# GitHub Authentication Setup Guide

GitPilot supports two authentication methods for GitHub integration:

1. **GitHub OAuth App** (Recommended for Enterprise) - Professional SSO-style login
2. **Personal Access Token** (PAT) - Simple token-based authentication

## Table of Contents

- [Option 1: GitHub OAuth App Setup (Recommended)](#option-1-github-oauth-app-setup-recommended)
- [Option 2: Personal Access Token Setup](#option-2-personal-access-token-setup)
- [GitHub App Installation (Optional)](#github-app-installation-optional)
- [Security Best Practices](#security-best-practices)
- [Troubleshooting](#troubleshooting)

---

## Option 1: GitHub OAuth App Setup (Recommended)

### Why Use OAuth?

- Professional enterprise login experience
- Users authenticate directly with GitHub (no token sharing)
- Granular permission management
- Automatic token refresh
- Audit trail of access

### Step 1: Create a GitHub OAuth App

#### For Personal Accounts:

1. Go to **GitHub Settings** → **Developer Settings** → **OAuth Apps**
   - Direct link: https://github.com/settings/developers
2. Click **"New OAuth App"**

#### For Organizations:

1. Go to your organization settings
   - Navigate to: `https://github.com/organizations/{your-org}/settings/applications`
2. Click **"New OAuth App"**

### Step 2: Configure OAuth App Settings

Fill in the following information:

| Field | Value | Description |
|-------|-------|-------------|
| **Application name** | GitPilot Enterprise | Display name for users |
| **Homepage URL** | `http://localhost:8000` | For local development |
| **Application description** | AI-powered GitHub repository assistant | Optional description |
| **Authorization callback URL** | `http://localhost:8000/api/auth/callback` | **Critical:** Must match exactly |

**Production URLs:** Replace `localhost:8000` with your production domain:
- Homepage URL: `https://gitpilot.yourcompany.com`
- Callback URL: `https://gitpilot.yourcompany.com/api/auth/callback`

### Step 3: Save Client Credentials

After creating the app, you'll see:

1. **Client ID** - Public identifier (safe to expose in frontend)
2. **Client Secret** - Click **"Generate a new client secret"**
   - ⚠️ **Important:** Save this secret immediately! GitHub only shows it once.

### Step 4: Configure Environment Variables

Create or edit `.env` in your GitPilot root directory:

```bash
# GitHub OAuth Configuration
GITHUB_CLIENT_ID=Iv1.a1b2c3d4e5f6g7h8
GITHUB_CLIENT_SECRET=abc123def456ghi789jkl012mno345pqr678stu
GITHUB_REDIRECT_URI=http://localhost:8000/api/auth/callback
```

### Step 5: Test OAuth Flow

1. **Start GitPilot server:**
   ```bash
   gitpilot serve
   ```

2. **Open browser:**
   - Navigate to `http://localhost:8000`
   - You should see the enterprise login page

3. **Click "Continue with GitHub":**
   - Redirects to GitHub authorization page
   - Shows requested permissions: `repo` and `user:email`
   - Click "Authorize"

4. **Redirected back to GitPilot:**
   - Automatically logged in
   - Can now select and work with repositories

### OAuth Flow Diagram

```
┌─────────┐                 ┌─────────┐                 ┌─────────┐
│ Browser │                 │ GitPilot│                 │ GitHub  │
│  (User) │                 │  Server │                 │  OAuth  │
└────┬────┘                 └────┬────┘                 └────┬────┘
     │                           │                           │
     │ 1. Click "Login"          │                           │
     ├──────────────────────────>│                           │
     │                           │                           │
     │ 2. Get auth URL           │                           │
     │<──────────────────────────┤                           │
     │                           │                           │
     │ 3. Redirect to GitHub     │                           │
     ├───────────────────────────────────────────────────────>│
     │                           │                           │
     │ 4. User authorizes        │                           │
     │                           │                           │
     │ 5. Callback with code     │                           │
     │<──────────────────────────────────────────────────────┤
     │                           │                           │
     │ 6. Send code to server    │                           │
     ├──────────────────────────>│                           │
     │                           │                           │
     │                           │ 7. Exchange code for token│
     │                           ├──────────────────────────>│
     │                           │                           │
     │                           │ 8. Return access token    │
     │                           │<──────────────────────────┤
     │                           │                           │
     │ 9. Session created        │                           │
     │<──────────────────────────┤                           │
     │                           │                           │
     │ 10. Access repositories   │                           │
     └───────────────────────────────────────────────────────┘
```

---

## Option 2: Personal Access Token Setup

### Why Use PAT?

- Simple setup (no OAuth configuration needed)
- Good for personal use and development
- Works offline/CLI environments
- No callback URL required

### Step 1: Generate Personal Access Token

1. **Go to GitHub Settings:**
   - Direct link: https://github.com/settings/tokens/new?scopes=repo,user:email

2. **Configure token:**
   - **Note:** `GitPilot Access Token`
   - **Expiration:** Choose duration (recommend 90 days)
   - **Scopes:** Select the following:
     - ✅ `repo` - Full control of private repositories
       - ✅ `repo:status`
       - ✅ `repo_deployment`
       - ✅ `public_repo`
       - ✅ `repo:invite`
     - ✅ `user:email` - Access user email addresses

3. **Generate token:**
   - Click **"Generate token"**
   - Copy token immediately (starts with `ghp_`)
   - ⚠️ **Save securely** - GitHub won't show it again!

### Step 2: Configure Environment Variable

Add to `.env` file:

```bash
# Personal Access Token
GITPILOT_GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Or use the alternative variable name:

```bash
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Step 3: Test PAT Authentication

1. **Start server:**
   ```bash
   gitpilot serve
   ```

2. **Open browser:**
   - Navigate to `http://localhost:8000`
   - If PAT is valid, you'll see login page with PAT option

3. **Enter PAT:**
   - Paste your token in the input field
   - Click "Continue"
   - Should see repository list

---

## GitHub App Installation (Optional)

For even more advanced features, you can create a **GitHub App** (different from OAuth App). This is recommended for:

- Organization-wide installations
- Fine-grained repository permissions
- Webhook integrations
- GitHub Actions integration

### What Should the GitHub App Contain?

#### Repository Permissions

| Permission | Access Level | Purpose |
|------------|--------------|---------|
| **Contents** | Read & Write | Read files, create/modify code |
| **Metadata** | Read | Repository information |
| **Pull Requests** | Read & Write | Create and manage PRs |
| **Issues** | Read & Write | Create and comment on issues |
| **Workflows** | Read & Write | Trigger GitHub Actions (optional) |

#### User Permissions

| Permission | Access Level | Purpose |
|------------|--------------|---------|
| **Email addresses** | Read | User identification |

#### Events/Webhooks (Optional)

Subscribe to these events if you want real-time updates:

- `push` - Code pushed to repository
- `pull_request` - PR created/updated
- `issues` - Issue created/updated
- `issue_comment` - Comments on issues

### Creating a GitHub App

1. **Navigate to GitHub App settings:**
   - Personal: https://github.com/settings/apps
   - Organization: `https://github.com/organizations/{org}/settings/apps`

2. **Click "New GitHub App"**

3. **Configure app settings:**

   ```yaml
   GitHub App name: GitPilot
   Homepage URL: http://localhost:8000
   Callback URL: http://localhost:8000/api/auth/callback
   Setup URL: (leave empty)
   Webhook URL: (optional) http://localhost:8000/api/webhooks
   Webhook secret: (generate random string)

   Permissions:
     Repository permissions:
       - Contents: Read & Write
       - Metadata: Read
       - Pull requests: Read & Write
       - Issues: Read & Write

     User permissions:
       - Email addresses: Read

   Where can this app be installed?
     - Any account (or "Only on this account" for private use)
   ```

4. **Generate private key:**
   - Scroll down to "Private keys"
   - Click "Generate a private key"
   - Save the `.pem` file securely

5. **Install app:**
   - Click "Install App" from sidebar
   - Choose repositories:
     - "All repositories" or
     - "Only select repositories"
   - Click "Install"

6. **Configure environment:**

   ```bash
   # GitHub App Configuration
   GITHUB_APP_ID=123456
   GITHUB_APP_INSTALLATION_ID=7890123
   GITHUB_APP_PRIVATE_KEY_PATH=/path/to/gitpilot.pem
   ```

---

## Security Best Practices

### OAuth Apps

1. **Never commit secrets:**
   ```bash
   # Add to .gitignore
   .env
   .env.local
   .env.production
   ```

2. **Use environment variables:**
   - Development: `.env` file
   - Production: Environment variable injection
   - Kubernetes: Secrets
   - Docker: Secrets or env files

3. **Rotate credentials regularly:**
   - OAuth client secrets: Every 90 days
   - Review authorized applications quarterly

4. **Validate redirect URIs:**
   - Only whitelist exact callback URLs
   - Use HTTPS in production
   - Never use wildcards

5. **Implement CSRF protection:**
   - GitPilot uses `state` parameter (built-in)
   - Verify state on callback

### Personal Access Tokens

1. **Set expiration:**
   - Never use "No expiration"
   - Recommended: 30-90 days
   - Rotate before expiry

2. **Minimal scopes:**
   - Only grant required permissions
   - Review scope list regularly

3. **Storage:**
   - Never hardcode in source code
   - Use environment variables or secret managers
   - Encrypt at rest (use tools like `pass`, `1Password CLI`)

4. **Revocation:**
   - Revoke immediately if compromised
   - GitHub Settings → Developer Settings → Personal Access Tokens

---

## Troubleshooting

### OAuth Issues

#### "Invalid redirect_uri"

**Problem:** Callback URL doesn't match OAuth app settings

**Solution:**
1. Check `.env` `GITHUB_REDIRECT_URI` matches GitHub OAuth app settings exactly
2. Include protocol (`http://` or `https://`)
3. Match port number (`:8000`)
4. No trailing slash

#### "Bad credentials" during callback

**Problem:** Client secret incorrect or expired

**Solution:**
1. Regenerate client secret in GitHub OAuth app settings
2. Update `.env` with new secret
3. Restart GitPilot server

#### Users see authorization screen every time

**Problem:** Not storing tokens properly

**Solution:**
1. Check browser console for localStorage errors
2. Verify cookies not being blocked
3. Test in incognito mode (to rule out extensions)

### PAT Issues

#### "401 Unauthorized" errors

**Problem:** Token invalid, expired, or insufficient permissions

**Solution:**
1. Verify token copied correctly (starts with `ghp_`)
2. Check token expiration date
3. Regenerate token with correct scopes: `repo`, `user:email`

#### "403 Forbidden" on specific repos

**Problem:** Token doesn't have access to private repositories

**Solution:**
1. Ensure `repo` scope is enabled (not just `public_repo`)
2. Verify you have access to the repository in GitHub
3. For organization repos, check org OAuth app restrictions

### General Issues

#### Can't see any repositories

**Problem:** Authentication succeeded but repo list empty

**Solution:**
1. Check you have access to at least one repository in GitHub
2. Verify token has `repo` scope
3. Check browser console/network tab for API errors

#### "Rate limit exceeded"

**Problem:** Too many API requests

**Solution:**
1. Authenticated requests have 5,000/hour limit (vs 60/hour for anonymous)
2. Wait for rate limit to reset
3. Check if token is being sent properly (should appear in API request headers)

---

## Production Deployment Checklist

- [ ] Use HTTPS for all URLs
- [ ] Update OAuth app callback URL to production domain
- [ ] Store secrets in secure secret manager (AWS Secrets Manager, HashiCorp Vault, etc.)
- [ ] Enable CORS only for your domain
- [ ] Set up log monitoring for authentication failures
- [ ] Implement rate limiting
- [ ] Configure session timeout
- [ ] Set up backup OAuth app for redundancy
- [ ] Document credential rotation procedures
- [ ] Train team on security practices

---

## Next Steps

After setting up authentication:

1. **Test repository access:**
   - Select a test repository
   - Verify file tree loads
   - Test reading file contents

2. **Configure LLM providers:**
   - Go to Admin/Settings tab
   - Add API keys for OpenAI, Claude, or other providers

3. **Start using GitPilot:**
   - Ask questions about your codebase
   - Generate implementation plans
   - Execute code changes

4. **Explore advanced features:**
   - Agent flow visualization
   - Multi-step planning
   - Automated code generation

---

## Support

If you encounter issues:

1. Check logs: `~/.gitpilot/logs/`
2. Enable debug mode: `GITPILOT_DEBUG=true` in `.env`
3. Review GitHub API status: https://www.githubstatus.com/
4. Open an issue: https://github.com/ruslanmv/gitpilot/issues

For enterprise support, contact your GitPilot administrator or create a support ticket.
