#!/bin/bash

# Build and run the Docker container locally

# Stop existing container if running
echo "Stopping any existing swagger2dcat container..."
docker stop swagger2dcat 2>/dev/null || true
docker rm swagger2dcat 2>/dev/null || true

# Build the latest image
echo "Building Docker image..."
docker build -t swagger2dcat:latest .

# Run the container
echo "Starting container..."
docker run -d \
  --name swagger2dcat \
  -p 8080:8080 \
  --env-file .env \
  swagger2dcat:latest

echo "Container started! Access the application at http://localhost:8080"
echo "View logs with: docker logs -f swagger2dcat"
