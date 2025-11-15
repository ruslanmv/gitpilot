# Production Deployment Guide

This guide shows you how to deploy GitPilot in production using environment variables for secure configuration management.

---

## 🚀 Quick Production Setup

### Option 1: Using .env File (Recommended)

```bash
# 1. Copy the template
cp .env.template .env

# 2. Edit .env and add your credentials
nano .env  # or vim, code, etc.

# 3. Run GitPilot
gitpilot
```

### Option 2: Using Environment Variables

```bash
# Export variables in your shell
export GITPILOT_GITHUB_TOKEN="ghp_xxx"
export GITPILOT_PROVIDER="openai"
export OPENAI_API_KEY="sk-xxx"

# Run GitPilot
gitpilot
```

### Option 3: Using Admin UI

```bash
# 1. Start GitPilot
gitpilot

# 2. Open browser at http://localhost:8000
# 3. Click "⚙️ Admin / Settings"
# 4. Configure your provider and save
```

---

## 🔐 Security Best Practices

### Never Commit Secrets

Add to `.gitignore`:
```
.env
.env.*
.pypirc
~/.gitpilot/settings.json
```

### Use Secret Management in Production

**Docker Secrets:**
```yaml
# docker-compose.yml
services:
  gitpilot:
    image: gitpilot:latest
    secrets:
      - github_token
      - openai_api_key
    environment:
      GITPILOT_GITHUB_TOKEN_FILE: /run/secrets/github_token
      OPENAI_API_KEY_FILE: /run/secrets/openai_api_key

secrets:
  github_token:
    external: true
  openai_api_key:
    external: true
```

**Kubernetes Secrets:**
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: gitpilot-secrets
type: Opaque
data:
  github-token: <base64-encoded-token>
  openai-api-key: <base64-encoded-key>
---
apiVersion: v1
kind: Pod
metadata:
  name: gitpilot
spec:
  containers:
  - name: gitpilot
    image: gitpilot:latest
    env:
    - name: GITPILOT_GITHUB_TOKEN
      valueFrom:
        secretKeyRef:
          name: gitpilot-secrets
          key: github-token
    - name: OPENAI_API_KEY
      valueFrom:
        secretKeyRef:
          name: gitpilot-secrets
          key: openai-api-key
```

**AWS Secrets Manager / Azure Key Vault / GCP Secret Manager:**
- Store secrets in your cloud provider's secret manager
- Use instance profiles / managed identities
- Inject secrets at runtime

---

## 🐳 Docker Deployment

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy application
COPY gitpilot ./gitpilot

# Expose port
EXPOSE 8000

# Run GitPilot
CMD ["gitpilot", "serve", "--host", "0.0.0.0", "--port", "8000", "--no-open"]
```

### Docker Compose

```yaml
version: '3.8'

services:
  gitpilot:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - ~/.gitpilot:/root/.gitpilot  # Persist settings
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/settings"]
      interval: 30s
      timeout: 10s
      retries: 3
```

### Build and Run

```bash
# Build
docker build -t gitpilot:latest .

# Run with .env file
docker run -p 8000:8000 --env-file .env gitpilot:latest

# Or with environment variables
docker run -p 8000:8000 \
  -e GITPILOT_GITHUB_TOKEN=ghp_xxx \
  -e OPENAI_API_KEY=sk-xxx \
  gitpilot:latest
```

---

## ☁️ Cloud Platform Deployment

### Heroku

```bash
# 1. Create app
heroku create your-gitpilot-app

# 2. Set config vars
heroku config:set GITPILOT_GITHUB_TOKEN=ghp_xxx
heroku config:set OPENAI_API_KEY=sk-xxx
heroku config:set GITPILOT_PROVIDER=openai

# 3. Deploy
git push heroku main
```

**Procfile:**
```
web: gitpilot serve --host 0.0.0.0 --port $PORT --no-open
```

### AWS Elastic Beanstalk

```bash
# 1. Initialize EB
eb init -p python-3.11 gitpilot-app

# 2. Set environment variables
eb setenv GITPILOT_GITHUB_TOKEN=ghp_xxx \
          OPENAI_API_KEY=sk-xxx \
          GITPILOT_PROVIDER=openai

# 3. Deploy
eb create gitpilot-env
```

### Google Cloud Run

```bash
# 1. Build container
gcloud builds submit --tag gcr.io/PROJECT_ID/gitpilot

# 2. Deploy
gcloud run deploy gitpilot \
  --image gcr.io/PROJECT_ID/gitpilot \
  --platform managed \
  --region us-central1 \
  --set-env-vars "GITPILOT_GITHUB_TOKEN=ghp_xxx,OPENAI_API_KEY=sk-xxx"
```

### Azure Container Instances

```bash
# 1. Create resource group
az group create --name gitpilot-rg --location eastus

# 2. Deploy
az container create \
  --resource-group gitpilot-rg \
  --name gitpilot \
  --image gitpilot:latest \
  --dns-name-label gitpilot-app \
  --ports 8000 \
  --environment-variables \
    GITPILOT_GITHUB_TOKEN=ghp_xxx \
    OPENAI_API_KEY=sk-xxx
```

---

## 🔧 Configuration Priority

GitPilot uses the following priority for configuration (highest to lowest):

