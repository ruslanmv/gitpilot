# Fix GitHub App Permissions

Your GitHub App "GitPilota" currently shows:
- ❌ **No permissions**
- ❌ **No repositories**

This guide will help you fix this issue so GitPilot can access your repositories.

---

## Problem

When you see "No permissions" and "No repositories" in your GitHub App installation, it means:
1. The app was created without the necessary permissions
2. No repositories have been selected for the app to access

GitPilot needs these permissions to read code, create commits, and manage issues.

---

## Solution: Configure Permissions and Install

### Step 1: Update GitHub App Permissions

1. Go to your GitHub App settings:
   ```
   https://github.com/settings/apps/gitpilota
   ```

2. Click **"Permissions & events"** in the left sidebar

3. Scroll to **"Repository permissions"** section

4. Configure these permissions:

   | Permission | Access Level | Why Needed |
   |------------|--------------|------------|
   | **Contents** | **Read and write** | Read files, create commits, push changes |
   | **Issues** | **Read and write** | Create and comment on issues |
   | **Pull requests** | **Read and write** | Create and review pull requests |
   | **Metadata** | **Read-only** | (Auto-selected) Basic repo info |

5. **Save changes** at the bottom of the page

   ⚠️ **IMPORTANT:** You'll see a warning that says:
   ```
   "This will affect all current installations of this GitHub App"
   ```
   Click **"Save changes"** to confirm.

---

### Step 2: Install App on Your Repositories

After updating permissions, install the app:

#### Option A: Install from GitHub

1. Go to:
   ```
   https://github.com/apps/gitpilota
   ```

2. Click the green **"Install"** button (or **"Configure"** if already installed)

3. Choose where to install:
   - **Only select repositories** - Choose specific repos (recommended)
   - **All repositories** - Give access to all your repos

4. Select the repositories you want GitPilot to access

5. Click **"Install"** or **"Save"**

#### Option B: Install from GitPilot UI

1. Open GitPilot in your browser: `http://localhost:8000`

2. On the welcome page, click **"Install GitHub App"** button

3. Follow the same steps as Option A above

---

### Step 3: Get Your Credentials

After installation, you need three pieces of information:

#### 3.1 - App ID

1. Go to: https://github.com/settings/apps/gitpilota
2. Look at the top of the page
3. You'll see: **"App ID: 2313985"**
4. Copy this number

#### 3.2 - Installation ID

1. Go to: https://github.com/settings/installations
2. Click on **"GitPilota"**
3. Look at the URL in your browser:
   ```
   https://github.com/settings/installations/12345678
   ```
4. The number at the end is your Installation ID
5. Copy this number

#### 3.3 - Private Key

If you don't have the private key file:

1. Go to: https://github.com/settings/apps/gitpilota
2. Scroll to **"Private keys"** section
3. Click **"Generate a private key"**
4. A `.pem` file will download
5. Open it with a text editor
6. Copy the entire contents (including `-----BEGIN RSA PRIVATE KEY-----` and `-----END RSA PRIVATE KEY-----`)

---

### Step 4: Configure GitPilot

1. Open GitPilot: `http://localhost:8000`

2. You'll see the welcome page

3. Click **"Setup GitHub App"**

4. Click **"Configure Credentials"**

5. Fill in the form:
   - **App ID**: `2313985` (from Step 3.1)
   - **Installation ID**: Your installation ID (from Step 3.2)
   - **Private Key**: Paste the entire `.pem` file contents (from Step 3.3)

6. Click **"Save & Continue"**

7. GitPilot will verify the credentials with GitHub

8. If successful, you'll be taken to the main app! 🎉

---

## Verify It Works

After configuration, verify everything is working:

1. In GitPilot, you should see a repository selector
2. Click it - you should see the repositories you granted access to
3. Select a repository
4. You should be able to browse files and use GitPilot features

---

## Common Issues

### "Failed to authenticate with GitHub"

**Cause:** Permissions not saved or installation incomplete

**Fix:**
1. Go back to Step 1 and verify permissions are saved
2. Make sure you clicked "Save changes" after setting permissions
3. Reinstall the app (Step 2)

### "No repositories shown in GitPilot"

**Cause:** No repositories selected during installation

**Fix:**
1. Go to: https://github.com/settings/installations
2. Click **"Configure"** next to GitPilota
3. Select the repositories you want to access
4. Click **"Save"**

### "Installation ID not found"

**Cause:** App not installed on your account

**Fix:**
1. Follow Step 2 to install the app
2. Make sure installation is complete before getting Installation ID

---

## Summary

Quick checklist:

- [ ] Update app permissions (Contents, Issues, PRs = Read & write)
- [ ] Save permission changes
- [ ] Install app on repositories
- [ ] Get App ID (2313985)
- [ ] Get Installation ID (from installations URL)
- [ ] Generate/download private key
- [ ] Enter credentials in GitPilot web form
- [ ] Verify repositories are accessible

---

## Need Help?

If you're still having issues:

1. Check the permissions are exactly:
   - Contents: **Read and write**
   - Issues: **Read and write**
   - Pull requests: **Read and write**

2. Make sure the app is installed and you selected at least one repository

3. Verify the Installation ID matches the one in the URL when you view the installation

4. Make sure the private key is the complete `.pem` file including the header and footer lines

---

**After following this guide, your GitHub App will have the correct permissions and GitPilot will be able to access your repositories!** ✅
