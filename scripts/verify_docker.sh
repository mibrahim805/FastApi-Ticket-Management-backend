#!/bin/bash
# Ensure cleanup runs even on error, and the script exits on error.
set -e

cleanup() {
    echo "Cleaning up Docker Compose stack..."
    docker compose down
}
trap cleanup EXIT

# Setup temporary environment
export SECRET_KEY=test-secret-that-is-long-enough-for-jwt
export MONGO_ROOT_USERNAME=testuser
export MONGO_ROOT_PASSWORD=testpassword

echo "Starting Docker Compose stack..."
docker compose up -d

# Wait for API to be ready
echo "Waiting for API readiness (polling /health/ready)..."
MAX_RETRIES=30
RETRY_COUNT=0
# Use -f for fail on HTTP errors. Direct curl in condition.
until curl -s -f http://localhost:7000/health/ready > /dev/null; do
    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        echo "API failed to become ready within timeout."
        docker compose logs api
        exit 1
    fi
    echo "API not ready yet... sleeping (retry $RETRY_COUNT/$MAX_RETRIES)"
    sleep 2
    RETRY_COUNT=$((RETRY_COUNT+1))
done

echo "API is ready."

# Check live endpoint
echo "Checking /health/live..."
if curl -s -f http://localhost:7000/health/live > /dev/null; then
    echo "API liveness check passed."
else
    echo "API liveness check failed."
    exit 1
fi

echo "Docker verification successful."
