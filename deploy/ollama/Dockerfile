# CPU-only Ollama: the official release minus its GPU backends.
# The official ollama/ollama image bundles CUDA 12/13 and MLX (several GB) that do nothing
# without an NVIDIA GPU. With one, use `image: ollama/ollama` instead (see docs/deploy.md).

FROM debian:bookworm-slim

ARG OLLAMA_VERSION=0.34.4
ARG TARGETARCH

# Streamed straight into tar, so the GPU libraries are never written to disk.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl zstd \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-${TARGETARCH}.tar.zst" \
    | tar --zstd -x -C /usr/local \
        --exclude='lib/ollama/cuda_*' \
        --exclude='lib/ollama/rocm*' \
        --exclude='lib/ollama/mlx*' \
        --exclude='lib/ollama/vulkan' \
    && ollama --version

ENV OLLAMA_HOST=0.0.0.0:11434
EXPOSE 11434
ENTRYPOINT ["ollama"]
CMD ["serve"]
