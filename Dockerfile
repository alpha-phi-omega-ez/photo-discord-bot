# Use the 3.12 official docker hardened python dev image with debian trixie (v13)
FROM dhi.io/python:3.12-debian13-dev AS builder

COPY --from=dhi.io/uv:0 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1

WORKDIR /app

# Install dependencies
RUN apt-get update && \
    apt-get install -y gcc libheif-dev libffi-dev python3-dev libjpeg-dev libpng-dev && \
    rm -rf /var/lib/apt/lists/*


COPY uv.lock pyproject.toml main.py /app/

# Install the required packages
RUN uv sync --frozen --no-dev --target /install

# Use the 3.12 official docker hardened python image with debian trixie (v13)
FROM dhi.io/python:3.12-debian13

WORKDIR /app

# Copy the system libraries and python packages from the builder
COPY --from=builder /usr/bin/gcc /usr/bin/gcc
COPY --from=builder /usr/bin/libheif-dev /usr/bin/libheif-dev
COPY --from=builder /usr/bin/libffi-dev /usr/bin/libffi-dev
COPY --from=builder /usr/bin/libjpeg-dev /usr/bin/libjpeg-dev
COPY --from=builder /usr/bin/libpng-dev /usr/bin/libpng-dev
COPY --from=builder /usr/bin/python3-dev /usr/bin/python3-dev
COPY --from=builder /install /app/.venv

COPY main.py /app/main.py

# Set environment to use the installed packages
ENV PATH="/app/.venv/bin:$PATH"

# DHI runs as a non-root user 'python' by default
USER python

CMD ["python", "main.py"]