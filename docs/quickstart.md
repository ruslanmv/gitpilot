# GitPilot - Quick Start Guide

Get started with GitPilot in **2 minutes**!

## 🚀 Fastest Way to Start (Personal Access Token)

### Step 1: Generate GitHub Token

1. Go to: https://github.com/settings/tokens/new?scopes=repo,user:email
2. Click **"Generate token"**
3. Copy the token (starts with `ghp_`)

### Step 2: Configure GitPilot

```bash
# Create .env file
cp .env.template .env

# Add your token
echo "GITPILOT_GITHUB_TOKEN=ghp_your_token_here" >> .env
```

### Step 3: Start GitPilot

```bash
gitpilot serve --open
```

**That's it!** GitPilot will open in your browser and you're ready to go.

---

## 🏢 Enterprise Setup (GitHub App) - Like Claude Code

For teams and organizations, use GitHub App installation for better security and user management.

### What You'll See

When you start GitPilot without a token, you'll see this installation dialog:

```
┌─────────────────────────────────────────────┐
│                                             │
│                    [GP]                     │
│                                             │
│       Install GitPilot GitHub App           │
│                                             │
│  The GitPilot GitHub app must be           │
│  installed in your repositories to         │
│  use GitPilot.                              │
│                                             │
│  ❌ GitHub app is not installed             │
│                                             │
│  Steps:                                     │
│  1️⃣ Install GitPilot App                    │
│     Grant GitPilot access                   │
│                                             │
│  2️⃣ Authenticate Your Account               │
│     Sign in to start using GitPilot         │
│                                             │
│     [ Install GitPilot → ]                  │
│                                             │
└─────────────────────────────────────────────┘
```

### Installation Flow

1. **Click "Install GitPilot →"**
   - Opens GitHub in your browser
   - Shows app installation page

2. **Select Repositories**
   - Choose "Only select repositories"
   - Select the repos you want to use with GitPilot
   - Click green **"Install"** button

3. **Return to GitPilot**
   - Come back to the GitPilot window
   - Click **"Check status"** button
   - Wait for verification

4. **Start Using!**
   - Once installed, automatically logged in
   - Can now select repositories and start working

---

## 🎯 What Each Method Gives You

| Feature | Personal Access Token | GitHub App |
|---------|----------------------|------------|
| **Setup Time** | 2 minutes | 5 minutes |
| **Best For** | Personal use, testing | Teams, organizations |
| **User Experience** | Enter token once | Install app → auto-login |
| **Security** | Token in .env | Per-repo permissions |
| **Team Sharing** | Share tokens (not recommended) | Each user installs |
| **Like Claude Code** | ❌ | ✅ |

---

## 📝 Troubleshooting

### "GitHub app is not installed"

**Solution:**
1. Make sure you completed the installation on GitHub
2. Click "Check status" button
3. Wait 10-20 seconds and try again

### "Authentication not configured"

**Solution:**
Add a Personal Access Token to `.env`:
```bash
GITPILOT_GITHUB_TOKEN=ghp_your_token_here
```

### Can't see any repositories

**Solution:**
- Token/App needs `repo` and `user:email` scopes
- Make sure app is installed to the repositories you want to access
- For GitHub App: Go to https://github.com/settings/installations and check which repos have access

---

## 🔧 Configuration Options

GitPilot supports three authentication methods (in priority order):

### 1. GitHub App (Recommended for Teams)

```bash
GITHUB_APP_ID=123456
GITHUB_APP_SLUG=gitpilot
```

**When to use:** Enterprise environments, teams, Claude Code-style experience

### 2. Personal Access Token (Recommended for Quick Start)

```bash
GITPILOT_GITHUB_TOKEN=ghp_xxxxxxxxxxxx
```

**When to use:** Personal use, testing, quick start, CLI usage

### 3. OAuth App (Advanced)

```bash
GITHUB_CLIENT_ID=Iv1.xxxxx
GITHUB_CLIENT_SECRET=xxxxx
GITHUB_REDIRECT_URI=http://localhost:8000/api/auth/callback
```

**When to use:** Custom SSO, enterprise auth, advanced setups

---

## 🎨 What You'll See After Login

Once authenticated, you'll see:

```
┌─────────────────────────────────────────────────────┐
│ [GP] GitPilot                      👤 @yourname      │
├─────────────────────────────────────────────────────┤
│                                                      │
│  📁 Workspace      🔄 Agent Flow      ⚙️ Settings    │
│                                                      │
│  Search repositories...          [Search]           │
│                                                      │
│  📦 yourname/repo1               Private            │
│  📦 yourname/repo2               Public             │
│  📦 organization/repo3           Private            │
│                                                      │
│  [Select a repository to start]                     │
│                                                      │
└─────────────────────────────────────────────────────┘
```

---

## 🚦 Next Steps

After logging in:

1. **Select a Repository**
   - Search for your repo
   - Click to select it

2. **Ask Questions**
   - "What does this codebase do?"
   - "Where is the authentication logic?"
   - "Find all API endpoints"

3. **Generate Plans**
   - "Add a dark mode feature"
   - "Fix the login bug in UserAuth.js"
   - "Refactor the API client"

4. **Execute Changes**
   - Review the AI-generated plan
   - Click "Execute" to apply changes
   - GitPilot commits directly to your repo

---

## 📚 Full Documentation

- **GitHub Setup:** `docs/GITHUB_SETUP.md`
- **Enterprise Login:** `docs/ENTERPRISE_LOGIN.md`
- **Environment Config:** `.env.template`

---

## 💬 Need Help?

- Check logs: `~/.gitpilot/logs/`
- Enable debug: `GITPILOT_DEBUG=true` in `.env`
- GitHub issues: https://github.com/ruslanmv/gitpilot/issues

---

**Ready to start?** Just run:

```bash
# Quickstart with PAT
echo "GITPILOT_GITHUB_TOKEN=ghp_your_token" >> .env
gitpilot serve --open

# Or let GitPilot guide you through GitHub App installation
gitpilot serve --open
```

Happy coding with GitPilot! 🚀
