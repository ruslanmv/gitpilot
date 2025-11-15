# GitPilot - PyPI Packaging Guide

This document provides complete instructions for building, testing, and publishing GitPilot to PyPI.

---

## 📋 Prerequisites

### Required Tools

```bash
# Install build tools
pip install build twine

# Or using the dev dependencies
pip install -e .[dev]
```

### Required Accounts

1. **PyPI Account**: https://pypi.org/account/register/
2. **TestPyPI Account**: https://test.pypi.org/account/register/

### API Tokens

Create API tokens for both PyPI and TestPyPI:

**PyPI:**
1. Go to https://pypi.org/manage/account/token/
2. Click "Add API token"
3. Name: "GitPilot Publishing"
4. Scope: "Entire account" (or specific to gitpilot project)
5. Copy the token (starts with `pypi-`)

**TestPyPI:**
1. Go to https://test.pypi.org/manage/account/token/
2. Follow the same steps as PyPI

---

## 🔧 Configuration

### 1. Setup PyPI Credentials

Copy the template and add your tokens:

```bash
cp .pypirc.template ~/.pypirc
chmod 600 ~/.pypirc  # Secure the file
```

Edit `~/.pypirc`:

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-YOUR_ACTUAL_PYPI_TOKEN_HERE

[testpypi]
repository = https://test.pypi.org/legacy/
username = __token__
password = pypi-YOUR_ACTUAL_TESTPYPI_TOKEN_HERE
```

### 2. Verify Package Configuration

Check `pyproject.toml`:

```toml
[project]
name = "gitpilot"
version = "0.1.0"  # Update for new releases
description = "Production-ready agentic AI assistant..."
requires-python = ">=3.11,<3.12"
```

Key fields:
- `version`: Follows semantic versioning (MAJOR.MINOR.PATCH)
- `requires-python`: Python version constraints
- `dependencies`: Runtime dependencies
- `optional-dependencies.dev`: Development dependencies

---

## 🏗️ Building the Package

### Step 1: Clean Previous Builds

```bash
make clean
# Or manually:
rm -rf build/ dist/ *.egg-info
```

### Step 2: Build Frontend

The frontend must be built before packaging:

```bash
make frontend-build
```

This:
1. Installs frontend dependencies (`npm install`)
2. Builds production assets (`npm run build`)
3. Copies `frontend/dist/` to `gitpilot/web/`

Verify the build:

```bash
ls -la gitpilot/web/
# Should show:
# - index.html
# - assets/index-*.css
# - assets/index-*.js
```

### Step 3: Build Python Package

```bash
make build
# Or manually:
python -m build
```

This creates:
- `dist/gitpilot-0.1.0-py3-none-any.whl` (wheel)
- `dist/gitpilot-0.1.0.tar.gz` (source distribution)

### Step 4: Verify Package Contents

```bash
# List contents of the wheel
unzip -l dist/gitpilot-0.1.0-py3-none-any.whl

# Check for:
# - gitpilot/*.py (Python modules)
# - gitpilot/web/ (frontend assets)
# - gitpilot-0.1.0.dist-info/ (metadata)
```

Verify the tarball:

```bash
tar -tzf dist/gitpilot-0.1.0.tar.gz | head -20

# Should include:
# - gitpilot/ (Python package)
# - README.md
# - LICENSE
# - pyproject.toml
```

---

## 🧪 Testing the Package Locally

### Option 1: Install from Wheel

```bash
# Create a test virtual environment
python3.11 -m venv test-env
source test-env/bin/activate

# Install the wheel
pip install dist/gitpilot-0.1.0-py3-none-any.whl

# Test the installation
gitpilot --help
which gitpilot

# Run the application
export GITPILOT_GITHUB_TOKEN="ghp_xxx"
export OPENAI_API_KEY="sk-xxx"
gitpilot

# Deactivate when done
deactivate
rm -rf test-env
```

### Option 2: Install in Editable Mode

```bash
# For development
pip install -e .

