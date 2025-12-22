# GitPilot Vercel Deployment Guide

This guide explains how to deploy GitPilot as a serverless application on Vercel.

## Overview

GitPilot has been adapted to run on Vercel's serverless platform with the following key changes:

- **Python 3.12 Support**: Updated to work with Vercel's Python runtime
- **Serverless Entry Point**: Created `api/index.py` for Vercel function handling
- **Ephemeral Storage Handling**: Settings persistence adapted for serverless environment
- **Optimized Bundle**: Excluded unnecessary files to stay within Vercel's 250MB limit

## Prerequisites

1. **Vercel Account**: Sign up at [vercel.com](https://vercel.com)
2. **Vercel CLI** (optional): `npm i -g vercel`
3. **GitHub Repository**: Your GitPilot fork or clone

## Quick Deploy

### Option 1: Deploy via Vercel Dashboard (Recommended)

1. **Import Project**
   - Go to [vercel.com/new](https://vercel.com/new)
   - Import your GitPilot repository
   - Vercel will auto-detect the configuration from `vercel.json`

2. **Configure Environment Variables** (see "Required Environment Variables" section below)

3. **Deploy**
   - Click "Deploy"
   - Wait for build to complete (first deploy may take 3-5 minutes)

### Option 2: Deploy via CLI

```bash
# Install Vercel CLI
npm i -g vercel

# Login
vercel login

# Deploy (from project root)
vercel

# Follow prompts to configure
# Deploy to production
vercel --prod
```

## Required Environment Variables

Configure these in Vercel Dashboard → Your Project → Settings → Environment Variables:

### GitHub Authentication (Choose One)

**Option A: Personal Access Token (Easiest)**
```bash
GITPILOT_GITHUB_TOKEN=ghp_your_github_token_here
```

**Option B: GitHub OAuth App**
```bash
GITHUB_CLIENT_ID=your_client_id
GITHUB_CLIENT_SECRET=your_client_secret
GITHUB_REDIRECT_URI=https://your-app.vercel.app/api/auth/callback
```

**Option C: GitHub App**
```bash
GITHUB_APP_ID=your_app_id
GITHUB_APP_SLUG=your_app_slug
GITHUB_PRIVATE_KEY=your_private_key_multiline
```

### LLM Provider Configuration (Choose One or More)

**OpenAI**
```bash
GITPILOT_PROVIDER=openai
OPENAI_API_KEY=sk-your-openai-key
GITPILOT_OPENAI_MODEL=gpt-4o-mini  # optional
```

**Anthropic Claude**
```bash
GITPILOT_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-your-claude-key
GITPILOT_CLAUDE_MODEL=claude-sonnet-4-5  # optional
```

**IBM Watsonx**
```bash
GITPILOT_PROVIDER=watsonx
WATSONX_API_KEY=your_watsonx_key
WATSONX_PROJECT_ID=your_project_id
GITPILOT_WATSONX_MODEL=meta-llama/llama-3-3-70b-instruct  # optional
```

**Ollama** (requires accessible endpoint)
```bash
GITPILOT_PROVIDER=ollama
OLLAMA_BASE_URL=https://your-ollama-endpoint.com
GITPILOT_OLLAMA_MODEL=llama3  # optional
```

### Optional: LangFlow Integration
```bash
GITPILOT_LANGFLOW_URL=https://your-langflow-instance.com
GITPILOT_LANGFLOW_API_KEY=your_langflow_key
GITPILOT_LANGFLOW_PLAN_FLOW_ID=your_flow_id
```

## Important Configuration Notes

### 1. GitHub OAuth Redirect URI

If using GitHub OAuth (Option B above), you **must** update your GitHub OAuth App settings:

1. Go to GitHub → Settings → Developer settings → OAuth Apps
2. Edit your OAuth App
3. Set **Authorization callback URL** to: `https://your-app.vercel.app/api/auth/callback`
4. Replace `your-app.vercel.app` with your actual Vercel domain

### 2. Settings Persistence

**Important**: On Vercel, the filesystem is ephemeral. This means:

- ✅ **Environment variables** are the single source of truth for configuration
- ❌ **Runtime settings changes via API** won't persist between function invocations
- ✅ **Settings API endpoints work** but changes are temporary (request-scoped)

**Recommendation**: Configure all settings via environment variables in Vercel dashboard.

### 3. Function Timeout

Current configuration: **60 seconds** (defined in `vercel.json`)

- This is sufficient for most LLM calls
- If you need longer execution times:
  - Edit `vercel.json` → `functions.api/index.py.maxDuration`
  - Max: 300s (Pro plan), 900s (Enterprise)

### 4. Memory Configuration

Current configuration: **1024 MB** (defined in `vercel.json`)

- Sufficient for most use cases
- If experiencing memory issues with large repositories:
  - Edit `vercel.json` → `functions.api/index.py.memory`
  - Options: 128, 256, 512, 1024, 3008 MB (Pro/Enterprise)

## Vercel-Specific Files

The following files were added/modified for Vercel deployment:

```
gitpilot/
├── api/
│   ├── __init__.py          # NEW: Makes api a package
│   └── index.py             # NEW: Vercel entrypoint
├── vercel.json              # NEW: Vercel configuration
├── .vercelignore            # NEW: Files to exclude from deployment
├── pyproject.toml           # MODIFIED: Python 3.12 support
└── gitpilot/
    └── settings.py          # MODIFIED: Vercel-aware persistence
```

## Architecture

### Request Flow

```
User → Vercel Edge Network → Serverless Function (api/index.py)
                                        ↓
                                  gitpilot.api (FastAPI)
                                        ↓
                        ┌───────────────┴───────────────┐
                        ↓                               ↓
                  API Endpoints                   Static Files
                  (JSON responses)              (React SPA from gitpilot/web)
```

### Static File Serving

- React frontend is served from `gitpilot/web` directory
- FastAPI's `StaticFiles` middleware serves `/assets` and `/static` routes
- SPA routing handled by catch-all route (serves `index.html`)

## Bundle Size Optimization

To stay under Vercel's 250MB function size limit:

### Current Exclusions (via `.vercelignore`)
- Tests and test files
- Documentation (except README)
- Frontend source code (built output in `gitpilot/web` is included)
- Development assets and media files
- Git history and GitHub workflows

### If You Hit the Size Limit

1. **Check bundle size**:
   ```bash
   vercel deploy --debug
   # Look for "Function size" in output
   ```

2. **Reduce dependencies**:
   - Consider removing unused LLM providers from `pyproject.toml`
   - Example: If only using Claude, remove `ibm-watsonx-ai` and `langchain-ibm`

3. **Add more exclusions** in `.vercelignore`:
   ```bash
   # Example: exclude specific provider SDKs you don't use
   **/site-packages/watsonx/**
   **/site-packages/ollama/**
   ```

## Testing Your Deployment

### 1. Check Health

```bash
curl https://your-app.vercel.app/api/settings
```

Should return your current LLM configuration.

### 2. Test Authentication

Visit: `https://your-app.vercel.app/`

The React app should load and prompt for GitHub authentication.

### 3. Test Repository Access

After authenticating, try:
- Listing repositories
- Opening a repository
- Generating a plan (AI feature)

## Common Issues

### Issue: "Module not found" errors

**Cause**: Package imports failing

**Solution**:
- Ensure `gitpilot` is a proper package (has `__init__.py`)
- Check `vercel.json` builds configuration
- Verify `api/index.py` imports work locally

### Issue: "Function size exceeded"

**Cause**: Deployment bundle > 250MB

**Solution**:
- Add more exclusions to `.vercelignore`
- Remove unused dependencies from `pyproject.toml`
- See "Bundle Size Optimization" section above

### Issue: "Settings changes don't persist"

**Cause**: Ephemeral filesystem in serverless environment

**Solution**:
- This is expected behavior
- Configure all settings via environment variables
- Settings API endpoints work but are request-scoped

### Issue: "Timeout errors on AI calls"

**Cause**: Function execution exceeded 60s

**Solution**:
- Increase `maxDuration` in `vercel.json`
- Max 300s on Pro plan, 900s on Enterprise
- Consider breaking long operations into smaller calls

### Issue: GitHub OAuth redirect fails

**Cause**: Redirect URI mismatch

**Solution**:
- Update GitHub OAuth App callback URL to match Vercel domain
- Set `GITHUB_REDIRECT_URI` environment variable correctly
- Format: `https://your-app.vercel.app/api/auth/callback`

## Production Considerations

### 1. Use Environment Variables for Secrets

Never hardcode API keys. Always use Vercel environment variables:
- Set via Dashboard → Settings → Environment Variables
- Use different values for Preview vs Production deployments

### 2. Monitor Function Logs

```bash
vercel logs your-app.vercel.app
# Or via Dashboard → Deployments → [deployment] → Logs
```

### 3. Set Up Custom Domain

1. Vercel Dashboard → Your Project → Settings → Domains
2. Add your domain (e.g., `gitpilot.yourdomain.com`)
3. Update GitHub OAuth callback URLs to use custom domain

### 4. Enable Production Protection

For sensitive deployments:
1. Settings → Deployment Protection
2. Enable Vercel Authentication or Password Protection

## Differences from Local/VPS Deployment

| Feature | Local/VPS | Vercel Serverless |
|---------|-----------|-------------------|
| Settings Persistence | Disk (`~/.gitpilot/settings.json`) | Environment variables only |
| Static Files | Served by FastAPI | Served by FastAPI (from bundle) |
| Python Version | 3.11+ | 3.12 (Vercel runtime) |
| Max Execution Time | Unlimited | 60s (configurable, max 300s/900s) |
| Scaling | Manual | Automatic (per request) |
| Cost | Server costs | Pay-per-invocation (free tier available) |

## Cost Estimation

Vercel Pricing (as of 2024):

**Hobby Plan (Free)**
- 100GB bandwidth/month
- 100 hours serverless function execution/month
- Good for: Personal projects, demos

**Pro Plan ($20/month)**
- 1TB bandwidth
- 1000 hours function execution
- Custom domains, analytics
- Good for: Small teams, production apps

**Enterprise**
- Custom pricing
- Advanced features (longer timeouts, more memory)
- Good for: Large organizations

**For GitPilot typical usage**:
- Most API calls: ~500ms - 2s execution time
- AI planning calls: ~5s - 30s execution time
- Estimate: ~100-500 requests/day fits comfortably in free tier

## Support and Resources

- **Vercel Docs**: https://vercel.com/docs
- **Vercel Python Runtime**: https://vercel.com/docs/functions/runtimes/python
- **FastAPI on Vercel**: https://vercel.com/docs/frameworks/backend/fastapi
- **GitPilot Issues**: https://github.com/ruslanmv/gitpilot/issues

## Next Steps

1. ✅ Deploy to Vercel
2. ✅ Configure environment variables
3. ✅ Test authentication flow
4. ✅ Test AI features with your preferred LLM provider
5. 🎯 (Optional) Set up custom domain
6. 🎯 (Optional) Enable monitoring and alerts
7. 🎯 (Optional) Optimize bundle size if needed

---

**Note**: This is an **experimental** Vercel adaptation. For production use, thoroughly test all features in your specific use case. The main limitation is the ephemeral filesystem - all configuration must be done via environment variables.
