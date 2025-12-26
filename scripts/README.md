# GitPilot Scripts

Utility scripts for testing and diagnosing GitPilot deployments.

## test-backend-api.sh

Comprehensive backend API health check and diagnostic tool.

### Usage

```bash
# Option 1: Use VITE_BACKEND_URL from .env file
./scripts/test-backend-api.sh

# Option 2: Specify backend URL directly
./scripts/test-backend-api.sh https://gitpilot-backend-latest.onrender.com

# Option 3: Set environment variable and run
VITE_BACKEND_URL=https://your-backend.onrender.com ./scripts/test-backend-api.sh
```

### What It Tests

The script performs the following diagnostic checks:

1. **DNS Resolution** - Verifies the backend hostname resolves correctly
2. **HTTP Connectivity** - Tests basic network connectivity to the backend
3. **Health Endpoint** - Checks `/api/health` returns 200 OK
4. **Auth Status Endpoint** - Checks `/api/auth/status` is accessible
5. **CORS Headers** - Verifies CORS is configured correctly for frontend
6. **Content-Type Headers** - Ensures API returns `application/json`
7. **Backend Environment** - Attempts to identify missing environment variables

### Example Output

```
GitPilot Backend API Diagnostics
========================================
Backend URL: https://gitpilot-backend-latest.onrender.com
Timestamp: 2024-01-15 10:30:00

========================================
Testing Basic Connectivity
========================================
✓ DNS resolution successful
✓ HTTP connectivity successful

========================================
Testing Health Endpoint
========================================
HTTP Status Code: 200
Response Body:
{
  "status": "healthy"
}
✓ Health endpoint is responding correctly

========================================
Diagnostic Summary
========================================
✓ All tests passed! Backend appears healthy.

Next steps:
  1. Set VITE_BACKEND_URL=https://gitpilot-backend-latest.onrender.com in Vercel
  2. Redeploy your Vercel frontend
  3. Test login flow
```

### Troubleshooting

#### "Cannot connect to backend"
- Check that the backend URL is correct
- Verify the backend service is running (check Render dashboard)
- Check firewall/network settings

#### "CORS headers missing"
- Add `CORS_ORIGINS` environment variable to your backend
- Should include your Vercel URL: `https://your-app.vercel.app`
- Use wildcard for preview deployments: `https://your-app-*.vercel.app`

#### "Auth endpoint returned HTTP 500"
- Backend is missing `GITHUB_CLIENT_ID` or `GITHUB_CLIENT_SECRET`
- Check Render environment variables

#### "Content-Type is not JSON"
- Backend may be returning HTML error pages
- Check backend logs for errors
- Verify FastAPI is running correctly

### Requirements

- `curl` - for HTTP requests
- `host` - for DNS lookups (usually pre-installed)
- `jq` - for JSON formatting (optional, but recommended)

Install jq:
```bash
# Ubuntu/Debian
sudo apt-get install jq

# macOS
brew install jq

# Alpine (Docker)
apk add jq
```

### Environment Variables

The script reads from `.env` file in the project root. Create one based on `.env.example`:

```bash
# Frontend - Required for Vercel deployment
VITE_BACKEND_URL=https://your-backend.onrender.com
```

### Exit Codes

- `0` - All tests passed
- `1` - One or more tests failed (check output for details)
