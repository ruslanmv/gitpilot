# Deploying GitPilot Backend to Render

Complete guide for deploying the GitPilot backend to Render using Docker.

## Quick Reference

- ✅ **Correct URL**: `https://gitpilot-backend-latest.onrender.com`
- ❌ **Wrong URL**: `https://gitpilot-backend-latest.onrender.com:8000` (no port!)
- 📝 **Docs**: `https://gitpilot-backend-latest.onrender.com/docs`
- 🏥 **Health**: `https://gitpilot-backend-latest.onrender.com/api/health`

## Step-by-Step Deployment

### 1. Prerequisites

- Docker image published to Docker Hub (use `make publish-container`)
- GitHub OAuth App created
- OpenAI or Anthropic API key

### 2. Create New Web Service on Render

1. Go to https://dashboard.render.com
2. Click **New +** → **Web Service**
3. Choose **Deploy an existing image from a registry**

### 3. Configure Service

#### Basic Settings

- **Name**: `gitpilot-backend` (or your choice)
- **Region**: Choose closest to your users
- **Image URL**: `ruslanmv/gitpilot-backend:latest`
  - Or your Docker Hub username: `yourusername/gitpilot-backend:latest`

#### Instance Settings

- **Instance Type**: Free (for testing) or Starter ($7/month recommended)
- **Auto-Deploy**: Yes (auto-deploys on new image push)

### 4. Environment Variables

**CRITICAL**: Add these environment variables in the Render dashboard:

#### Required Variables

```bash
# GitHub OAuth (Required)
GITHUB_CLIENT_ID=Iv1.xxxxxxxxxxxxx
GITHUB_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# LLM Provider (Required - choose one)
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# OR
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# CORS (Required for Vercel frontend)
CORS_ORIGINS=https://your-app.vercel.app,https://your-app-*.vercel.app
```

#### Optional Variables

```bash
# Default LLM settings
DEFAULT_LLM_PROVIDER=openai
DEFAULT_LLM_MODEL=gpt-4

# Port (Render auto-detects 8000, but you can set it)
PORT=8000
```

### 5. Advanced Settings

#### Health Check Path

Set to: `/api/health`

This tells Render to monitor your backend health using the `/api/health` endpoint.

#### Docker Command (Optional)

Render will use the `CMD` from your Dockerfile:
```bash
uv run gitpilot serve --host 0.0.0.0 --port 8000
```

You can override this in Render if needed.

### 6. Deploy

1. Click **Create Web Service**
2. Wait for deployment (first deploy takes ~2-3 minutes)
3. Watch the logs for any errors

### 7. Verify Deployment

Once deployed, test these endpoints:

```bash
# Health check
curl https://gitpilot-backend-latest.onrender.com/api/health
# Should return: {"status":"healthy","service":"gitpilot-backend"}

# API docs
curl https://gitpilot-backend-latest.onrender.com/docs
# Should return: HTML page with FastAPI Swagger UI

# Auth status
curl https://gitpilot-backend-latest.onrender.com/api/auth/status
# Should return: {"mode":"web"} or {"mode":"device"}
```

Or use the diagnostic script:
```bash
./scripts/test-backend-api.sh https://gitpilot-backend-latest.onrender.com
```

## Common Issues

### Issue 1: "Service Unavailable" (503 Error)

**Symptoms:**
- Render shows "Live" but API returns 503
- Health check fails
- TLS/SSL errors in logs

**Causes:**
1. Backend is crashing on startup
2. Missing environment variables
3. Port mismatch

**Solutions:**

1. **Check Render Logs:**
   ```
   Dashboard → Your Service → Logs
   ```
   Look for:
   - Python import errors
   - Missing environment variable errors
   - Port binding errors

2. **Verify Environment Variables:**
   - All required variables are set
   - No typos in variable names
   - Values are correctly formatted (no extra quotes)

3. **Check Events Tab:**
   - See if deployments are succeeding or failing
   - Check for out-of-memory errors

### Issue 2: CORS Errors

**Symptoms:**
- Frontend shows: "blocked by CORS policy"
- Network tab shows 403 or OPTIONS request failures

**Solution:**

Update `CORS_ORIGINS` environment variable:
```bash
CORS_ORIGINS=https://your-app.vercel.app,https://your-app-*.vercel.app
```

**Important:**
- Use comma-separated list (no spaces)
- Include wildcard `*` for preview deployments
- Must match exactly (https vs http, trailing slashes matter)

### Issue 3: GitHub OAuth Not Working

**Symptoms:**
- Login button returns 404 or 500
- `/api/auth/url` returns error

**Solutions:**

1. **Check GitHub App Settings:**
   - Homepage URL: `https://gitpilot-backend-latest.onrender.com`
   - Callback URL: `https://your-frontend.vercel.app`
   - Or for GitHub OAuth App: `https://gitpilot-backend-latest.onrender.com/api/auth/callback`

2. **Verify Environment Variables:**
   ```bash
   GITHUB_CLIENT_ID=Iv1.xxxxx  # Should start with Iv1.
   GITHUB_CLIENT_SECRET=xxxxx   # Should be 40 characters
   ```

3. **Check Device Flow:**
   If using Device Flow, enable it in GitHub App Settings:
   - Settings → Developer settings → GitHub Apps → Your App
   - General → Identifying and authorizing users
   - ✅ Enable Device Flow

