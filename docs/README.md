# GitPilot Documentation

Welcome to the GitPilot documentation! This directory contains comprehensive guides for setting up and using GitPilot.

## 📚 Documentation Index

### Authentication & Security

- **[GitHub App Setup Guide](GITHUB_APP_SETUP.md)** ⭐ **START HERE**
  - Step-by-step tutorial to create your own GitHub App
  - Detailed instructions with examples
  - Troubleshooting and security best practices
  - **Perfect for first-time setup**

- **[Authentication Guide](../AUTHENTICATION.md)**
  - Complete authentication documentation
  - All authentication modes (PAT, OAuth, App, Hybrid)
  - Security features and best practices
  - CLI commands reference
  - Migration guides

---

## 🚀 Quick Start by Use Case

### I Want to Test GitPilot Quickly (2 minutes)

1. Create a Personal Access Token at https://github.com/settings/tokens
2. Add to `.env`: `GITPILOT_GITHUB_TOKEN=ghp_your_token`
3. Configure your LLM provider (OpenAI, Claude, etc.)
4. Run: `gitpilot`

**See:** [Authentication Guide - Simple Setup](../AUTHENTICATION.md#simple-setup-pat)

---

### I Want Enterprise-Grade Security (20 minutes)

1. **Follow the [GitHub App Setup Guide](GITHUB_APP_SETUP.md)**
   - Create your GitHub App
   - Configure permissions
   - Install on repositories
   - Get credentials

2. **Configure GitPilot:**
   ```bash
   cp .env.template .env
   # Add your GitHub App credentials to .env
   ```

3. **Login and start:**
   ```bash
   gitpilot login  # OAuth authentication
   gitpilot        # Start the server
   ```

**See:** [GitHub App Setup Guide](GITHUB_APP_SETUP.md)

---

### I'm Setting Up for My Team/Organization

1. **Create Organization-Level GitHub App**
   - Follow [GitHub App Setup Guide](GITHUB_APP_SETUP.md)
   - Create app under your organization settings
   - Set repository restrictions

2. **Configure Hybrid Authentication:**
   - User OAuth for identity
   - GitHub App for repository access
   - **See:** [Authentication Guide - Hybrid Mode](../AUTHENTICATION.md#enterprise-setup-hybrid)

3. **Deploy to Production:**
   - Use environment variables for secrets
   - Set up proper callback URLs
   - Configure HTTPS
   - Enable monitoring

---

## 📖 Documentation Overview

### For Developers

| Document | Description | Time |
|----------|-------------|------|
| [GitHub App Setup](GITHUB_APP_SETUP.md) | How to create a GitHub App | 15 min |
| [Authentication Guide](../AUTHENTICATION.md) | Complete auth documentation | 30 min read |
| [.env.template](../.env.template) | Configuration reference | 5 min |

### For System Administrators

| Topic | Where to Find It |
|-------|------------------|
| Security best practices | [GitHub App Setup - Security](GITHUB_APP_SETUP.md#security-best-practices) |
| Production deployment | [Authentication Guide - Hybrid Mode](../AUTHENTICATION.md#enterprise-setup-hybrid) |
| Troubleshooting | [GitHub App Setup - Troubleshooting](GITHUB_APP_SETUP.md#troubleshooting) |
| API endpoints | [Authentication Guide - API Endpoints](../AUTHENTICATION.md#api-endpoints) |

### For End Users

| Task | Guide |
|------|-------|
| First-time login | [Authentication Guide - Quick Start](../AUTHENTICATION.md#quick-start) |
| Check auth status | Run `gitpilot whoami` |
| Logout | Run `gitpilot logout` |
| Repository access issues | [Troubleshooting](GITHUB_APP_SETUP.md#troubleshooting) |

---

## 🔐 Authentication Modes Comparison

| Mode | Setup Time | Security | Best For |
|------|------------|----------|----------|
| **PAT** | 2 min | Medium | Testing, personal projects |
| **OAuth** | 10 min | High | Multi-user environments |
| **App** | 15 min | High | Organizations, repo selection |
| **Hybrid** | 20 min | Highest | Enterprise, production |

**Recommendation:**
- **Development:** Use PAT mode
- **Production:** Use Hybrid mode (OAuth + App)

---

## 🛠️ Common Tasks

### Creating a GitHub App

**Full Guide:** [GitHub App Setup Guide](GITHUB_APP_SETUP.md)

**Quick Steps:**
1. Go to https://github.com/settings/apps
2. Click "New GitHub App"
3. Configure permissions (Contents, Issues, PRs)
4. Generate private key
5. Install on repositories

### Logging In

```bash
# OAuth login (requires GITPILOT_OAUTH_CLIENT_ID in .env)
gitpilot login

# Check status
gitpilot whoami
```

### Changing Authentication Mode

Edit `.env`:
```bash
# For PAT mode
GITPILOT_GITHUB_AUTH_MODE=pat
GITPILOT_GITHUB_TOKEN=ghp_xxx

# For App mode
GITPILOT_GITHUB_AUTH_MODE=app
GITPILOT_GH_APP_ID=123456
# ... other app settings

# For Hybrid mode (recommended)
GITPILOT_GITHUB_AUTH_MODE=hybrid
# ... both OAuth and App settings
```

Restart GitPilot: `gitpilot`

### Troubleshooting Authentication

```bash
# 1. Check authentication status
gitpilot whoami

# 2. Test API connectivity
curl http://localhost:8000/api/auth/status | jq

# 3. View detailed logs
gitpilot 2>&1 | tee gitpilot.log

# 4. Re-login if needed
gitpilot logout
gitpilot login
```

**More troubleshooting:** [GitHub App Setup - Troubleshooting](GITHUB_APP_SETUP.md#troubleshooting)

---

## 📝 Environment Variables Reference

### GitHub Authentication

```bash
# Authentication mode
GITPILOT_GITHUB_AUTH_MODE=hybrid  # pat|oauth|app|hybrid

# Personal Access Token (PAT)
GITPILOT_GITHUB_TOKEN=ghp_xxx

# OAuth Configuration
GITPILOT_OAUTH_CLIENT_ID=Iv1.xxx
GITPILOT_OAUTH_CLIENT_SECRET=xxx

# GitHub App Configuration
GITPILOT_GH_APP_ID=123456
GITPILOT_GH_APP_INSTALLATION_ID=12345678
GITPILOT_GH_APP_PRIVATE_KEY_BASE64=LS0tLS...
GITPILOT_GH_APP_CLIENT_ID=Iv1.xxx
GITPILOT_GH_APP_CLIENT_SECRET=xxx
GITPILOT_GH_APP_SLUG=gitpilot-yourname
```

**Full reference:** See [.env.template](../.env.template)

---

## 🔗 External Resources

- **GitHub Apps Documentation:** https://docs.github.com/en/apps
- **GitHub OAuth Documentation:** https://docs.github.com/en/apps/oauth-apps
- **GitHub API Reference:** https://docs.github.com/en/rest
- **GitPilot Repository:** https://github.com/ruslanmv/gitpilot
- **Report Issues:** https://github.com/ruslanmv/gitpilot/issues

---

## 💡 Tips & Best Practices

### Security

✅ **DO:**
- Use Hybrid mode for production
- Rotate private keys every 90 days
- Use `.gitignore` to exclude `.env` and `*.pem` files
- Store secrets in environment variables or secret managers
- Review app permissions regularly

❌ **DON'T:**
- Commit `.env` or `.pem` files to Git
- Share private keys or client secrets
- Use "All repositories" in production
- Grant unnecessary permissions

### Performance

- Use GitHub App mode for better rate limits
- Cache repository lists when possible
- Monitor API usage in app settings

### Team Collaboration

- Create apps at organization level
- Document your app configuration
- Share app installation instructions with team
- Set up proper access controls

---

## 🆘 Getting Help

### Self-Service

1. **Check the documentation:**
   - [GitHub App Setup Guide](GITHUB_APP_SETUP.md)
   - [Authentication Guide](../AUTHENTICATION.md)

2. **Search existing issues:**
   - https://github.com/ruslanmv/gitpilot/issues

3. **Run diagnostics:**
   ```bash
   gitpilot whoami
   curl http://localhost:8000/api/auth/status | jq
   ```

### Community Support

- **GitHub Issues:** https://github.com/ruslanmv/gitpilot/issues
- **Discussions:** https://github.com/ruslanmv/gitpilot/discussions

### Reporting Bugs

When reporting issues, include:
- GitPilot version (`gitpilot version`)
- Authentication mode (from `.env`)
- Error messages (from logs)
- Steps to reproduce

---

## 📊 What's Next?

After setting up authentication:

1. **Configure LLM Provider:**
   - OpenAI, Claude, Watsonx, or Ollama
   - See `.env.template` for configuration

2. **Start Using GitPilot:**
   ```bash
   gitpilot
   # Open http://localhost:8000
   ```

3. **Explore Features:**
   - Repository analysis
   - Code suggestions
   - Automated planning
   - Agent workflows

4. **Deploy to Production:**
   - Set up HTTPS
   - Configure proper domains
   - Use secret management
   - Set up monitoring

---

**Last Updated:** 2025-11-16
**GitPilot Version:** 0.1.1

**Questions?** Check the [GitHub App Setup Guide](GITHUB_APP_SETUP.md) or [Authentication Guide](../AUTHENTICATION.md)
