# GitPilota OAuth Authentication Setup

This document explains how to set up GitHub OAuth authentication for GitPilota, enabling enterprise-grade user login and repository access control.

## Overview

GitPilota uses a two-layer authentication system:

1. **User Authentication (OAuth)**: Users sign in with their GitHub account via OAuth
2. **Repository Access (GitHub App)**: Users grant the GitPilota GitHub App access to specific repositories

## Prerequisites

- A GitHub account
- Admin access to create a GitHub App
- GitPilota installed and configured

## Setup Process

### Step 1: Create a GitHub App

1. Go to [GitHub Developer Settings](https://github.com/settings/apps)
2. Click **"New GitHub App"**
3. Fill in the required fields:
   - **GitHub App name**: `gitpilota-[your-org]` (must be globally unique)
   - **Homepage URL**: `http://localhost:8000` (or your production URL)
   - **Callback URL**: `http://localhost:8000/auth/callback`
   - **Setup URL** (optional): Leave blank
   - **Webhook**: Uncheck "Active" for local development

4. Set permissions:
   - **Repository permissions**:
     - Contents: Read & Write
     - Issues: Read & Write
     - Pull requests: Read & Write
     - Metadata: Read-only (automatically included)

5. Choose where this app can be installed:
   - Select **"Only on this account"** for personal use
   - Or **"Any account"** for wider distribution

6. Click **"Create GitHub App"**

### Step 2: Configure OAuth Credentials

After creating the app:

1. On the app settings page, note your **App ID**
2. Generate a **Client Secret**:
   - Click **"Generate a new client secret"**
   - Copy the secret immediately (it won't be shown again)
3. Note your **Client ID** (shown on the same page)
4. Generate a **Private Key**:
   - Scroll down to "Private keys"
   - Click **"Generate a private key"**
   - A `.pem` file will be downloaded

### Step 3: Configure Environment Variables

Set up the following environment variables:

```bash
# GitHub OAuth credentials
export GITPILOT_GH_APP_CLIENT_ID="Iv1.xxxxxxxxxxxxx"
export GITPILOT_GH_APP_CLIENT_SECRET="xxxxxxxxxxxxxxxxxxxxx"

# GitHub App credentials
export GITPILOT_GH_APP_ID="123456"
export GITPILOT_GH_APP_SLUG="gitpilota-yourorg"

# Convert private key to base64
export GITPILOT_GH_APP_PRIVATE_KEY_BASE64=$(cat path/to/your-app.pem | base64 -w 0)

# OAuth redirect URI (optional, defaults to http://localhost:8000/auth/callback)
export GITPILOT_OAUTH_REDIRECT_URI="http://localhost:8000/auth/callback"
```

Or add them to your `.env` file:

```env
GITPILOT_GH_APP_CLIENT_ID=Iv1.xxxxxxxxxxxxx
GITPILOT_GH_APP_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxx
GITPILOT_GH_APP_ID=123456
GITPILOT_GH_APP_SLUG=gitpilota-yourorg
GITPILOT_GH_APP_PRIVATE_KEY_BASE64=<base64-encoded-private-key>
GITPILOT_OAUTH_REDIRECT_URI=http://localhost:8000/auth/callback
```

### Step 4: Install the GitHub App

After users log in via OAuth, they need to install your GitHub App to grant repository access:

1. The user logs in to GitPilota using GitHub OAuth
2. They navigate to the GitHub connection panel
3. Click **"Connect with GitHub"** to install the app
4. Select which repositories to grant access to:
   - **All repositories**: Full access
   - **Only select repositories**: Choose specific repos

### Step 5: Configure Installation ID (for server-to-server auth)

After installing the app:

1. Visit `https://github.com/settings/installations`
2. Click on your app installation
3. Note the installation ID from the URL: `https://github.com/settings/installations/INSTALLATION_ID`
4. Set it as an environment variable:

```bash
export GITPILOT_GH_APP_INSTALLATION_ID="12345678"
```

## Authentication Flow

### User Login Flow

```
1. User visits GitPilota → Welcome page shown
2. User clicks "Sign in with GitHub"
3. Redirected to GitHub OAuth authorization
4. User approves access
5. GitHub redirects back to /auth/callback
6. GitPilota creates session and stores access token
7. User is logged in and sees main app
```

### Repository Access Flow

```
1. User installs GitHub App
2. Selects repositories to grant access
3. GitHub creates installation
4. GitPilota uses installation token for API calls
```

## API Endpoints

### Authentication Endpoints

- `GET /api/auth/status` - Check authentication status
- `GET /api/auth/login` - Initiate OAuth login
- `GET /api/auth/callback` - OAuth callback handler
- `POST /api/auth/logout` - Logout user
- `GET /api/auth/user` - Get current user info

### GitHub Connection Endpoints

- `GET /api/github/status` - Check GitHub connection status
- `GET /api/github/app-install-url` - Get app installation URL

## Security Considerations

### Session Management

- Sessions are stored server-side with secure random tokens
- Cookies are HTTP-only and use SameSite protection
- Session tokens expire after 30 days
- CSRF protection using state parameter in OAuth flow

### Token Storage

- Access tokens are stored server-side only
- Never exposed to client-side JavaScript
- Encrypted in transit using HTTPS (production)

### Best Practices

1. **Use HTTPS in production**: Set `secure: true` for cookies
2. **Rotate secrets regularly**: Generate new client secrets periodically
3. **Limit permissions**: Only request necessary GitHub permissions
4. **Monitor access**: Review app installations regularly
5. **Use short-lived tokens**: Consider implementing token refresh

## Production Deployment

For production deployments:

1. Update OAuth callback URL to your production domain:
   ```
   https://gitpilota.yourdomain.com/auth/callback
   ```

2. Update environment variables:
   ```bash
   export GITPILOT_OAUTH_REDIRECT_URI="https://gitpilota.yourdomain.com/auth/callback"
   ```

3. Enable secure cookies in `auth.py`:
   ```python
   response.set_cookie(
       key="gitpilota_session",
       value=session_id,
       max_age=max_age,
       httponly=True,
       secure=True,  # Enable for HTTPS
       samesite="lax",
   )
   ```

4. Use a production-grade session store:
   - Redis for scalability
   - Database for persistence
   - Replace `SessionStore` with production implementation

## Troubleshooting

### "GitHub OAuth not configured" error

**Cause**: Missing OAuth credentials

**Solution**: Ensure `GITPILOT_GH_APP_CLIENT_ID` and `GITPILOT_GH_APP_CLIENT_SECRET` are set

### "Invalid state" error during OAuth callback

**Cause**: CSRF token mismatch or expired

**Solution**:
- Clear browser cookies and try again
- Check that your redirect URI matches exactly
- Ensure server time is synchronized

### "Failed to fetch user information" error

**Cause**: Invalid access token or GitHub API issues

**Solution**:
- Verify your client secret is correct
- Check GitHub API status
- Regenerate client secret if needed

### App installation not working

**Cause**: App slug not configured or app doesn't exist

**Solution**:
- Verify `GITPILOT_GH_APP_SLUG` matches your GitHub App's slug
- Check that the app is public or accessible to users
- Ensure the app installation URL is correct

## Development vs Production

### Development Setup

- Use `http://localhost:8000` as base URL
- Cookies with `secure: false`
- In-memory session storage
- Debug logging enabled

### Production Setup

- Use HTTPS domain
- Cookies with `secure: true`
- Redis or database session storage
- Structured logging
- Rate limiting
- Session cleanup job

## CLI Authentication

For CLI usage, GitPilota also supports Personal Access Token (PAT) authentication:

```bash
# Set PAT for CLI usage
export GITPILOT_GITHUB_TOKEN="ghp_xxxxxxxxxxxxx"

# Or use GitHub CLI authentication
gh auth login
export GITHUB_TOKEN=$(gh auth token)
```

The web UI will prefer OAuth authentication when available, falling back to PAT for API operations.

## Next Steps

After setting up authentication:

1. Configure LLM providers (OpenAI, Claude, Watsonx, or Ollama)
2. Complete the setup wizard
3. Select repositories to work with
4. Start using GitPilota's AI-powered features

## Support

For issues or questions:

- [GitHub Issues](https://github.com/ruslanmv/gitpilota/issues)
- [Documentation](https://github.com/ruslanmv/gitpilota#readme)
