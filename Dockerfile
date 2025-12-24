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

# Create virtual environment and install the required packages
RUN uv venv /install && \
    . /install/bin/activate && \
    uv sync --frozen --no-dev

# Use the 3.12 official docker hardened python image with debian trixie (v13)
FROM dhi.io/python:3.12-debian13

WORKDIR /app

# Copy runtime libraries from builder (installed as dependencies of -dev packages)
# These are needed by pyheif and Pillow for HEIC/HEIF, JPEG, and PNG support
COPY --from=builder /usr/lib/x86_64-linux-gnu/libheif.so* /usr/lib/x86_64-linux-gnu/
COPY --from=builder /usr/lib/x86_64-linux-gnu/libffi.so* /usr/lib/x86_64-linux-gnu/
COPY --from=builder /usr/lib/x86_64-linux-gnu/libjpeg.so* /usr/lib/x86_64-linux-gnu/
COPY --from=builder /usr/lib/x86_64-linux-gnu/libpng16.so* /usr/lib/x86_64-linux-gnu/

# Copy the virtual environment from the builder
COPY --from=builder /install /app/.venv

COPY main.py /app/main.py

# Set environment to use the installed packages
ENV PATH="/app/.venv/bin:$PATH"

# DHI runs as a non-root user 'python' by default
USER python

CMD ["python", "main.py"]