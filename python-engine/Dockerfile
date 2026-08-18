FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy TZ=Asia/Kolkata PYTHONUNBUFFERED=1

COPY python-engine/pyproject.toml python-engine/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY python-engine/src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8080
CMD ["uvicorn", "swing_atlas.app:app", "--host", "0.0.0.0", "--port", "8080"]