### Issue 4: Port Confusion

**Wrong URLs:**
```bash
❌ https://gitpilot-backend-latest.onrender.com:8000
❌ https://gitpilot-backend-latest.onrender.com:443
```

**Correct URL:**
```bash
✅ https://gitpilot-backend-latest.onrender.com
```

**Why:**
- Render exposes your service on standard HTTPS port (443)
- Your app listens on port 8000 internally
- Render's load balancer forwards 443 → 8000 automatically
- Never include `:8000` in external URLs

### Issue 5: Free Tier Spin Down

**Symptoms:**
- First request after inactivity takes 30-60 seconds
- Subsequent requests are fast

**Cause:**
- Render free tier spins down after 15 minutes of inactivity

**Solutions:**
1. **Upgrade to paid tier** ($7/month) - no spin down
2. **Use external monitoring** (Uptime Robot, Pingdom) to keep service alive
3. **Accept the delay** - explain to users first load may be slow

### Issue 6: Environment Variables Not Loading

**Symptoms:**
- Backend logs show: "GITHUB_CLIENT_ID not found"
- Endpoints return 500 errors

**Solutions:**

1. **Check Variable Names:**
   - Must match exactly (case-sensitive)
   - No extra spaces
   - Example: `GITHUB_CLIENT_ID` not `github_client_id`

2. **Trigger Redeploy:**
   - After adding variables, redeploy is required
   - Dashboard → Your Service → Manual Deploy → Deploy latest commit

3. **Check for Typos:**
   - Compare your variables with `.env.example`
   - Common mistake: `GITHUB_CLIENT_SECRET` vs `GITHUB_SECRET`

## Testing Your Deployment

### Method 1: Using curl

```bash
# Test health
curl https://gitpilot-backend-latest.onrender.com/api/health

# Test auth status
curl https://gitpilot-backend-latest.onrender.com/api/auth/status

# Test CORS (simulate frontend request)
curl -I -X OPTIONS \
  -H "Origin: https://your-app.vercel.app" \
  -H "Access-Control-Request-Method: GET" \
  https://gitpilot-backend-latest.onrender.com/api/health
```

### Method 2: Using Browser

1. Open: `https://gitpilot-backend-latest.onrender.com/docs`
2. You should see FastAPI Swagger UI
3. Try the `/api/health` endpoint
4. Should return: `{"status":"healthy","service":"gitpilot-backend"}`

### Method 3: Using Diagnostic Script

```bash
# Test your backend
./scripts/test-backend-api.sh https://gitpilot-backend-latest.onrender.com

# Should show all tests passing
```

## Monitoring Your Service

### View Logs

```
Render Dashboard → Your Service → Logs
```

Look for:
- Startup messages: "Application startup complete"
- Request logs: "GET /api/health HTTP/1.1 200"
- Error logs: Any Python tracebacks

### Check Metrics

```
Render Dashboard → Your Service → Metrics
```

Monitor:
- CPU usage (should be low, spikes on requests)
- Memory usage (should stay under 512MB)
- Bandwidth (tracks API calls)

### Set Up Alerts (Paid Plans)

Configure alerts for:
- Service down
- High memory usage
- Deploy failures

## Updating Your Deployment

### Method 1: Auto-Deploy (Recommended)

1. Build and push new Docker image:
   ```bash
   make publish-container
   ```

2. Render auto-detects new image and redeploys

### Method 2: Manual Deploy

1. Go to Render Dashboard
2. Click **Manual Deploy** → **Deploy latest commit**
3. Wait for deployment to complete

### Method 3: Trigger from CI/CD

GitHub Actions workflow already configured:
```bash
.github/workflows/dockerhub.yml
```

On release or manual trigger:
1. Builds Docker images
2. Pushes to Docker Hub
3. Render auto-deploys

## Production Checklist

Before going to production:

- [ ] Environment variables all set correctly
- [ ] CORS includes your production frontend URL
- [ ] Health check configured: `/api/health`
- [ ] Logs show no errors
- [ ] All test endpoints return 200 OK
- [ ] GitHub OAuth working end-to-end
- [ ] Upgraded to paid tier (no spin down)
- [ ] Custom domain configured (optional)
- [ ] Monitoring/alerts set up
- [ ] SSL certificate verified (auto by Render)

## Next Steps

After backend is deployed and healthy:

1. **Configure Vercel frontend** (see `VERCEL_SETUP.md`)
2. **Set VITE_BACKEND_URL** to your Render backend URL
3. **Update CORS_ORIGINS** to include Vercel URL
4. **Test login flow** end-to-end
5. **Monitor logs** for any issues

## Get Help

If issues persist:

1. **Check Render Logs** - Most issues are visible here
2. **Run diagnostic script** - `./scripts/test-backend-api.sh`
3. **Review this guide** - Double-check all steps
4. **Check Render Status** - https://status.render.com
5. **Render Support** - https://render.com/docs/support

## Summary

**Key Points:**
- ✅ Use `https://your-service.onrender.com` (NO :8000!)
- ✅ Set all required environment variables
- ✅ Configure CORS to include Vercel frontend
- ✅ Use `/api/health` for health checks
- ✅ Check logs for errors
- ✅ Test with diagnostic script

Your backend should now be live and accessible!
