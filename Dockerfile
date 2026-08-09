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
# The State Cube and Thermal Model are built locally and
# copied into the image via the COPY . . command above.
# ---------------------------------------------------------

# Point the API to the cube, and listen on all interfaces
ENV TERRARIUM_SERVE_ZARR_STORE=data/processed/cube.zarr
ENV TERRARIUM_API_HOST=0.0.0.0
ENV TERRARIUM_API_PORT=8000

# Expose the API port
EXPOSE 8000

# Run the API
CMD ["uv", "run", "terrarium-api"]
