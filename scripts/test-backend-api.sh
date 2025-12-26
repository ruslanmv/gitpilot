#!/bin/bash

# GitPilot Backend API Health Check Script
# This script tests the backend API to diagnose connection issues

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output (all to stderr to not interfere with return values)
print_header() {
    echo -e "\n${BLUE}========================================${NC}" >&2
    echo -e "${BLUE}$1${NC}" >&2
    echo -e "${BLUE}========================================${NC}\n" >&2
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}" >&2
}

print_error() {
    echo -e "${RED}✗ $1${NC}" >&2
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}" >&2
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}" >&2
}

# Load environment variables from .env file
load_env() {
    if [ -f ".env" ]; then
        print_success "Found .env file"
        export $(grep -v '^#' .env | grep -v '^$' | xargs)
    else
        print_warning "No .env file found in current directory"
    fi
}

# Get backend URL from environment or argument
get_backend_url() {
    local backend_url=""

    # Priority: 1. Command line arg, 2. VITE_BACKEND_URL, 3. Prompt user
    if [ -n "$1" ]; then
        backend_url="$1"
        print_info "Using backend URL from argument: $backend_url"
    elif [ -n "$VITE_BACKEND_URL" ]; then
        backend_url="$VITE_BACKEND_URL"
        print_info "Using VITE_BACKEND_URL from environment: $backend_url"
    else
        print_warning "VITE_BACKEND_URL not found in environment"
        read -p "Enter backend URL (e.g., https://gitpilot-backend.onrender.com): " backend_url
    fi

    # Normalize URL:
    # 1. Remove trailing slash
    backend_url="${backend_url%/}"

    # 2. Add http:// prefix if missing (for localhost)
    if [[ ! "$backend_url" =~ ^https?:// ]]; then
        backend_url="http://${backend_url}"
        print_info "Added http:// prefix: $backend_url"
    fi

    # 3. Remove port :8000 from Render URLs (Render uses standard HTTPS port 443)
    if [[ "$backend_url" =~ render\.com:8000 ]]; then
        backend_url="${backend_url/:8000/}"
        print_warning "Removed :8000 from Render URL (Render uses standard HTTPS port)"
        print_info "Corrected URL: $backend_url"
    fi

    echo "$backend_url"
}

# Test basic connectivity
test_connectivity() {
    local url="$1"
    print_header "Testing Basic Connectivity"

    print_info "Target: $url"

    # Extract hostname
    local hostname=$(echo "$url" | sed -e 's|^[^/]*//||' -e 's|/.*$||')

    # Test DNS resolution (optional, skip if host command not available)
    if command -v host > /dev/null 2>&1; then
        print_info "Testing DNS resolution for $hostname..."
        if host "$hostname" > /dev/null 2>&1; then
            print_success "DNS resolution successful"
        else
            print_warning "DNS resolution failed for $hostname (but this might be OK behind proxies)"
        fi
    else
        print_info "Skipping DNS test (host command not available)"
    fi

    # Test basic HTTP connectivity
    print_info "Testing HTTP connectivity..."
    local http_code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 "$url" 2>/dev/null || echo "000")

    if [ "$http_code" = "200" ] || [ "$http_code" = "301" ] || [ "$http_code" = "302" ] || [ "$http_code" = "404" ]; then
        print_success "HTTP connectivity successful (HTTP $http_code)"
    elif [ "$http_code" = "000" ]; then
        print_error "Cannot connect to $url (timeout or connection refused)"
        return 1
    else
        print_warning "HTTP request returned status code: $http_code"
    fi
}

# Test health endpoint
test_health_endpoint() {
    local backend_url="$1"
    print_header "Testing Health Endpoint"

    local health_url="${backend_url}/api/health"
    print_info "Testing: $health_url"

    local response=$(curl -s -w "\n%{http_code}" -m 10 "$health_url" 2>&1)
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)

    echo "HTTP Status Code: $http_code" >&2
    echo "Response Body:" >&2
    echo "$body" | jq . 2>/dev/null || echo "$body" >&2

    if [ "$http_code" = "200" ]; then
        print_success "Health endpoint is responding correctly"
        return 0
    else
        print_error "Health endpoint returned HTTP $http_code"
        return 1
    fi
}

# Test auth status endpoint
test_auth_status() {
    local backend_url="$1"
    print_header "Testing Auth Status Endpoint"

    local auth_url="${backend_url}/api/auth/status"
    print_info "Testing: $auth_url"

    local response=$(curl -s -w "\n%{http_code}" -m 10 "$auth_url" 2>&1)
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)

    echo "HTTP Status Code: $http_code" >&2
    echo "Response Body:" >&2
    echo "$body" | jq . 2>/dev/null || echo "$body" >&2

    if [ "$http_code" = "200" ]; then
        print_success "Auth status endpoint is responding correctly"
        return 0
    else
        print_error "Auth status endpoint returned HTTP $http_code"
        return 1
    fi
}

