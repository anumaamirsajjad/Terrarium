FROM python:3.12-slim

# Install system dependencies required for geospatial libraries and building
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy uv from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy the dependency definitions
COPY uv.lock pyproject.toml ./

# Install dependencies without installing the project itself
RUN uv sync --frozen --no-install-project --no-dev

# Copy the rest of the source code
COPY . .

# Install the project
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------
# Build the State Cube and Train the Thermal Model
# ---------------------------------------------------------
# This generates data/processed/cube.zarr and data/processed/thermal.txt
RUN uv run python scripts/build_tile.py
RUN uv run python scripts/train_thermal.py

# Point the API to the cube we just built, and listen on all interfaces
ENV TERRARIUM_SERVE_ZARR_STORE=data/processed/cube.zarr
ENV TERRARIUM_API_HOST=0.0.0.0
ENV TERRARIUM_API_PORT=8000

# Expose the API port
EXPOSE 8000

# Run the API
CMD ["uv", "run", "terrarium-api"]
