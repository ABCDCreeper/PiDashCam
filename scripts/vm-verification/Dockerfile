FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends qemu-system-arm qemu-utils ipxe-qemu \
    && rm -rf /var/lib/apt/lists/*