1. **Environment Variables** (.env file or shell exports)
2. **Admin UI Settings** (~/.gitpilot/settings.json)
3. **Default Values** (built-in defaults)

### Example:

```bash
# Admin UI sets: OpenAI, gpt-4o
# .env has: GITPILOT_PROVIDER=claude, ANTHROPIC_API_KEY=sk-ant-xxx

# Result: Uses Claude from .env (environment overrides Admin UI)
```

---

## 📊 Monitoring & Health Checks

### Health Check Endpoint

```bash
curl http://localhost:8000/api/settings
```

### View Configuration

```bash
gitpilot config
```

Output:
```
╭──────────────────────────────────────╮
│   GitPilot Configuration             │
╰──────────────────────────────────────╯

                Settings
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ Setting          ┃ Value        ┃ Source      ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ Active Provider  │ openai       │ Environment │
│ GitHub Token     │ Configured   │ Environment │
│ OpenAI API Key   │ Configured   │ Environment │
│ OpenAI Model     │ gpt-4o-mini  │ Settings    │
└──────────────────┴──────────────┴─────────────┘
```

---

## 🚦 Startup Checks

When you run `gitpilot`, you'll see:

```
╭─────────────────────────────────────────────────╮
│             GitPilot v0.1.0                     │
│   Agentic AI Assistant for GitHub Repositories │
╰─────────────────────────────────────────────────╯

Environment File    ✅ Found
GitHub Token        ✅ Configured
LLM Provider        ✅ OPENAI
Server              http://127.0.0.1:8000

✓ GitPilot is ready!

Next Steps:
  • Open the Admin UI to configure LLM providers
  • Select a repository in the Workspace tab
  • Start chatting with your AI coding assistant

Press Ctrl+C to stop the server
```

If configuration is missing:

```
⚠️  Configuration Issues:
  ❌ GitHub token not found
    Set GITPILOT_GITHUB_TOKEN or GITHUB_TOKEN in .env
    Get token at: https://github.com/settings/tokens
  ❌ OPENAI API key not configured
    Configure in Admin UI or set environment variable

╭─────────────────────── Setup Required ───────────────────────╮
│                                                               │
│  Quick Setup:                                                 │
│                                                               │
│  1. Copy .env.template to .env:                              │
│     cp .env.template .env                                    │
│                                                               │
│  2. Edit .env and add your credentials                       │
│                                                               │
│  3. Or configure via Admin UI in your browser                │
│                                                               │
│  See README.md for detailed setup instructions               │
╰───────────────────────────────────────────────────────────────╯
```

---

## 🔄 Environment Variables Reference

### Required

```bash
GITPILOT_GITHUB_TOKEN=ghp_xxx    # GitHub Personal Access Token
```

### Provider Selection

```bash
GITPILOT_PROVIDER=openai         # openai | claude | watsonx | ollama
```

### OpenAI

```bash
OPENAI_API_KEY=sk-xxx
GITPILOT_OPENAI_MODEL=gpt-4o-mini
```

### Claude

```bash
ANTHROPIC_API_KEY=sk-ant-xxx
GITPILOT_CLAUDE_MODEL=claude-3-5-sonnet-20241022
```

### Watsonx

```bash
WATSONX_API_KEY=xxx
WATSONX_PROJECT_ID=xxx
GITPILOT_WATSONX_MODEL=meta-llama/llama-3-1-70b-instruct
```

### Ollama

```bash
OLLAMA_BASE_URL=http://localhost:11434
GITPILOT_OLLAMA_MODEL=llama3
```

### Server (Optional)

```bash
GITPILOT_HOST=0.0.0.0
GITPILOT_PORT=8000
GITPILOT_DEBUG=false
```

---

## 📝 Example Production .env

```bash
# Production .env example

# GitHub
GITPILOT_GITHUB_TOKEN=ghp_AaBbCcDdEeFfGgHhIiJjKkLlMm1234567890

# Provider
GITPILOT_PROVIDER=openai

# OpenAI
OPENAI_API_KEY=sk-proj-AaBbCcDdEeFfGgHhIiJjKkLlMm1234567890
GITPILOT_OPENAI_MODEL=gpt-4o

# Server
GITPILOT_HOST=0.0.0.0
GITPILOT_PORT=8000
```

---

## 🛡️ Security Checklist

- [ ] Never commit .env file
- [ ] Use separate .env for dev/staging/production
- [ ] Rotate API keys regularly
- [ ] Use read-only GitHub tokens when possible
- [ ] Enable 2FA on all accounts
- [ ] Monitor API usage
- [ ] Use HTTPS in production
- [ ] Implement rate limiting
- [ ] Use firewall/security groups
- [ ] Keep dependencies updated

---

## 🔍 Troubleshooting

### Issue: Configuration not loading

**Solution:**
```bash
# Check if .env exists in current directory
ls -la .env

# Verify environment variables are set
gitpilot config

# Check settings file
cat ~/.gitpilot/settings.json
```

### Issue: API key not working

**Solution:**
```bash
# Verify key is loaded
echo $OPENAI_API_KEY

# Test directly
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

### Issue: Server not accessible

**Solution:**
```bash
# Check if port is in use
lsof -i :8000

# Try different port
gitpilot serve --port 8080

# Check firewall
sudo ufw status
```

---

**GitPilot** - Production-ready with flexible configuration management! 🚀
