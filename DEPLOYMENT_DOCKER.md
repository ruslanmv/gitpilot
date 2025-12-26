# Docker Deployment Guide for GitPilot

This guide explains how to deploy GitPilot using Docker containers for both backend and frontend.

## 🐳 Architecture

```
┌─────────────────────┐         ┌──────────────────────┐
│  Frontend Container │  HTTP   │  Backend Container   │
│                     │────────>│                      │
│  nginx:alpine       │         │  python:3.12-slim    │
│  Serves React/Vite  │         │  FastAPI + uv        │
│  Port: 3000 (80)    │         │  Port: 8000          │
└─────────────────────┘         └──────────────────────┘
```

**Benefits:**
- ✅ **Portable** - Run anywhere Docker is supported
- ✅ **Consistent** - Same environment locally and in production
- ✅ **Isolated** - Each service in its own container
- ✅ **Scalable** - Easy to replicate and scale

## 📋 Prerequisites

- Docker (20.10+): https://docs.docker.com/get-docker/
- Docker Compose (2.0+): Usually included with Docker Desktop
- Environment variables configured (see below)

## 🚀 Quick Start

### 1. Configure Environment Variables

Create a `.env` file in the root directory:

```bash
# GitHub OAuth (required)
GITHUB_CLIENT_ID=your_github_client_id
GITHUB_CLIENT_SECRET=your_github_client_secret

# LLM Provider (required - choose one)
OPENAI_API_KEY=sk-your-openai-key
# ANTHROPIC_API_KEY=sk-ant-your-anthropic-key

# Optional settings
DEFAULT_LLM_PROVIDER=openai
DEFAULT_LLM_MODEL=gpt-4
```

### 2. Build Containers

```bash
make build-container
```

This builds two Docker images:
- `gitpilot-backend` - Python FastAPI backend
- `gitpilot-frontend` - React/Vite frontend with nginx

### 3. Run Containers

```bash
make run-container
```

Access the application:
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **Backend Health**: http://localhost:8000/api/health

### 4. View Logs

```bash
make logs-container
```

Press `Ctrl+C` to exit log viewer.

### 5. Stop Containers

```bash
make stop-container
```

## 📝 Makefile Commands

| Command | Description |
|---------|-------------|
| `make build-container` | Build both Docker containers |
| `make run-container` | Start containers in detached mode |
| `make stop-container` | Stop and remove containers |
| `make logs-container` | View real-time logs from all containers |
| `make clean-container` | Remove containers, images, and volumes |

## 🔧 Manual Docker Commands

If you prefer to use Docker commands directly:

### Build Images

```bash
# Backend
docker build -f Dockerfile.backend -t gitpilot-backend .

# Frontend
docker build -f Dockerfile.frontend -t gitpilot-frontend .
```

### Run with Docker Compose

```bash
# Start in detached mode
docker-compose up -d

# Start with live logs
docker-compose up

# Stop
docker-compose down
```

### Run Individual Containers

```bash
# Backend
docker run -d \
  --name gitpilot-backend \
  -p 8000:8000 \
  --env-file .env \
  gitpilot-backend

# Frontend
docker run -d \
  --name gitpilot-frontend \
  -p 3000:80 \
  --link gitpilot-backend:backend \
  gitpilot-frontend
```

## 🌐 Deploying to Cloud Platforms

### Render.com (Recommended)

#### Option 1: Using Docker Image Registry

1. **Build and push to Docker Hub**:
   ```bash
   # Tag images
   docker tag gitpilot-backend yourusername/gitpilot-backend:latest
   docker tag gitpilot-frontend yourusername/gitpilot-frontend:latest

   # Push to Docker Hub
   docker push yourusername/gitpilot-backend:latest
   docker push yourusername/gitpilot-frontend:latest
   ```

2. **Deploy Backend on Render**:
   - Go to Render Dashboard → New → Web Service
   - Select "Deploy an existing image from a registry"
   - Image URL: `docker.io/yourusername/gitpilot-backend:latest`
   - Set environment variables (see below)
   - Deploy!

3. **Deploy Frontend on Render**:
   - New → Web Service
   - Image URL: `docker.io/yourusername/gitpilot-frontend:latest`
   - Update nginx config to point to backend URL
   - Deploy!

#### Option 2: Using Render Blueprint

Use the existing `render.yaml` for native build (no Docker).

### AWS Elastic Container Service (ECS)

1. **Push to Amazon ECR**:
   ```bash
   # Authenticate
   aws ecr get-login-password --region us-east-1 | \
     docker login --username AWS --password-stdin YOUR_ECR_URI

   # Tag and push
   docker tag gitpilot-backend:latest YOUR_ECR_URI/gitpilot-backend:latest
   docker push YOUR_ECR_URI/gitpilot-backend:latest
   ```

