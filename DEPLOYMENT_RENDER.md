# Deploying GitPilot with Render Backend + Vercel Frontend

This guide explains how to deploy GitPilot with:
- **Backend**: Python FastAPI on Render (https://render.com)
- **Frontend**: React/Vite on Vercel (https://vercel.com)

## Why This Setup?

✅ **Better separation of concerns** - Frontend and backend scale independently
✅ **Cost effective** - Render has generous free tier for Python apps
✅ **Easier debugging** - Backend logs separate from frontend
✅ **Flexible** - Can switch providers independently

## 🚀 Quick Start

### 1. Deploy Backend to Render

#### Option A: Using Render Blueprint (Easiest)

1. **Push this repo to GitHub** (if not already)

2. **Go to Render Dashboard**: https://dashboard.render.com

3. **Create New Web Service**:
   - Click "New +" → "Web Service"
   - Connect your GitHub repository
   - Render will detect `render.yaml` automatically

4. **Configure Environment Variables** in Render Dashboard:
   ```
   GITHUB_CLIENT_ID=your_github_oauth_client_id
   GITHUB_CLIENT_SECRET=your_github_oauth_client_secret
   OPENAI_API_KEY=your_openai_api_key (or ANTHROPIC_API_KEY)
   CORS_ORIGINS=https://your-app.vercel.app,http://localhost:5173
   ```

5. **Deploy**: Click "Create Web Service"

6. **Note your backend URL**: `https://gitpilot-backend.onrender.com`

#### Option B: Manual Setup

1. **New Web Service** in Render Dashboard
2. **Connect GitHub repo**
3. **Configure**:
   - **Name**: `gitpilot-backend`
   - **Environment**: `Python 3`
   - **Build Command**: `pip install uv && uv sync --all-extras`
   - **Start Command**: `uv run gitpilot serve --host 0.0.0.0 --port $PORT`
   - **Health Check Path**: `/api/health`
4. **Set environment variables** (same as above)
5. **Deploy**

### 2. Deploy Frontend to Vercel

#### Update Frontend Configuration

Before deploying, you need to configure the backend URL:

1. **Create `.env.production` in frontend directory**:
   ```bash
   VITE_BACKEND_URL=https://gitpilot-backend.onrender.com
   ```

2. **Update API client** (see changes below)

3. **Push to GitHub**

#### Deploy to Vercel

1. **Go to Vercel Dashboard**: https://vercel.com/dashboard

2. **Import Project**:
   - Click "New Project"
   - Import your GitHub repository

3. **Configure Build Settings**:
   - **Framework Preset**: Vite
   - **Root Directory**: `frontend`
   - **Build Command**: `npm run build`
   - **Output Directory**: `dist`

4. **Environment Variables**:
   ```
   VITE_BACKEND_URL=https://gitpilot-backend.onrender.com
   ```

5. **Deploy**: Click "Deploy"

## 🔧 Required Code Changes

### Frontend: Update API Client

You'll need to update `frontend/utils/api.js` to use the backend URL from environment variables instead of relative paths.

**Create/Update** `frontend/utils/api.js`:
```javascript
// Get backend URL from environment or use relative path (for local dev)
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || '';

export const API_BASE = BACKEND_URL;

export const getHeaders = () => ({
  'Content-Type': 'application/json',
  'Authorization': `Bearer ${localStorage.getItem('github_token') || ''}`,
});

// Helper to construct full API URLs
export const apiUrl = (path) => {
  // Ensure path starts with /
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `${API_BASE}${cleanPath}`;
};
```

**Update API calls** to use `apiUrl()`:
```javascript
// Before:
fetch('/api/chat/plan', { ... })

// After:
import { apiUrl, getHeaders } from '../utils/api.js';
fetch(apiUrl('/api/chat/plan'), { headers: getHeaders(), ... })
```

### Backend: Enable CORS

Update your FastAPI app to allow cross-origin requests from Vercel:

**In `gitpilot/api.py`** (or wherever your FastAPI app is):
```python
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI()

# CORS configuration
allowed_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## 📝 Environment Variables Reference

### Render (Backend)

Required:
- `GITHUB_CLIENT_ID` - GitHub OAuth app client ID
- `GITHUB_CLIENT_SECRET` - GitHub OAuth app secret
- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` - LLM provider key
- `CORS_ORIGINS` - Comma-separated list of allowed origins

Optional:
- `DEFAULT_LLM_PROVIDER` - Default to `openai` or `anthropic`
- `DEFAULT_LLM_MODEL` - Default model name
- `PORT` - Automatically set by Render

### Vercel (Frontend)

Required:
- `VITE_BACKEND_URL` - Full URL to Render backend (e.g., `https://gitpilot-backend.onrender.com`)

## 🔍 Testing the Setup

### Test Backend on Render

```bash
# Health check
curl https://gitpilot-backend.onrender.com/api/health

# Should return: {"status": "healthy"}
```

### Test Frontend Locally with Render Backend

```bash
# In frontend directory
echo "VITE_BACKEND_URL=https://gitpilot-backend.onrender.com" > .env.local
npm run dev
```

Visit http://localhost:5173 - it should connect to your Render backend!

### Test Production

Visit your Vercel URL - everything should work end-to-end!

## 💰 Cost Estimate

**Free Tier:**
- Render: 750 hours/month free (Web Service)
- Vercel: 100 GB bandwidth/month free

**Paid (if needed):**
- Render Starter: $7/month (more CPU, always-on)
- Vercel Pro: $20/month (more bandwidth, team features)

## 🐛 Troubleshooting

### CORS Errors

**Problem**: `Access to fetch has been blocked by CORS policy`

**Solution**:
1. Check `CORS_ORIGINS` in Render includes your Vercel URL
2. Restart Render service after updating env vars

### Backend Cold Start

**Problem**: First request takes 30+ seconds (Render free tier)

**Solution**:
- Upgrade to Render Starter ($7/mo) for always-on
- Or implement a health check ping service

### Environment Variables Not Working

**Problem**: `VITE_BACKEND_URL` is undefined

**Solution**:
- Env vars in Vite must start with `VITE_`
- Rebuild frontend after changing env vars
- Check Vercel dashboard → Settings → Environment Variables

## 📚 Next Steps

1. ✅ Deploy backend to Render
2. ✅ Update frontend API client
3. ✅ Deploy frontend to Vercel
4. ✅ Test end-to-end
5. 🔐 Set up custom domain (optional)
6. 📊 Set up monitoring/logging (optional)

## Alternative: All-in-One Render

You can also deploy BOTH frontend and backend on Render:
- Backend: Web Service (as above)
- Frontend: Static Site (pointing to `frontend/dist`)

This simplifies deployment but loses Vercel's edge CDN benefits.
