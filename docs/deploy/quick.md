# Quick Deployment Guide: Vercel Frontend + Render Backend

This guide shows how to deploy GitPilot with frontend on Vercel and backend on Render using published Docker images.

## 🎯 Architecture

```
┌──────────────────┐         ┌────────────────────┐
│  Vercel (CDN)    │  HTTPS  │  Render (Cloud)    │
│  Frontend        │────────>│  Backend           │
│  React/Vite      │   API   │  Docker Container  │
└──────────────────┘         └────────────────────┘
```

## 🚀 Quick Start (3 Steps)

### Step 1: Publish Docker Images

```bash
# Set your Docker Hub username
export DOCKERHUB_USERNAME=your-dockerhub-username

# Build and publish containers
make publish-container
```

This will:
- Build both backend and frontend containers
- Tag them with your Docker Hub username
- Push to Docker Hub
- Give you the image URLs to use

**Output:**
```
✅ Successfully published to Docker Hub!

Backend:  docker.io/your-username/gitpilot-backend:latest
Frontend: docker.io/your-username/gitpilot-frontend:latest
```

### Step 2: Deploy Backend on Render

1. **Go to Render**: https://dashboard.render.com

2. **Create Web Service**:
   - Click **New +** → **Web Service**
   - Select "**Deploy an existing image from a registry**"

3. **Configure**:
   - **Image URL**: `docker.io/your-username/gitpilot-backend:latest`
   - **Name**: `gitpilot-backend`
   - **Region**: Choose closest to your users
   - **Instance Type**: Free (or Starter for production)

4. **Environment Variables** (click "Add Environment Variable"):
   ```
   GITHUB_CLIENT_ID=your_github_oauth_client_id
   GITHUB_CLIENT_SECRET=your_github_oauth_secret
   OPENAI_API_KEY=sk-your-openai-key
   CORS_ORIGINS=https://your-vercel-app.vercel.app
   ```

5. **Create Web Service**

6. **Copy Backend URL**:
   ```
   https://gitpilot-backend-xxx.onrender.com
   ```

### Step 3: Configure Vercel Frontend

1. **Go to Vercel**: https://vercel.com/dashboard

2. **Select Your Project** → **Settings** → **Environment Variables**

3. **Add Environment Variable**:
   - **Name**: `VITE_BACKEND_URL`
   - **Value**: `https://gitpilot-backend-xxx.onrender.com` (from Step 2)
   - **Environment**: Production, Preview, Development (all)
   - **Save**

4. **Redeploy**:
   - Go to **Deployments** tab
   - Click latest deployment → **Redeploy**
   - Or push a new commit to trigger auto-deploy

5. **Test**: Visit your Vercel URL and verify it connects to backend

---

## 📋 Full Workflow

```bash
# 1. Build and test locally
make build-container
make run-container

# 2. Test works locally
curl http://localhost:8000/api/health  # Backend
curl http://localhost:3000/            # Frontend

# 3. Stop local containers
make stop-container

# 4. Publish to Docker Hub
export DOCKERHUB_USERNAME=your-username
make publish-container

# 5. Deploy backend on Render (see Step 2 above)

# 6. Configure Vercel with backend URL (see Step 3 above)

# 7. Done! 🎉
```

---

## 🔄 Updating Your Deployment

When you make changes:

### Update Backend:
```bash
# 1. Make code changes
# 2. Rebuild and publish
make publish-container

# 3. Render auto-updates (if "Auto-Deploy" enabled)
#    Or manually: Render Dashboard → Manual Deploy
```

### Update Frontend:
```bash
# 1. Make code changes
# 2. Commit and push to GitHub
git add .
git commit -m "Update frontend"
git push

# 3. Vercel auto-deploys from GitHub
```

---

## 🔍 Verifying Deployment

### Check Backend (Render):
```bash
# Health check
curl https://gitpilot-backend-xxx.onrender.com/api/health

# Should return:
{"status": "healthy"}
```

### Check Frontend (Vercel):
```bash
# Visit your Vercel URL
https://your-project.vercel.app

# Check browser console for backend URL
console.log(import.meta.env.VITE_BACKEND_URL)
```

### Test Integration:
```bash
# Frontend should successfully call backend API
# Check browser Network tab for API calls to your Render backend
```

---

## 💰 Cost Breakdown

### Free Tier:
- **Docker Hub**: Unlimited public images (free)
- **Render**: 750 hours/month free
  - ⚠️ Spins down after 15min inactivity (cold starts)
- **Vercel**: 100GB bandwidth/month free

### Recommended Paid (for production):
- **Render Starter**: $7/month
  - Always-on (no cold starts)
  - 512MB RAM, shared CPU
- **Vercel Pro**: $20/month
  - More bandwidth, team features

**Total**: $7-27/month for production-ready setup

---

## 🐛 Troubleshooting

### Backend not starting on Render:
```bash
# Check Render logs
# Common issues:
# - Missing environment variables
# - Port not set to $PORT (Render provides this)
# - Docker image pull failed
```

### Frontend can't connect to backend:
```bash
# Check VITE_BACKEND_URL is set in Vercel
# Check CORS_ORIGINS includes your Vercel URL in Render
# Check backend is running (visit health endpoint)
```

### Docker Hub push failed:
```bash
# Make sure you're logged in
docker login

# Check image was built
docker images | grep gitpilot

# Try manual push
docker push your-username/gitpilot-backend:latest
```

---

## 📚 Additional Resources

- [Render Docker Deployment](https://render.com/docs/deploy-an-image)
- [Vercel Environment Variables](https://vercel.com/docs/environment-variables)
- [Docker Hub](https://hub.docker.com/)
- [docker.md](./docker.md) - Full Docker guide
- [render.md](./render.md) - Render deployment details

---

## ✅ Success Checklist

- [ ] Docker images published to Docker Hub
- [ ] Backend deployed on Render
- [ ] Backend health endpoint responding
- [ ] Environment variables set in Render
- [ ] Vercel `VITE_BACKEND_URL` configured
- [ ] Vercel redeployed with new env var
- [ ] Frontend connects to backend successfully
- [ ] Full application working end-to-end

---

## 🎉 You're Done!

Your GitPilot is now deployed:
- ✅ Frontend on Vercel (global CDN, fast)
- ✅ Backend on Render (scalable, Docker-based)
- ✅ Connected and working together

**Access your app:**
- Frontend: `https://your-project.vercel.app`
- Backend: `https://gitpilot-backend-xxx.onrender.com`

Enjoy your cloud-deployed GitPilot! 🚀
