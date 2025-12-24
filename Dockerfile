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
RUN uv sync --frozen --no-dev --no-install-project

# Stage runtime libraries to a known location for copying to production stage
# This works for both aarch64 and x86_64 architectures
RUN mkdir -p /runtime-libs && \
    cp -r /usr/lib/aarch64-linux-gnu/libheif* /usr/lib/aarch64-linux-gnu/libde265* /usr/lib/aarch64-linux-gnu/libx265* /usr/lib/aarch64-linux-gnu/libffi* /usr/lib/aarch64-linux-gnu/libjpeg* /usr/lib/aarch64-linux-gnu/libpng* /runtime-libs/ 2>/dev/null || \
    cp -r /usr/lib/x86_64-linux-gnu/libheif* /usr/lib/x86_64-linux-gnu/libde265* /usr/lib/x86_64-linux-gnu/libx265* /usr/lib/x86_64-linux-gnu/libffi* /usr/lib/x86_64-linux-gnu/libjpeg* /usr/lib/x86_64-linux-gnu/libpng* /runtime-libs/ 2>/dev/null || true && \
    ARCH=$(uname -m) && \
    if [ -d "/usr/lib/${ARCH}-linux-gnu/libheif" ]; then \
        cp -r /usr/lib/${ARCH}-linux-gnu/libheif /runtime-libs/; \
    fi

# Use the 3.12 official docker hardened python image with debian trixie (v13)
FROM dhi.io/python:3.12-debian13

WORKDIR /app

# Copy runtime libraries from builder staging directory
# These are needed by pyheif and Pillow for HEIC/HEIF, JPEG, and PNG support
# Libraries are staged in builder to work with any architecture
# Copy to both possible architecture paths (COPY creates directories automatically)
COPY --from=builder /runtime-libs/ /usr/lib/aarch64-linux-gnu/
COPY --from=builder /runtime-libs/ /usr/lib/x86_64-linux-gnu/

COPY main.py /app/main.py

# Copy the virtual environment from the builder
COPY --from=builder /app/.venv /app/.venv

# Set environment to use the installed packages
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]