# Test CORS headers
test_cors() {
    local backend_url="$1"
    print_header "Testing CORS Headers"

    local test_url="${backend_url}/api/health"
    print_info "Testing CORS for: $test_url"

    # Simulate a request from Vercel frontend
    local cors_response=$(curl -s -I -X OPTIONS \
        -H "Origin: https://gitpilot-frontend.vercel.app" \
        -H "Access-Control-Request-Method: GET" \
        -H "Access-Control-Request-Headers: Content-Type" \
        -m 10 \
        "$test_url" 2>&1)

    echo "$cors_response" >&2

    if echo "$cors_response" | grep -qi "Access-Control-Allow-Origin"; then
        local allow_origin=$(echo "$cors_response" | grep -i "Access-Control-Allow-Origin" | cut -d: -f2- | tr -d '\r' | xargs)
        print_success "CORS headers present: $allow_origin"
    else
        print_error "CORS headers missing - this will cause frontend connection issues!"
        print_warning "Add CORS_ORIGINS environment variable to your backend"
        return 1
    fi
}

# Test content-type headers
test_content_type() {
    local backend_url="$1"
    print_header "Testing Content-Type Headers"

    local test_url="${backend_url}/api/health"
    print_info "Checking Content-Type for: $test_url"

    local content_type=$(curl -s -I -m 10 "$test_url" 2>&1 | grep -i "content-type" | cut -d: -f2- | tr -d '\r' | xargs)

    echo "Content-Type: $content_type" >&2

    if echo "$content_type" | grep -qi "application/json"; then
        print_success "Content-Type is correctly set to JSON"
    else
        print_error "Content-Type is not JSON: $content_type"
        print_warning "Frontend expects application/json responses"
        return 1
    fi
}

# Check backend environment variables
check_backend_env() {
    local backend_url="$1"
    print_header "Checking Backend Configuration"

    print_info "Attempting to identify missing environment variables..."

    # Try to get auth URL (this will fail if GITHUB_CLIENT_ID is not set)
    local auth_url_response=$(curl -s -w "\n%{http_code}" -m 10 "${backend_url}/api/auth/url" 2>&1)
    local http_code=$(echo "$auth_url_response" | tail -n1)
    local body=$(echo "$auth_url_response" | head -n-1)

    if [ "$http_code" = "500" ] || [ "$http_code" = "404" ]; then
        print_error "Auth endpoint returned HTTP $http_code"
        print_warning "This usually means GITHUB_CLIENT_ID or GITHUB_CLIENT_SECRET is not set"
        echo "Response: $body" >&2
    elif [ "$http_code" = "200" ]; then
        print_success "GitHub OAuth appears to be configured"
    fi
}

# Main diagnostic function
run_diagnostics() {
    local backend_url="$1"

    print_header "GitPilot Backend API Diagnostics"
    print_info "Backend URL: $backend_url"
    print_info "Timestamp: $(date)"

    local failed_tests=0

    # Run all tests
    test_connectivity "$backend_url" || ((failed_tests++))
    test_health_endpoint "$backend_url" || ((failed_tests++))
    test_auth_status "$backend_url" || ((failed_tests++))
    test_cors "$backend_url" || ((failed_tests++))
    test_content_type "$backend_url" || ((failed_tests++))
    check_backend_env "$backend_url" || true

    # Summary
    print_header "Diagnostic Summary"

    if [ $failed_tests -eq 0 ]; then
        print_success "All tests passed! Backend appears healthy."
        print_info "Next steps:"
        echo "  1. Set VITE_BACKEND_URL=$backend_url in Vercel" >&2
        echo "  2. Redeploy your Vercel frontend" >&2
        echo "  3. Test login flow" >&2
    else
        print_error "$failed_tests test(s) failed"
        print_info "Common fixes:"
        echo "  1. Check that backend is deployed and running" >&2
        echo "  2. Verify environment variables are set in Render:" >&2
        echo "     - GITHUB_CLIENT_ID" >&2
        echo "     - GITHUB_CLIENT_SECRET" >&2
        echo "     - OPENAI_API_KEY (or ANTHROPIC_API_KEY)" >&2
        echo "     - CORS_ORIGINS (should include your Vercel URL)" >&2
        echo "  3. Check Render logs for errors" >&2
    fi
}

# Main script execution
main() {
    echo "GitPilot Backend API Health Check" >&2
    echo "==================================" >&2
    echo "" >&2

    # Load .env file if it exists
    load_env

    # Get backend URL
    BACKEND_URL=$(get_backend_url "$1")

    if [ -z "$BACKEND_URL" ]; then
        print_error "No backend URL provided"
        echo "Usage: $0 [backend-url]" >&2
        echo "Example: $0 https://gitpilot-backend.onrender.com" >&2
        exit 1
    fi

    # Run diagnostics
    run_diagnostics "$BACKEND_URL"
}

# Run main function with all arguments
main "$@"
