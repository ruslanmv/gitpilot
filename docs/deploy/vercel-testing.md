# Vercel Local Testing Guide

This guide explains how to test Vercel deployments locally before pushing to production.

## Prerequisites

- Vercel CLI installed (already done via `npm install -g vercel`)
- Vercel account (sign up at https://vercel.com)

## Authentication

Before testing locally, authenticate with Vercel:

```bash
vercel login
```

This will open your browser and prompt you to log in.

## Local Testing Commands

We've added three new make targets for Vercel testing:

### 1. `make vercel` - Run Vercel Dev Server

Runs a local development server that simulates the Vercel deployment environment:

```bash
make vercel
```

This will:
- Install frontend dependencies if needed
- Start Vercel dev server (usually at http://localhost:3000)
- Simulate serverless functions and routing exactly as they would run on Vercel

**Use this for:** Testing your app locally before deploying

### 2. `make vercel-build` - Test Build Process

Tests the Vercel build process locally without deploying:

```bash
make vercel-build
```

This will:
- Install frontend dependencies if needed
- Run the complete Vercel build process
- Validate your `vercel.json` configuration
- Build both frontend and API functions

**Use this for:** Catching build errors before deployment

### 3. `make vercel-deploy` - Deploy to Production

Deploys to Vercel production:

```bash
make vercel-deploy
```

**Use this for:** Deploying to production after local testing

## Vercel Configuration

The `vercel.json` file configures the deployment:

- **Dev Command**: `cd frontend && npm run dev` - Runs Vite dev server on port 5173
- **Build Command**: `cd frontend && npm run build` - Builds the frontend
- **Install Command**: `cd frontend && npm install` - Installs frontend dependencies
- **Framework**: Set to `null` to disable auto-detection (prevents Next.js detection)
- **Frontend Build**: Uses `@vercel/static-build` with the `vercel-build` script from `frontend/package.json`
- **API Functions**: Python serverless functions using `@vercel/python` with Python 3.12 runtime
- **Routes**: API routes go to `/api/*`, all other routes serve the frontend

**Note**: When running `vercel dev`:
- The frontend Vite dev server runs (port 5173) through Vercel's proxy (usually port 3000)
- API functions are automatically started and available at `/api/*` routes
- The Vite proxy is automatically disabled (Vercel handles API routing to serverless functions)
- No need to separately start the Python backend - Vercel runs it as serverless functions

## Fixed Issues

✅ **Removed `excludeFiles` property** - This property is not valid in the Vercel schema and was causing deployment failures.

✅ **Removed conflicting `functions` property** - The `functions` and `builds` properties cannot be used together. Removed `functions` property and kept the `builds` configuration with `@vercel/python` builder.

✅ **Fixed Vercel dev command** - Added `devCommand`, `buildCommand`, and `installCommand` to prevent Vercel from incorrectly detecting the project as Next.js. Set `framework: null` to disable auto-detection.

✅ **Fixed API routing in Vercel dev** - Modified `vite.config.js` to conditionally disable the proxy when running in Vercel environment. This allows Vercel dev to properly route `/api/*` requests to the Python serverless functions instead of trying to proxy to localhost:8000.

## Workflow

### Development Options

You have two ways to develop locally:

**Option 1: Local Development (Recommended for active development)**
```bash
make run
```
- Runs Python backend on http://127.0.0.1:8000
- Runs Vite frontend on http://localhost:5173
- Fast hot-reload for both frontend and backend
- Uses Vite proxy to connect frontend to backend

**Option 2: Vercel Development (Test deployment environment)**
```bash
make vercel
```
- Simulates the exact Vercel production environment
- Runs Python code as serverless functions (like production)
- Frontend served through Vercel dev proxy (port 3000)
- Good for testing before deploying

### Recommended Workflow

1. Make your code changes
2. Test with `make run` for fast iteration
3. Test with `make vercel` to verify Vercel compatibility (optional but recommended)
4. Verify build works with `make vercel-build`
5. Commit and push to your branch
6. Vercel will automatically deploy from GitHub
7. OR manually deploy with `make vercel-deploy`

## Troubleshooting

### "The specified token is not valid"
Run `vercel login` to authenticate.

### Build fails locally
Check that all dependencies are installed:
```bash
make install
```

### Port already in use
The Vercel dev server typically uses port 3000. Stop any processes using that port:
```bash
lsof -ti:3000 | xargs kill -9
```

## References

- [Vercel CLI Documentation](https://vercel.com/docs/cli)
- [Vercel Configuration](https://vercel.com/docs/projects/project-configuration)
- [Vercel Builds](https://vercel.com/docs/build-step)
