FROM python:3.13-slim AS build
ENV UV_PROJECT_ENVIRONMENT=/usr/local/
# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY --from=ghcr.io/astral-sh/uv:0.8.13 /uv /uvx /bin/

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev

FROM python:3.13-slim
WORKDIR /app

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

# Create non-root user for security
RUN groupadd -r botuser && useradd -r -g botuser botuser

# Change ownership to non-root user
RUN chown -R botuser:botuser /app

# Copy installed dependencies from build stage
COPY --from=build /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages

# Switch to non-root user
USER botuser

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations

# Command to run the application
CMD ["python", "-m", "app"]
