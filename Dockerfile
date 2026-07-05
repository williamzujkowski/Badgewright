# Badgewright — a local, read-only Steam badge optimizer.
#
# The image ships only the code; it bakes NO credentials and NO user data. Your local
# SQLite database lives in a mounted volume (SBO_DATA_DIR=/data), never in the image.
# Run it hardened (read-only rootfs, no new privileges, all capabilities dropped) — see
# the README "Docker" section for the full command.

# Pin the base by tag AND digest for reproducibility (Dependabot keeps the digest fresh).
FROM python:3.13-slim@sha256:af5bd286051a06b38587d30a8638958f4a2f38381aa80fe859c740af3411bd4d AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /src
# Copy only what's needed to build the wheel (keeps the layer cache tight).
COPY pyproject.toml README.md ./
COPY src ./src
# Build an isolated venv so the runtime stage carries no build tooling.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install .

# --- runtime -----------------------------------------------------------------
FROM python:3.13-slim@sha256:af5bd286051a06b38587d30a8638958f4a2f38381aa80fe859c740af3411bd4d

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SBO_DATA_DIR=/data

# Non-root, numeric UID so orchestrators can enforce runAsNonRoot and volume ownership
# is predictable. The app only ever writes to the mounted /data volume.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin sbo \
    && mkdir -p /data \
    && chown 10001:10001 /data

COPY --from=builder /opt/venv /opt/venv

USER 10001
WORKDIR /home/sbo
VOLUME ["/data"]

# Fail fast if the CLI is broken in the image.
HEALTHCHECK --interval=1m --timeout=5s CMD ["sbo", "version"]

ENTRYPOINT ["sbo"]
CMD ["--help"]
