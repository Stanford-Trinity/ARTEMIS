FROM ghcr.io/astral-sh/uv:python3.11-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \ 
    curl \
    libssl-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*


RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    ~/.cargo/bin/rustup install stable && \
    ~/.cargo/bin/rustup default stable

ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app

COPY . /app/trinity/ARTEMIS

WORKDIR /app/trinity/ARTEMIS

RUN cd codex-rs && cargo build --release && \
cp target/release/codex ../supervisor/codex

WORKDIR /app/trinity/ARTEMIS/supervisor

RUN uv sync

ENV VIRTUAL_ENV=/app/trinity/ARTEMIS/supervisor/.venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"




