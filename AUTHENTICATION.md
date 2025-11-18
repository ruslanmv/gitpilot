# GitPilot Authentication Guide

GitPilot implements a **two-layer enterprise authentication system** to securely access your GitHub repositories. This guide explains how authentication works and how to set it up.

## Table of Contents

- [Overview](#overview)
- [Authentication Modes](#authentication-modes)
- [Quick Start](#quick-start)
- [Setup Guides](#setup-guides)
  - [Simple Setup (PAT)](#simple-setup-pat)
  - [User OAuth Setup](#user-oauth-setup)
  - [GitHub App Setup](#github-app-setup)
  - [Enterprise Setup (Hybrid)](#enterprise-setup-hybrid)
- [CLI Commands](#cli-commands)
- [Security](#security)
- [Troubleshooting](#troubleshooting)

---

## Overview

GitPilot supports **four authentication modes**:

### 1. Personal Access Token (PAT) Mode
- **Use Case**: Development, testing, quick setup
- **Security**: Medium (token has full access)
- **Setup Time**: 2 minutes
- **Recommended For**: Individual developers

### 2. User OAuth Mode
- **Use Case**: User authentication
- **Security**: High (scoped permissions)
- **Setup Time**: 10 minutes
- **Recommended For**: Multi-user environments

### 3. GitHub App Mode
- **Use Case**: Repository-level access control
- **Security**: High (granular permissions)
- **Setup Time**: 15 minutes
- **Recommended For**: Team/organization deployments

### 4. Hybrid Mode (OAuth + App)
- **Use Case**: Enterprise deployments
- **Security**: Highest (two-layer auth)
- **Setup Time**: 20 minutes
- **Recommended For**: Production environments

---

## Authentication Modes

### Mode Comparison

| Feature | PAT | OAuth | App | Hybrid |
|---------|-----|-------|-----|--------|
| User Identity | ❌ | ✅ | ❌ | ✅ |
| Repository Selection | ❌ | ❌ | ✅ | ✅ |
| Granular Permissions | ❌ | ✅ | ✅ | ✅ |
| Secure Token Storage | ❌ | ✅ | ✅ | ✅ |
| Multi-User Support | ❌ | ✅ | ✅ | ✅ |
| Audit Trail | ❌ | ❌ | ✅ | ✅ |
| Login/Logout | ❌ | ✅ | ❌ | ✅ |
| Best For | Dev | Teams | Orgs | Enterprise |

---

## Quick Start

### Option A: Simple Setup (2 minutes)

For development or personal use:

```bash
# 1. Create a Personal Access Token
# Visit: https://github.com/settings/tokens/new
# Scopes: repo, read:user

# 2. Add to .env file
echo "GITPILOT_GITHUB_TOKEN=ghp_your_token_here" >> .env

# 3. Start GitPilot
gitpilot
```

### Option B: Enterprise Setup (20 minutes)

For production deployment:

```bash
# 1. Configure authentication mode
echo "GITPILOT_GITHUB_AUTH_MODE=hybrid" >> .env

# 2. Set up OAuth and GitHub App (see detailed guides below)

# 3. Login via CLI
gitpilot login

# 4. Start GitPilot
gitpilot
```

---

## Setup Guides

### Simple Setup (PAT)

**Best for**: Quick testing, personal projects

1. **Create a Personal Access Token**
   - Go to https://github.com/settings/tokens/new
   - Give it a descriptive name (e.g., "GitPilot Local Dev")
   - Select scopes:
     - ✅ `repo` (full control of private repositories)
     - ✅ `read:user` (read user profile data)
   - Click "Generate token"
   - **Copy the token** (you won't see it again!)

2. **Configure GitPilot**
   ```bash
   # Create .env file
   cp .env.template .env

   # Edit .env and add:
   GITPILOT_GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
   GITPILOT_GITHUB_AUTH_MODE=pat
   ```

3. **Start GitPilot**
   ```bash
   gitpilot
   ```

---

### User OAuth Setup

**Best for**: Multi-user environments, team deployments

#### Step 1: Create GitHub OAuth App

1. Go to https://github.com/settings/developers
2. Click "New OAuth App"
3. Fill in the form:
   - **Application name**: `GitPilot` (or your custom name)
   - **Homepage URL**: `http://localhost:8000` (or your domain)
   - **Authorization callback URL**: `http://localhost:8000/auth/callback`
4. Click "Register application"
5. Copy the **Client ID**
6. Click "Generate a new client secret"
7. Copy the **Client Secret**

#### Step 2: Configure GitPilot

```bash
# Edit .env file
GITPILOT_GITHUB_AUTH_MODE=oauth
GITPILOT_OAUTH_CLIENT_ID=Iv1.xxxxxxxxxxxxxxxx
GITPILOT_OAUTH_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

#### Step 3: Login

```bash
# Login via CLI (device flow)
gitpilot login

# The CLI will display:
# - A verification URL (https://github.com/login/device)
# - A user code (XXXX-XXXX)

# Open the URL in your browser and enter the code
# Authorize the application
# Return to the CLI - you should see "Successfully logged in!"
```

#### Step 4: Verify Authentication

```bash
# Check authentication status
gitpilot whoami

# Output should show:
# User OAuth        ✅ Authenticated
# GitHub Username   your-username
```

---

### GitHub App Setup

**Best for**: Repository-level access control, organizations

#### Step 1: Create GitHub App

1. Go to https://github.com/settings/apps/new
2. Fill in the form:

   **Basic Information:**
   - **GitHub App name**: `GitPilot` (must be unique)
   - **Homepage URL**: `http://localhost:8000`
   - **Callback URL**: `http://localhost:8000/auth/callback`
   - **Setup URL**: (leave blank)
   - **Webhook URL**: (leave blank for now)
   - **Webhook secret**: (leave blank)

   **Permissions:**
   - Repository permissions:
     - ✅ **Contents**: Read & write
     - ✅ **Issues**: Read & write
     - ✅ **Pull requests**: Read & write
     - ✅ **Metadata**: Read-only (automatic)

   **Where can this GitHub App be installed?**
   - ✅ Only on this account (or "Any account" for public apps)

3. Click "Create GitHub App"

#### Step 2: Generate Private Key

1. Scroll down to "Private keys"
2. Click "Generate a private key"
3. Save the downloaded `.pem` file
4. Base64 encode it:
   ```bash
   cat your-app-private-key.pem | base64 -w 0 > private-key.txt
   ```

#### Step 3: Install the App

1. Go to https://github.com/apps/YOUR-APP-NAME/installations/new
2. Select the repositories you want GitPilot to access:
   - All repositories
   - OR Select specific repositories
3. Click "Install"
4. You'll be redirected to a page with the Installation ID in the URL:
   ```
   https://github.com/settings/installations/12345678
                                                ^^^^^^^^
                                                This is your Installation ID
   ```

#### Step 4: Configure GitPilot

```bash
# Edit .env file
GITPILOT_GITHUB_AUTH_MODE=app
GITPILOT_GH_APP_ID=123456
GITPILOT_GH_APP_INSTALLATION_ID=12345678
GITPILOT_GH_APP_PRIVATE_KEY_BASE64=LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0tLQo...
GITPILOT_GH_APP_SLUG=your-app-name
```

---

### Enterprise Setup (Hybrid)

**Best for**: Production, enterprise environments

This combines **User OAuth** (for identity) + **GitHub App** (for repository access).

#### Complete Both Setups

1. Follow the [User OAuth Setup](#user-oauth-setup) guide
2. Follow the [GitHub App Setup](#github-app-setup) guide

#### Configure Hybrid Mode

```bash
# Edit .env file
GITPILOT_GITHUB_AUTH_MODE=hybrid

# User OAuth
GITPILOT_OAUTH_CLIENT_ID=Iv1.xxxxxxxxxxxxxxxx
GITPILOT_OAUTH_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# GitHub App
GITPILOT_GH_APP_ID=123456
GITPILOT_GH_APP_INSTALLATION_ID=12345678
GITPILOT_GH_APP_PRIVATE_KEY_BASE64=LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0tLQo...
GITPILOT_GH_APP_CLIENT_ID=Iv1.yyyyyyyyyyyyyyyy
GITPILOT_GH_APP_CLIENT_SECRET=yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy
GITPILOT_GH_APP_SLUG=your-app-name
```

#### Login

```bash
# Login via CLI
gitpilot login

# Start GitPilot
gitpilot

# Access web UI - you should see your username and logout button
```

---

## CLI Commands

### `gitpilot login`

Authenticate with GitHub using OAuth device flow.

```bash
gitpilot login
```

**What happens:**
1. CLI generates a device code
2. Displays a verification URL and code
3. You visit the URL and enter the code
4. Authorize the application
5. Token is securely stored in your system keyring

### `gitpilot logout`

Remove stored credentials.

```bash
gitpilot logout
```

**What happens:**
1. Deletes OAuth token from keyring
2. Removes GitHub App private key from keyring
3. You'll need to login again to access GitHub

### `gitpilot whoami`

Display current authentication status.

```bash
gitpilot whoami
```

**Sample output:**
```
╭─────────────────────────────────────────╮
│ GitPilot Authentication Status          │
╰─────────────────────────────────────────╯

  User OAuth         ✅ Authenticated
  GitHub Username    ruslanmv
  GitHub App         ✅ Configured
  App ID             123456
  Installation ID    12345678
  Auth Mode          hybrid
```

---

## Security

### Token Storage

GitPilot uses **system keyring** for secure credential storage:

- **macOS**: Keychain
- **Windows**: Windows Credential Locker
- **Linux**: Secret Service (GNOME Keyring, KWallet, etc.)

Tokens are **never** stored in:
- Plain text files
- Environment variables (after initial login)
- Git repositories
- Log files

### Permissions

#### Personal Access Token (PAT)
- Full access to all repositories
- No expiration by default
- ⚠️ Use with caution

#### OAuth Token
- Scoped to requested permissions (`repo`, `read:user`)
- Can be revoked at any time
- Expires based on GitHub settings

#### GitHub App
- Granular, repository-level permissions
- Can be installed on specific repos only
- Full audit trail in GitHub
- ✅ **Recommended for production**

### Best Practices

1. **Use Hybrid Mode** for production deployments
2. **Rotate tokens** regularly
3. **Limit App installations** to only required repositories
4. **Enable Two-Factor Authentication** (2FA) on your GitHub account
5. **Review App permissions** periodically
6. **Use environment variables** for CI/CD (not keyring)

---

## Troubleshooting

### "GitHub token not configured"

**Problem**: GitPilot can't find authentication credentials.

**Solution**:
```bash
# Check auth status
gitpilot whoami

# If not authenticated:
gitpilot login

# OR set PAT in .env
echo "GITPILOT_GITHUB_TOKEN=ghp_xxx" >> .env
```

### "OAuth error: invalid_client"

**Problem**: OAuth Client ID or Secret is incorrect.

**Solution**:
1. Verify `GITPILOT_OAUTH_CLIENT_ID` in `.env`
2. Verify `GITPILOT_OAUTH_CLIENT_SECRET` in `.env`
3. Check OAuth app settings at https://github.com/settings/developers

### "GitHub App authentication failed"

**Problem**: App ID, Installation ID, or Private Key is incorrect.

**Solution**:
1. Verify `GITPILOT_GH_APP_ID` matches your app ID
2. Verify `GITPILOT_GH_APP_INSTALLATION_ID`
3. Re-generate and re-encode private key:
   ```bash
   cat your-app.pem | base64 -w 0
   ```

### "Permission denied" when accessing repos

**Problem**: GitHub App not installed on repository.

**Solution**:
1. Go to https://github.com/apps/YOUR-APP-NAME
2. Click "Configure"
3. Add the repository to the installation

### Keyring issues on Linux

**Problem**: No keyring backend available.

**Solution**:
```bash
# Install GNOME Keyring or KWallet
sudo apt-get install gnome-keyring  # Ubuntu/Debian
sudo dnf install gnome-keyring      # Fedora

# OR use fallback to environment variables
export GITPILOT_GITHUB_TOKEN=ghp_xxx
```

---

## API Endpoints

For frontend developers:

### `GET /api/auth/status`

Check authentication status.

**Response:**
```json
{
  "authenticated": true,
  "auth_mode": "hybrid",
  "has_user_token": true,
  "has_app_config": true,
  "setup_completed": true,
  "username": "ruslanmv"
}
```

### `POST /api/auth/login`

Initiate login (returns instructions for CLI login).

### `POST /api/auth/logout`

Logout and clear credentials.

### `GET /api/auth/github-app-install-url`

Get GitHub App installation URL.

**Response:**
```json
{
  "install_url": "https://github.com/apps/gitpilota/installations/new"
}
```

---

## Migration Guide

### From PAT to OAuth

```bash
# 1. Set up OAuth app (see guide above)

# 2. Update .env
GITPILOT_GITHUB_AUTH_MODE=oauth
GITPILOT_OAUTH_CLIENT_ID=xxx
GITPILOT_OAUTH_CLIENT_SECRET=xxx

# 3. Login
gitpilot login

# 4. (Optional) Remove PAT
# Delete GITPILOT_GITHUB_TOKEN from .env
```

### From OAuth to Hybrid

```bash
# 1. Set up GitHub App (see guide above)

# 2. Update .env
GITPILOT_GITHUB_AUTH_MODE=hybrid
# (keep OAuth settings)
GITPILOT_GH_APP_ID=xxx
GITPILOT_GH_APP_INSTALLATION_ID=xxx
GITPILOT_GH_APP_PRIVATE_KEY_BASE64=xxx

# 3. Restart GitPilot
gitpilot
```

---

## Support

For issues or questions:

- GitHub Issues: https://github.com/ruslanmv/gitpilot/issues
- Documentation: https://github.com/ruslanmv/gitpilot#readme

---

**Last Updated**: 2025-11-16