2. **Create ECS Task Definition** using the ECR image

3. **Create ECS Service** with the task definition

### Google Cloud Run

```bash
# Build and push to Google Container Registry
gcloud builds submit --tag gcr.io/YOUR_PROJECT/gitpilot-backend

# Deploy
gcloud run deploy gitpilot-backend \
  --image gcr.io/YOUR_PROJECT/gitpilot-backend \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated
```

### DigitalOcean App Platform

1. Push to Docker Hub (as above)
2. Create App → From Docker Hub
3. Configure environment variables
4. Deploy!

## 🔐 Environment Variables Reference

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `GITHUB_CLIENT_ID` | GitHub OAuth App Client ID | `Iv1.a1b2c3d4e5f6g7h8` |
| `GITHUB_CLIENT_SECRET` | GitHub OAuth App Secret | `a1b2c3d4e5f6g7h8i9j0...` |
| `OPENAI_API_KEY` | OpenAI API Key | `sk-...` |

### Optional

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API Key (alternative to OpenAI) | - |
| `DEFAULT_LLM_PROVIDER` | Default LLM provider | `openai` |
| `DEFAULT_LLM_MODEL` | Default model name | `gpt-4` |
| `CORS_ORIGINS` | Allowed CORS origins (comma-separated) | `*` |
| `PORT` | Backend port | `8000` |

## 🏗️ Multi-Stage Build Explained

### Frontend Dockerfile

```dockerfile
# Stage 1: Build (node:20-alpine)
- Install dependencies
- Build React/Vite app
- Output: /app/dist

# Stage 2: Serve (nginx:alpine)
- Copy built files from stage 1
- Serve with nginx
- Final image size: ~25MB (vs 1GB+ with Node)
```

### Backend Dockerfile

```dockerfile
# Single stage: python:3.12-slim
- Install system dependencies
- Install uv (fast package manager)
- Copy source code
- Install Python dependencies
- Run FastAPI app
```

## 🔍 Health Checks

Both containers include health checks:

```yaml
# Backend
curl -f http://localhost:8000/api/health || exit 1

# Frontend
wget --quiet --tries=1 --spider http://localhost/ || exit 1
```

Docker automatically restarts unhealthy containers.

## 🐛 Troubleshooting

### Container won't start

**Check logs**:
```bash
docker-compose logs backend
docker-compose logs frontend
```

**Common issues**:
- Missing environment variables
- Port already in use
- Insufficient memory

### Backend connection refused

**Issue**: Frontend can't reach backend

**Solution**: Check `docker-compose.yml` network configuration:
```yaml
services:
  backend:
    networks:
      - gitpilot-network
  frontend:
    networks:
      - gitpilot-network
```

### Frontend shows 502 Bad Gateway

**Issue**: nginx can't proxy to backend

**Solution**: Update `frontend/nginx.conf`:
```nginx
location /api/ {
    proxy_pass http://backend:8000;  # Use service name, not localhost
}
```

### Build fails with "no space left on device"

**Solution**: Clean up Docker resources:
```bash
docker system prune -a
```

## 📊 Resource Usage

Typical resource consumption:

| Container | CPU | Memory | Disk |
|-----------|-----|--------|------|
| Backend | 0.5-1 CPU | 512MB-1GB | 500MB |
| Frontend | 0.1 CPU | 50MB | 25MB |

**Total**: ~1 CPU, ~1GB RAM, ~525MB disk

## 🚢 Production Best Practices

### 1. Use Specific Tags

Don't use `latest` in production:
```bash
docker tag gitpilot-backend:latest gitpilot-backend:v1.0.0
```

### 2. Enable Health Checks

Already configured in `docker-compose.yml`!

### 3. Use Secrets Management

Instead of `.env` file:
```bash
docker run -e GITHUB_CLIENT_ID="$(cat /run/secrets/github_id)" ...
```

### 4. Enable Logging

Configure log drivers:
```yaml
services:
  backend:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

### 5. Set Resource Limits

```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 1G
```

### 6. Use Multi-Stage Builds

Already implemented for frontend to minimize image size!

## 📚 Next Steps

1. ✅ Test locally with `make run-container`
2. ✅ Push images to Docker registry
3. ✅ Deploy to cloud platform (Render, AWS, GCP, etc.)
4. 🔐 Configure production secrets
5. 📊 Set up monitoring and alerts
6. 🔄 Set up CI/CD pipeline

## 🔗 Related Documentation

- [Render Deployment](./DEPLOYMENT_RENDER.md)
- [Vercel Testing](./VERCEL_TESTING.md)
- [Docker Documentation](https://docs.docker.com/)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
