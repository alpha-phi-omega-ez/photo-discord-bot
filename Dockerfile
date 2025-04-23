# Use the latest uv image with python 3.13 and debian
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && \
    apt-get install -y libffi libheif-dev libde265-dev && \
    rm -rf /var/lib/apt/lists/*


COPY uv.lock pyproject.toml main.py /app/

# Install the required packages
RUN uv sync --frozen --no-cache


# Run the discord bot
CMD ["uv", "run", "main.py"]