# With dev dependencies
pip install -e .[dev]
```

---

## 📤 Publishing to TestPyPI

Always test on TestPyPI before publishing to the real PyPI.

### Step 1: Upload to TestPyPI

```bash
make publish-test
# Or manually:
twine upload -r testpypi dist/*
```

Expected output:
```
Uploading distributions to https://test.pypi.org/legacy/
Uploading gitpilot-0.1.0-py3-none-any.whl
100% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Uploading gitpilot-0.1.0.tar.gz
100% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

View at:
https://test.pypi.org/project/gitpilot/0.1.0/
```

### Step 2: Test Installation from TestPyPI

```bash
# Create clean environment
python3.11 -m venv testpypi-env
source testpypi-env/bin/activate

# Install from TestPyPI
pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    gitpilot

# Test it works
export GITPILOT_GITHUB_TOKEN="ghp_xxx"
export OPENAI_API_KEY="sk-xxx"
gitpilot --help
gitpilot

# Clean up
deactivate
rm -rf testpypi-env
```

**Note:** `--extra-index-url https://pypi.org/simple/` is needed because dependencies (FastAPI, CrewAI, etc.) are on the main PyPI, not TestPyPI.

---

## 🚀 Publishing to PyPI

Once tested on TestPyPI, publish to the production PyPI.

### Pre-Release Checklist

- [ ] All tests pass (`make test`)
- [ ] Code is linted and formatted (`make lint`, `make fmt`)
- [ ] Frontend is built (`make frontend-build`)
- [ ] Version number updated in `pyproject.toml` and `gitpilot/version.py`
- [ ] CHANGELOG.md updated
- [ ] Documentation is current
- [ ] Package tested on TestPyPI
- [ ] Git tag created for the release

### Step 1: Create Git Tag

```bash
git tag -a v0.1.0 -m "Release version 0.1.0"
git push origin v0.1.0
```

### Step 2: Upload to PyPI

```bash
make publish
# Or manually:
twine upload dist/*
```

Expected output:
```
Uploading distributions to https://upload.pypi.org/legacy/
Uploading gitpilot-0.1.0-py3-none-any.whl
100% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Uploading gitpilot-0.1.0.tar.gz
100% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

View at:
https://pypi.org/project/gitpilot/0.1.0/
```

### Step 3: Verify on PyPI

1. Visit https://pypi.org/project/gitpilot/
2. Check:
   - Description renders correctly
   - Links work (Homepage, Documentation, Issues)
   - Version is correct
   - Classifiers are accurate
   - Dependencies are listed

### Step 4: Test Installation from PyPI

```bash
# Create clean environment
python3.11 -m venv pypi-test-env
source pypi-test-env/bin/activate

# Install from PyPI
pip install gitpilot

# Test
gitpilot --help

# Clean up
deactivate
rm -rf pypi-test-env
```

---

## 🔄 Releasing New Versions

### Version Numbering

Follow [Semantic Versioning](https://semver.org/):

- **MAJOR** (1.0.0): Incompatible API changes
- **MINOR** (0.1.0): Add functionality (backwards-compatible)
- **PATCH** (0.1.1): Bug fixes (backwards-compatible)

### Release Process

1. **Update version number** in both files:
   ```bash
   # pyproject.toml
   version = "0.2.0"

   # gitpilot/version.py
   __version__ = "0.2.0"
   ```

2. **Update CHANGELOG.md**:
   ```markdown
   ## [0.2.0] - 2024-12-01

   ### Added
   - New feature X

   ### Changed
   - Improved Y

   ### Fixed
   - Bug Z
   ```

3. **Commit changes**:
   ```bash
   git add pyproject.toml gitpilot/version.py CHANGELOG.md
   git commit -m "Bump version to 0.2.0"
   git push
   ```

4. **Build and publish**:
   ```bash
   make clean
   make frontend-build
   make build
   make publish-test  # Test first!
   make publish       # Production
   ```

5. **Create GitHub release**:
   ```bash
   git tag -a v0.2.0 -m "Release version 0.2.0"
   git push origin v0.2.0
   ```

   Then create a release on GitHub with the changelog.

---

## 🛠️ Troubleshooting

### Issue: "File already exists"

**Problem:** Trying to upload a version that already exists on PyPI.

**Solution:**
```bash
# Increment the version number
# Edit pyproject.toml and gitpilot/version.py
# Then rebuild
make clean
make build
```

### Issue: "Invalid distribution file"

**Problem:** Package structure is incorrect.

**Solution:**
```bash
# Verify package contents
unzip -l dist/*.whl
tar -tzf dist/*.tar.gz

# Ensure gitpilot/web/ exists and contains frontend
ls -la gitpilot/web/

# Rebuild frontend if needed
make frontend-build
```

### Issue: "401 Unauthorized"

**Problem:** Invalid or missing API token.

**Solution:**
```bash
# Verify ~/.pypirc exists and has correct token
cat ~/.pypirc

# Test authentication
twine check dist/*
```

### Issue: Frontend assets not included

**Problem:** `gitpilot/web/` directory not found in package.

**Solution:**
```bash
# Ensure MANIFEST.in includes web/
cat MANIFEST.in

# Rebuild frontend
make frontend-build

# Check directory exists
ls -la gitpilot/web/

# Rebuild package
make build

# Verify web/ is in the wheel
unzip -l dist/*.whl | grep web/
```

---

## 📊 Package Quality Checks

### Before Publishing

Run these checks:

```bash
# Lint code
make lint

# Format code
make fmt

# Run tests
make test

# Check package metadata
twine check dist/*

# Verify package can be installed
pip install dist/*.whl --dry-run
```

### Package Size

Monitor package size:

```bash
ls -lh dist/
# Wheel should be ~350-400 KB
# Source should be ~320-370 KB
```

If too large:
- Ensure `frontend/node_modules/` is excluded (check .gitignore)
- Verify only `gitpilot/web/` is included, not `frontend/`
- Check MANIFEST.in excludes development files

---

## 🔐 Security Best Practices

1. **Never commit credentials**
   - Add `.pypirc` to `.gitignore`
   - Use API tokens, not passwords
   - Rotate tokens periodically

2. **Use token scope**
   - Create project-specific tokens when possible
   - Limit token permissions

3. **Verify packages**
   - Always test on TestPyPI first
   - Review package contents before uploading
   - Check for sensitive data in distributions

4. **Enable 2FA**
   - Enable Two-Factor Authentication on PyPI account
   - Use a password manager

---

## 📚 Additional Resources

- **PyPI Publishing Guide**: https://packaging.python.org/tutorials/packaging-projects/
- **Twine Documentation**: https://twine.readthedocs.io/
- **Setuptools Documentation**: https://setuptools.pypa.io/
- **PEP 517/518**: Modern Python packaging standards
- **Semantic Versioning**: https://semver.org/

---

## ✅ Quick Reference

```bash
# Full release workflow
make clean              # Clean old builds
make frontend-build     # Build frontend
make build              # Build Python package
make publish-test       # Test on TestPyPI
make publish            # Publish to PyPI

# Individual steps
make lint               # Lint code
make fmt                # Format code
make test               # Run tests
twine check dist/*      # Verify package
```

---

**GitPilot** - Ready for production PyPI distribution! 📦
