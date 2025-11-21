# Configure GitPilota GitHub App (SaaS Model)

This guide shows how to configure your GitPilota GitHub App to work like Claude Code - where users install a central app instead of creating their own.

---

## Current Issues to Fix

Your GitPilota app currently shows:
```
❌ Permissions: No permissions
❌ Repository access: No repositories
```

Let's fix this step-by-step.

---

## Step 1: Configure Permissions

Go to your app settings:
```
https://github.com/settings/apps/gitpilota
```

### 1.1 - Repository Permissions

Click **"Permissions & events"** in the sidebar, then scroll to **"Repository permissions"**:

| Permission | Access Level | Required |
|------------|--------------|----------|
| **Contents** | **Read and write** | ✅ Yes |
| **Issues** | **Read and write** | ✅ Yes |
| **Pull requests** | **Read and write** | ✅ Yes |
| **Metadata** | **Read-only** | ✅ Auto-selected |

**How to set:**
1. Find "Repository permissions" section
2. Click dropdown next to **"Contents"** → Select **"Read and write"**
3. Click dropdown next to **"Issues"** → Select **"Read and write"**
4. Click dropdown next to **"Pull requests"** → Select **"Read and write"**
5. Scroll to bottom and click **"Save changes"**

---

## Step 2: Configure OAuth (For Web Authentication)

Still on the GitPilota settings page:

### 2.1 - Callback URL

Find **"Callback URL"** field and set to:
```
http://localhost:8000/auth/github/callback
```

**For production**, update to your domain:
```
https://your-domain.com/auth/github/callback
```

### 2.2 - Enable OAuth Features

Check these boxes:
- ✅ **"Request user authorization (OAuth) during installation"**
- ✅ **"Enable Device Flow"** (optional, for CLI)

### 2.3 - Generate Client Secret

