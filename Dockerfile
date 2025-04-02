# Use the latest uv image with python 3.12 and debian
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && \
    apt-get install -y gcc libheif-dev libffi-dev python3-dev libjpeg-dev libpng-dev && \
    rm -rf /var/lib/apt/lists/*


COPY uv.lock pyproject.toml main.py /app/

# Install the required packages
RUN uv sync --frozen --no-cache --system


# Run the discord bot
CMD ["uv", "run", "main.py"]
