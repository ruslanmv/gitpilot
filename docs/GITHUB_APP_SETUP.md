# How to Create a GitHub App for GitPilot

This tutorial will guide you through creating your own GitHub App to enable GitPilot's enterprise authentication system. This allows you to control which repositories GitPilot can access and provides a secure, auditable authentication mechanism.

## Table of Contents

- [Why Create a GitHub App?](#why-create-a-github-app)
- [Prerequisites](#prerequisites)
- [Step-by-Step Guide](#step-by-step-guide)
  - [Part 1: Create the GitHub App](#part-1-create-the-github-app)
  - [Part 2: Generate Private Key](#part-2-generate-private-key)
  - [Part 3: Install the App](#part-3-install-the-app)
  - [Part 4: Configure GitPilot](#part-4-configure-gitpilot)
- [Testing Your Setup](#testing-your-setup)
- [Troubleshooting](#troubleshooting)
- [Security Best Practices](#security-best-practices)

---

## Why Create a GitHub App?

A GitHub App provides several advantages over Personal Access Tokens:

✅ **Granular Permissions** - Only grant access to what GitPilot needs
✅ **Repository Selection** - Choose exactly which repos the app can access
✅ **Better Security** - Short-lived tokens, no user password required
✅ **Audit Trail** - All actions are logged under the app's identity
✅ **Team Collaboration** - Multiple users can use the same app
✅ **Rate Limits** - Higher API rate limits than user tokens

---

## Prerequisites

Before you begin, make sure you have:

- A GitHub account (personal or organization)
- Admin access to the repositories you want GitPilot to access
- GitPilot installed locally (`pip install gitcopilot`)
- Basic familiarity with terminal/command line

**Time Required:** ~15 minutes

---

## Step-by-Step Guide

### Part 1: Create the GitHub App

#### Step 1: Navigate to GitHub Apps Settings

1. Go to your GitHub account settings:
   - **For personal account**: https://github.com/settings/apps
   - **For organization**: https://github.com/organizations/YOUR_ORG/settings/apps

2. Click the **"New GitHub App"** button (top right corner)

#### Step 2: Fill in Basic Information

You'll see a form with several sections. Fill them in as follows:

**GitHub App Name:**
```
GitPilot
```
> **Note:** This name must be globally unique across all of GitHub. If "GitPilot" is taken, try:
> - `GitPilot-YourName`
> - `GitPilot-YourOrg`
> - `YourOrg-GitPilot`

**Description:** (optional but recommended)
```
Agentic AI assistant for GitHub repositories. Analyzes code, suggests improvements, and helps with development tasks.
```

**Homepage URL:**
```
http://localhost:8000
```
> For production deployments, use your actual domain:
> - `https://gitpilot.yourdomain.com`
> - `https://your-company.com/gitpilot`

**Callback URL:**
```
http://localhost:8000/auth/callback
```
> For production, update to match your domain:
> - `https://gitpilot.yourdomain.com/auth/callback`

#### Step 3: Configure Webhook (Optional)

For now, we'll skip webhooks. Scroll down to the webhook section:

- [ ] **Active** - Leave this **UNCHECKED**
- **Webhook URL** - Leave blank
- **Webhook secret** - Leave blank

> **Note:** Webhooks are optional and can be added later if you want real-time notifications for repository events.

#### Step 4: Set Permissions

This is the most important part. Scroll to **"Permissions"** section.

**Repository Permissions:**

| Permission | Access Level | Why GitPilot Needs It |
|------------|--------------|----------------------|
| **Contents** | **Read and write** | Read code files, create branches, commit changes |
| **Issues** | **Read and write** | Respond to issues, create issues for suggestions |
| **Pull requests** | **Read and write** | Create PRs, comment on PRs, suggest code changes |
| **Metadata** | **Read-only** | Basic repository information (automatically selected) |

To set these:

1. Find **"Repository permissions"** section
2. Click the dropdown next to **"Contents"** → Select **"Read and write"**
3. Click the dropdown next to **"Issues"** → Select **"Read and write"**
4. Click the dropdown next to **"Pull requests"** → Select **"Read and write"**
5. **"Metadata"** will automatically be set to **"Read-only"** (you can't change this)

**Account Permissions:**

Leave all **Account permissions** at **"No access"** - GitPilot doesn't need these.

#### Step 5: Choose Installation Location

Scroll to **"Where can this GitHub App be installed?"**

Choose one:

- ✅ **Only on this account** - Recommended for personal/organization use
  - Only you (or your org) can install this app
  - More secure
  - Easier to manage

- ⚪ **Any account** - For public apps
  - Anyone can install your app
  - Choose this if you want to share with others
  - Requires public app review for certain features

**Recommendation:** Choose **"Only on this account"** for most use cases.

#### Step 6: Create the App

1. Scroll to the bottom of the page
2. Click the green **"Create GitHub App"** button
3. You'll be redirected to your new app's settings page

🎉 **Congratulations!** You've created your GitHub App!

---

### Part 2: Generate Private Key

The private key allows GitPilot to authenticate as your app.

#### Step 1: Generate the Key

On your app's settings page:

1. Scroll down to the **"Private keys"** section
2. Click **"Generate a private key"**
3. A `.pem` file will download automatically (e.g., `gitpilot.2024-01-15.private-key.pem`)

**⚠️ IMPORTANT:**
- This is the **ONLY TIME** you'll see this private key
- Store it securely
- Never commit it to Git
- Treat it like a password

#### Step 2: Save Important Information

While on the app settings page, copy these values (you'll need them later):

| Field | Where to Find It | Example |
|-------|------------------|---------|
| **App ID** | Top of the page, under the app name | `123456` |
| **Client ID** | In the "Basic information" section | `Iv1.a1b2c3d4e5f6g7h8` |
| **Client Secret** | Click "Generate a new client secret" | `abc123def456...` |
| **App Slug** | In the URL and app name | `gitpilot-yourname` |

**To generate Client Secret:**
1. Find the **"Client secrets"** section
2. Click **"Generate a new client secret"**
3. Copy the secret immediately (you won't see it again!)

#### Step 3: Prepare the Private Key

The private key needs to be base64-encoded for GitPilot.

**On Linux/Mac:**
```bash
# Navigate to your downloads folder
cd ~/Downloads

# Encode the private key
cat gitpilot.*.private-key.pem | base64 -w 0 > private-key-encoded.txt

# Display the encoded key
cat private-key-encoded.txt
```

**On Windows (PowerShell):**
```powershell
# Navigate to your downloads folder
cd $HOME\Downloads

# Encode the private key
[Convert]::ToBase64String([IO.File]::ReadAllBytes("gitpilot.2024-01-15.private-key.pem")) | Out-File private-key-encoded.txt

# Display the encoded key
Get-Content private-key-encoded.txt
```

Copy the entire encoded string - you'll need it for configuration.

#### Step 4: Secure Your Private Key

```bash
# Move the original .pem file to a secure location
mkdir -p ~/.gitpilot/keys
mv gitpilot.*.private-key.pem ~/.gitpilot/keys/

# Set secure permissions (Linux/Mac only)
chmod 600 ~/.gitpilot/keys/gitpilot.*.private-key.pem

# Delete the encoded text file (we'll put it in .env)
rm private-key-encoded.txt
```

---

### Part 3: Install the App

Now you need to install the app on your repositories.

#### Step 1: Navigate to Installation Page

**Method 1: From App Settings**
1. Go to your app's settings page
2. Click **"Install App"** in the left sidebar
3. Click **"Install"** next to your account/organization

**Method 2: Direct URL**
```
https://github.com/apps/YOUR-APP-SLUG/installations/new
```
Replace `YOUR-APP-SLUG` with your actual app slug (e.g., `gitpilot-yourname`)

#### Step 2: Select Repositories

You'll see a page asking: **"Where do you want to install GitPilot?"**

Choose one:

**Option A: All repositories**
- ✅ **All repositories** - GitPilot can access all current and future repos
- Easiest option
- Good for personal accounts with trusted repos

**Option B: Only select repositories**
- ⚪ **Only select repositories**
- More secure
- Choose specific repos from the dropdown
- Recommended for organizations

Select the repositories you want GitPilot to access, then click **"Install"**.

#### Step 3: Get Installation ID

After clicking "Install", you'll be redirected to a page. Look at the URL:

```
https://github.com/settings/installations/12345678
                                            ^^^^^^^^
                                            This is your Installation ID
```

Copy this **Installation ID** - you'll need it for configuration.

**Alternative way to find it:**
1. Go to https://github.com/settings/installations
2. Click **"Configure"** next to your GitPilot app
3. Look at the URL for the installation ID

---

### Part 4: Configure GitPilot

Now let's configure GitPilot to use your GitHub App.

#### Step 1: Create/Edit .env File

In your GitPilot directory:

```bash
# Copy the template if you haven't already
cp .env.template .env

# Edit the file
nano .env  # or use your preferred editor
```

#### Step 2: Add GitHub App Configuration

Add these lines to your `.env` file:

```bash
# =============================================================================
# GitHub App Configuration
# =============================================================================

# Set authentication mode to 'app' or 'hybrid'
GITPILOT_GITHUB_AUTH_MODE=app

# Your GitHub App ID (from Step 2.2)
GITPILOT_GH_APP_ID=123456

# Installation ID (from Step 3.3)
GITPILOT_GH_APP_INSTALLATION_ID=12345678

# Base64-encoded private key (from Step 2.3)
GITPILOT_GH_APP_PRIVATE_KEY_BASE64=LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0tLQpNSUlFcEFJQkFB...

# App Client ID (from Step 2.2)
GITPILOT_GH_APP_CLIENT_ID=Iv1.a1b2c3d4e5f6g7h8

# App Client Secret (from Step 2.2)
GITPILOT_GH_APP_CLIENT_SECRET=abc123def456ghi789jkl012mno345pqr678stu901

# App Slug (your app name in URL)
GITPILOT_GH_APP_SLUG=gitpilot-yourname
```

**📝 Example Complete Configuration:**

```bash
GITPILOT_GITHUB_AUTH_MODE=app
GITPILOT_GH_APP_ID=123456
GITPILOT_GH_APP_INSTALLATION_ID=12345678
GITPILOT_GH_APP_PRIVATE_KEY_BASE64=LS0tLS1CRUdJTi....(very long string)
GITPILOT_GH_APP_CLIENT_ID=Iv1.a1b2c3d4e5f6g7h8
GITPILOT_GH_APP_CLIENT_SECRET=abc123def456ghi789jkl012mno345pqr678stu901
GITPILOT_GH_APP_SLUG=gitpilot-yourname

# Also configure your LLM provider
GITPILOT_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

#### Step 3: Verify Configuration

Save the `.env` file and verify it's correct:

```bash
# Check that the file exists and has content
cat .env | grep GITPILOT_GH_APP

# You should see your configuration (without showing secrets in production)
```

---

## Testing Your Setup

Let's verify everything works!

### Test 1: Check Authentication Status

```bash
# Start GitPilot
gitpilot

# In another terminal, check status
curl http://localhost:8000/api/auth/status | jq
```

You should see:
```json
{
  "authenticated": true,
  "auth_mode": "app",
  "has_user_token": false,
  "has_app_config": true,
  "setup_completed": false,
  "username": null
}
```

### Test 2: List Repositories

```bash
# List repositories accessible via the app
curl http://localhost:8000/api/repos | jq
```

You should see a JSON array of the repositories where you installed the app:

```json
[
  {
    "id": 123456789,
    "name": "my-repo",
    "full_name": "yourusername/my-repo",
    "private": true,
    "owner": "yourusername"
  }
]
```

### Test 3: Access the Web UI

1. Open your browser to http://localhost:8000
2. You should see the GitPilot interface (not the welcome page)
3. Click on "Workspace" tab
4. You should see your repositories in the selector

✅ **If all tests pass, your GitHub App is working correctly!**

---

## Troubleshooting

### Problem: "GitHub App authentication failed"

**Possible causes:**
1. **Wrong App ID or Installation ID**
   - Go to https://github.com/settings/apps
   - Click on your app
   - Verify the App ID at the top
   - Click "Install App" → "Configure" → Check URL for Installation ID

2. **Invalid Private Key**
   - Make sure you copied the entire base64 string
   - No line breaks or spaces
   - Try re-encoding: `cat key.pem | base64 -w 0`

3. **App not installed on repositories**
   - Go to https://github.com/settings/installations
   - Click "Configure" next to your app
   - Make sure repositories are selected

### Problem: "No repositories found"

**Solution:**
1. Check app installation:
   ```bash
   # This should show repos
   curl http://localhost:8000/api/repos
   ```

2. Verify app has permissions:
   - Go to your app settings
   - Check that "Contents: Read and write" is set

3. Make sure app is installed on repos:
   - https://github.com/settings/installations
   - Configure your app
   - Add repositories

### Problem: "Permission denied" on repository operations

**Solution:**
1. Check repository permissions in app settings
2. Make sure "Contents: Read and write" is enabled
3. Reinstall the app if you changed permissions:
   - Uninstall: Settings → Installations → Configure → Uninstall
   - Reinstall: Go to installation page again

### Problem: "Client secret is invalid"

**Solution:**
1. Generate a new client secret:
   - Go to your app settings
   - Find "Client secrets" section
   - Click "Generate a new client secret"
   - Copy it immediately and update `.env`

2. Restart GitPilot after updating `.env`

### Problem: Private key has line breaks in .env

**Solution:**

The base64 string should be one continuous line. If you see errors:

```bash
# Re-encode without line breaks
cat your-key.pem | base64 -w 0

# On Mac (base64 doesn't have -w option):
cat your-key.pem | base64 | tr -d '\n'
```

Copy the output and paste it as a single line in `.env`.

---

## Security Best Practices

### 🔒 Protecting Your Private Key

1. **Never commit to Git:**
   ```bash
   # Add to .gitignore
   echo ".env" >> .gitignore
   echo "*.pem" >> .gitignore
   ```

2. **Use environment variables in production:**
   ```bash
   # Instead of .env file, use:
   export GITPILOT_GH_APP_PRIVATE_KEY_BASE64="..."
   ```

3. **Rotate keys regularly:**
   - Generate new private key every 90 days
   - Delete old keys from GitHub

4. **Use secret management in production:**
   - AWS Secrets Manager
   - HashiCorp Vault
   - GitHub Secrets (for Actions)

### 🛡️ App Permissions

1. **Principle of Least Privilege:**
   - Only grant permissions GitPilot actually needs
   - Review permissions periodically

2. **Repository Selection:**
   - Don't use "All repositories" in production
   - Select only necessary repos

3. **Monitor Access:**
   - Check app activity: https://github.com/settings/apps/YOUR-APP/advanced
   - Review audit logs for suspicious activity

### 👥 Team Access

1. **Organization Apps:**
   - Create app at organization level for team use
   - Set up repository restrictions

2. **Access Control:**
   - Limit who can modify app settings
   - Use GitHub's org permissions

---

## Next Steps

Now that you have your GitHub App configured:

1. **Add User OAuth** (Optional - for hybrid mode):
   - Follow the [User OAuth Setup guide](../AUTHENTICATION.md#user-oauth-setup)
   - Set `GITPILOT_GITHUB_AUTH_MODE=hybrid`

2. **Configure LLM Provider:**
   - Set up OpenAI, Claude, or other LLM
   - See `.env.template` for options

3. **Start Using GitPilot:**
   ```bash
   # Start the server
   gitpilot

   # Open browser
   open http://localhost:8000
   ```

4. **Deploy to Production:**
   - Update callback URLs to your domain
   - Use environment variables for secrets
   - Set up HTTPS
   - Configure firewall rules

---

## Additional Resources

- **GitHub Apps Documentation**: https://docs.github.com/en/apps
- **GitPilot Authentication Guide**: [AUTHENTICATION.md](../AUTHENTICATION.md)
- **GitHub API Reference**: https://docs.github.com/en/rest
- **Security Best Practices**: https://docs.github.com/en/apps/creating-github-apps/setting-up-a-github-app/best-practices-for-creating-a-github-app

---

## Support

If you run into issues:

1. **Check the logs:**
   ```bash
   gitpilot 2>&1 | tee gitpilot.log
   ```

2. **Verify configuration:**
   ```bash
   gitpilot whoami
   ```

3. **Test GitHub connectivity:**
   ```bash
   curl -H "Authorization: Bearer $(cat ~/.gitpilot/token)" https://api.github.com/user
   ```

4. **Get help:**
   - GitHub Issues: https://github.com/ruslanmv/gitpilot/issues
   - Documentation: https://github.com/ruslanmv/gitpilot#readme

---

## Summary Checklist

Use this checklist to ensure you've completed all steps:

- [ ] Created GitHub App with unique name
- [ ] Set Homepage URL and Callback URL
- [ ] Configured permissions (Contents, Issues, Pull Requests)
- [ ] Generated private key and saved securely
- [ ] Copied App ID, Client ID, Client Secret, and App Slug
- [ ] Base64-encoded the private key
- [ ] Installed app on repositories
- [ ] Got Installation ID from URL
- [ ] Created/updated `.env` file with all values
- [ ] Tested authentication status
- [ ] Verified repository access
- [ ] Accessed web UI successfully
- [ ] Added `.env` and `*.pem` to `.gitignore`

✅ **All done? You're ready to use GitPilot with GitHub App authentication!**

---

**Last Updated:** 2025-11-16
**GitPilot Version:** 0.1.1