1. Scroll to **"Client secrets"** section
2. Click **"Generate a new client secret"**
3. **IMPORTANT:** Copy the secret immediately (you won't see it again)
4. Save it securely

### 2.4 - Note Your Credentials

Copy these values (you'll need them for .env configuration):

| Field | Where to Find | Example |
|-------|---------------|---------|
| **App ID** | Top of page, under app name | `2313985` |
| **Client ID** | Basic information section | `Iv23litmRp80Z6wmlyRn` |
| **Client Secret** | Just generated above | `abc123def456...` |

---

## Step 3: Generate Private Key (For Backend)

The backend needs a private key to authenticate as the app.

### 3.1 - Generate Key

1. Scroll to **"Private keys"** section
2. Click **"Generate a private key"**
3. A `.pem` file will download (e.g., `gitpilota.2024-01-20.private-key.pem`)

### 3.2 - Save Key Securely

**⚠️ IMPORTANT:**
- This key allows full access to your app's capabilities
- Never commit it to Git
- Store it in a secure location
- Never share it publicly

---

## Step 4: Configure GitPilot Backend

Update your `.env` file with the app credentials:

### 4.1 - Base64 Encode Private Key

**On Linux/Mac:**
```bash
base64 -i gitpilota.2024-01-20.private-key.pem | tr -d '\n'
```

**On Windows (PowerShell):**
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("gitpilota.2024-01-20.private-key.pem"))
```

Copy the output (one long string).

### 4.2 - Update .env File

Create or edit `/home/user/gitpilot/.env`:

```bash
# GitHub App Configuration (SaaS Model)
GITPILOT_GITHUB_AUTH_MODE=app

# Your GitPilota App Credentials
GITPILOT_GH_APP_ID=2313985
GITPILOT_GH_APP_CLIENT_ID=Iv23litmRp80Z6wmlyRn
GITPILOT_GH_APP_CLIENT_SECRET=your_client_secret_here
GITPILOT_GH_APP_PRIVATE_KEY_BASE64=your_base64_encoded_private_key_here
GITPILOT_GH_APP_SLUG=gitpilota

# Note: Installation ID is set per-user after they install the app
# The app stores this automatically during OAuth callback
```

**Important:**
- Replace `your_client_secret_here` with the Client Secret from Step 2.3
- Replace `your_base64_encoded_private_key_here` with the base64 string from Step 4.1

---

## Step 5: Test the Installation Flow

### 5.1 - Start GitPilot

```bash
cd /home/user/gitpilot
gitpilot  # or python -m gitpilot
```

### 5.2 - Open in Browser

```
http://localhost:8000
```

### 5.3 - Install App

1. Click **"Install GitHub App"** button
2. You'll be redirected to GitHub
3. Select repositories to grant access:
   - **All repositories** (simplest for testing)
   - **Only select repositories** (recommended for production)
4. Click **"Install & Authorize"**
5. You'll be redirected back to GitPilot
6. You should now see your repositories! 🎉

---

## Verification Checklist

After configuration, verify everything works:

- [ ] App has correct permissions (Contents, Issues, PRs = Read & write)
- [ ] Callback URL is configured
- [ ] OAuth is enabled during installation
- [ ] Client secret is generated and saved
- [ ] Private key is generated and base64 encoded
- [ ] .env file is configured with all credentials
- [ ] GitPilot server is running
- [ ] "Install GitHub App" button redirects to GitHub
- [ ] After installation, redirected back to GitPilot
- [ ] Can see repositories in the selector
- [ ] Can browse files in selected repository

---

## How It Works (SaaS Model)

```
┌─────────────┐
│    User     │
└──────┬──────┘
       │ 1. Clicks "Install GitHub App"
       ▼
┌─────────────────────────────────────┐
│ https://github.com/apps/gitpilota  │
│ GitHub App Installation Page        │
└──────┬──────────────────────────────┘
       │ 2. User selects repositories
       │ 3. Clicks "Install & Authorize"
       ▼
┌─────────────────────────────────────┐
│ GitHub OAuth Flow                   │
│ - Generates authorization code      │
└──────┬──────────────────────────────┘
       │ 4. Redirects with code
       ▼
┌─────────────────────────────────────┐
│ http://localhost:8000/auth/github/  │
│ callback?code=XXX&installation_id=YY│
└──────┬──────────────────────────────┘
       │ 5. Backend exchanges code for token
       │ 6. Backend stores installation_id
       ▼
┌─────────────────────────────────────┐
│ GitPilot Main App                   │
│ - User is authenticated             │
│ - Can access selected repositories  │
└─────────────────────────────────────┘
```

---

## Multi-User Support

The SaaS model supports multiple users on the same GitPilot instance:

**Backend Configuration (in .env):**
- App ID, Client ID, Client Secret, Private Key (same for all users)

**Per-User Storage:**
- Installation ID (stored when user installs the app)
- Each user gets their own installation with their selected repositories

**How it works:**
1. Admin configures GitPilota app credentials in .env (one time)
2. User A installs app → gets installation_id_A
3. User B installs app → gets installation_id_B
4. Each user sees only their authorized repositories

---

## Troubleshooting

### "No permissions" error

**Solution:**
1. Go to https://github.com/settings/apps/gitpilota
2. Click "Permissions & events"
3. Verify Contents, Issues, PRs are "Read and write"
4. Click "Save changes" at the bottom

### "OAuth client_id not configured" error

**Solution:**
1. Add `GITPILOT_GH_APP_CLIENT_ID` to .env
2. Add `GITPILOT_GH_APP_CLIENT_SECRET` to .env
3. Restart GitPilot

### "No repositories" after installation

**Solution:**
1. Go to https://github.com/settings/installations
2. Click "Configure" next to GitPilota
3. Select repositories you want to access
4. Click "Save"
5. Refresh GitPilot in browser

### Callback doesn't work

**Solution:**
1. Verify callback URL in app settings: `http://localhost:8000/auth/github/callback`
2. Check GitPilot server is running on port 8000
3. Check browser console for errors
4. Verify `GITPILOT_GH_APP_CLIENT_ID` and `GITPILOT_GH_APP_CLIENT_SECRET` are correct

---

## Security Best Practices

1. **Never commit .env to Git**
   - Add `.env` to `.gitignore`
   - Use `.env.template` for documentation

2. **Protect the Private Key**
   - Store securely
   - Never expose in logs or error messages
   - Rotate if compromised

3. **Limit Repository Access**
   - Use "Only select repositories" during installation
   - Grant minimum necessary permissions
   - Review access regularly

4. **Use HTTPS in Production**
   - Callback URL: `https://your-domain.com/auth/github/callback`
   - Never use HTTP for OAuth in production

---

## Next Steps

After configuration:

1. ✅ Test the installation flow
2. ✅ Install on a test repository first
3. ✅ Verify file access works
4. ✅ Test issue creation (if needed)
5. ✅ Test PR creation (if needed)
6. 🎉 Roll out to users!

---

**Your GitPilota app is now configured like Claude Code!** Users can simply install the app and start using GitPilot immediately. 🚀
