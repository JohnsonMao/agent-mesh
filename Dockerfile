FROM mcr.microsoft.com/playwright:v1.51.0-noble

# Install Python 3.13 and uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    curl \
    git \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.13 \
    python3.13-venv \
    python3.13-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv for Python dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install Google Chrome and its Linux dependencies during image build.
# playwright-cli uses Chrome by default.
RUN npm install -g @playwright/cli@latest \
    && playwright-cli install-browser chrome --with-deps

WORKDIR /app

# Copy dependency definition and lockfile
COPY pyproject.toml uv.lock ./

# Install project dependencies into virtualenv
RUN uv sync --frozen --no-dev

# Copy application source code
COPY . .

# Default command starts the Slack app
CMD ["uv", "run", "python", "slack_app.py"]
