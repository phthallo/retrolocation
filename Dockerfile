FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.14-slim-bookworm

RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir /data && chown app /data

COPY --from=build /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    RETRO_TOKEN_FILE=/data/retro_refresh_token

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)"

# one worker: the location cache and sign-in lock live in process memory.
# forwarded-allow-ips trusts coolify's proxy, which is the only thing that can reach the container.
CMD ["uvicorn", "retrolocation.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--forwarded-allow-ips", "*", "--no-server-header"]
