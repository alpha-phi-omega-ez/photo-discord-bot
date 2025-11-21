# Use the 3.12.12 official python image with debian trixie (v13)
FROM python:3.12.12-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1

WORKDIR /app

# Install dependencies
RUN apt-get update && \
    apt-get install -y gcc libheif-dev libffi-dev python3-dev libjpeg-dev libpng-dev && \
    rm -rf /var/lib/apt/lists/*


COPY uv.lock pyproject.toml main.py /app/

# Install the required packages
RUN uv sync --frozen --no-cache


# Run the discord bot
CMD ["uv", "run", "main.py"]
