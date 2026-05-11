# Vercel Frontend Setup Guide

This guide explains how to configure your GitPilot frontend on Vercel to connect to your backend running on Render (or any other platform).

## Overview

GitPilot uses a split architecture:
- **Frontend**: React/Vite app deployed on Vercel (this guide)
- **Backend**: FastAPI Python server deployed on Render (or other platform)

The frontend needs to know the backend URL through an environment variable.

---

## Step-by-Step Configuration

### 1. Deploy Backend First

Before configuring Vercel, ensure your backend is deployed and running. You should have:

- Backend URL (e.g., `https://gitpilot-backend-latest.onrender.com`)
- Backend health endpoint working: `https://your-backend-url.onrender.com/api/health`

### 2. Configure Vercel Environment Variable

#### Option A: Using Vercel Dashboard

1. **Go to your Vercel project**: https://vercel.com/dashboard
2. **Select your GitPilot project**
3. **Navigate to**: Settings → Environment Variables
4. **Add new environment variable**:
   - **Name**: `VITE_BACKEND_URL`
   - **Value**: `https://your-backend-url.onrender.com` (without trailing slash)
   - **Environment**: Select all three: Production, Preview, Development
5. **Click "Save"**

#### Option B: Using Vercel CLI

```bash
# Set environment variable for all environments
vercel env add VITE_BACKEND_URL

# When prompted, enter:
# Value: https://your-backend-url.onrender.com
# Environments: Production, Preview, Development (select all)
```

### 3. Redeploy Your Frontend

After adding the environment variable, you must redeploy for it to take effect:

#### Option A: Using Vercel Dashboard
1. Go to **Deployments** tab
2. Find the latest deployment
3. Click the three dots (⋯) → **Redeploy**
4. Select "Use existing Build Cache" or "Rebuild with same settings"

#### Option B: Using Git Push
```bash
# Make a small change and push
git commit --allow-empty -m "Trigger Vercel redeploy with env vars"
git push origin main
```

#### Option C: Using Vercel CLI
```bash
vercel --prod
```

---

## Verifying the Configuration

### Check Environment Variable

After deployment completes:

1. **Visit your Vercel app**: `https://your-project.vercel.app`
2. **Open browser console** (F12 → Console tab)
3. **Run**:
   ```javascript
   console.log(import.meta.env.VITE_BACKEND_URL)
   ```
4. **Should see**: `https://your-backend-url.onrender.com`

### Test Backend Connection

1. **Try to login** to your GitPilot app
2. **Check browser Network tab** (F12 → Network)
3. **Look for API calls** to `/api/auth/*`
4. **Verify they're going to**: `https://your-backend-url.onrender.com/api/auth/*`

If you see calls going to `https://your-project.vercel.app/api/auth/*` instead, the environment variable is not set correctly.

---

## Common Issues

### Issue 1: "Backend not reachable" Error

**Symptoms**: Login shows error: "Backend not reachable. VITE_BACKEND_URL environment variable is not configured."

**Solution**:
- Check that `VITE_BACKEND_URL` is set in Vercel dashboard
- Make sure you redeployed after adding the variable
- Verify the variable value doesn't have a trailing slash

### Issue 2: JSON Parse Error

**Symptoms**: Error like "Unexpected token 'T', 'The page c'... is not valid JSON"

**Cause**: Frontend is trying to call APIs on Vercel (which has no backend) because `VITE_BACKEND_URL` is not set.

**Solution**:
1. Set `VITE_BACKEND_URL` in Vercel
2. Redeploy
3. Clear browser cache and try again

### Issue 3: CORS Errors

**Symptoms**: Browser console shows: "blocked by CORS policy"

**Solution**: Update your backend's `CORS_ORIGINS` environment variable in Render:
```bash
CORS_ORIGINS=https://your-project.vercel.app,https://your-project-*.vercel.app
```

The `*` pattern allows preview deployments to work as well.

### Issue 4: Changes Not Reflecting

**Symptoms**: Environment variable is set but app still shows old behavior

**Solution**:
1. **Hard refresh** in browser: Ctrl+Shift+R (Windows) or Cmd+Shift+R (Mac)
2. **Clear browser cache** completely
3. **Verify deployment completed**: Check Vercel Deployments tab for "Ready" status
4. **Check build logs** for any errors during build

---

## Complete Environment Variable Checklist

### Vercel (Frontend)
- [x] `VITE_BACKEND_URL` = `https://your-backend-url.onrender.com`

### Render (Backend)
- [x] `GITHUB_CLIENT_ID` = Your GitHub OAuth App Client ID
- [x] `GITHUB_CLIENT_SECRET` = Your GitHub OAuth App Client Secret
- [x] `OPENAI_API_KEY` = Your OpenAI API key (or `ANTHROPIC_API_KEY`)
- [x] `CORS_ORIGINS` = `https://your-project.vercel.app,https://your-project-*.vercel.app`

---

## Example: Complete Setup

Here's a real example configuration:

### Render Backend
```
Service URL: https://gitpilot-backend-abc123.onrender.com

Environment Variables:
GITHUB_CLIENT_ID=Iv1.a1b2c3d4e5f6g7h8
GITHUB_CLIENT_SECRET=1234567890abcdef1234567890abcdef12345678
OPENAI_API_KEY=sk-proj-abc123...xyz789
CORS_ORIGINS=https://gitpilot-frontend.vercel.app,https://gitpilot-frontend-*.vercel.app
```

### Vercel Frontend
```
Production URL: https://gitpilot-frontend.vercel.app

Environment Variables:
VITE_BACKEND_URL=https://gitpilot-backend-abc123.onrender.com
```

---

## Testing End-to-End

After configuration:

1. **Visit**: `https://your-project.vercel.app`
2. **Click**: "Sign in with GitHub"
3. **Verify**: Redirected to GitHub OAuth
4. **Authorize**: Grant permissions
5. **Success**: You should be logged in and see your repositories

If any step fails, check the browser console and network tab for specific errors.

---

## Advanced: Preview Deployments

Vercel creates preview deployments for every branch/PR. To make these work:

1. **Backend CORS must allow**: `https://your-project-*.vercel.app`
2. **Preview deployments inherit**: Production environment variables
3. **No changes needed**: Preview deployments automatically use the same `VITE_BACKEND_URL`

---

## Need Help?

If you're still having issues:

1. **Check Vercel build logs**: Settings → Deployments → [Your deployment] → Build Logs
2. **Check Render backend logs**: Dashboard → [Your service] → Logs
3. **Verify backend health**: `curl https://your-backend-url.onrender.com/api/health`
4. **Check this repo's issues**: https://github.com/ruslanmv/gitpilot/issues

---

## Summary

**Quick checklist to get Vercel frontend working:**

- [ ] Backend deployed and health check passing
- [ ] `VITE_BACKEND_URL` set in Vercel (all environments)
- [ ] `CORS_ORIGINS` set in Render backend (includes Vercel URL)
- [ ] Frontend redeployed after adding env var
- [ ] Browser cache cleared
- [ ] Login flow works end-to-end

That's it! Your GitPilot frontend on Vercel should now successfully connect to your backend.
