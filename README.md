<h1 align="center">🏹 ARTEMIS</h1>
<p align="center"><strong>A</strong>utomated <strong>R</strong>ed <strong>T</strong>eaming <strong>E</strong>ngine with <strong>M</strong>ulti-agent <strong>I</strong>ntelligent <strong>S</strong>upervision</p>
<p align="center">ARTEMIS is an autonomous agent created by the <a href="https://trinity.cs.stanford.edu/">Stanford Trinity project</a> to automate vulnerability discovery.</p>

#### Quickstart

Install `uv` if you haven't already:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install the latest version of Rust (required for building):

```bash
# Remove old Rust if installed via apt
sudo apt remove rustc cargo
sudo apt install libssl-dev

# Install rustup (the official Rust toolchain installer)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Restart shell or source the environment
source ~/.cargo/env

# Install latest stable Rust
rustup install stable
rustup default stable
```

First, we have to build the codex binary:

```bash
cargo build --release --manifest-path codex-rs/Cargo.toml
```

Now we can setup the Python environment:

```bash
uv sync
source .venv/bin/activate
```

### Environment Configuration

Copy the example configuration and add your API keys:

```bash
cp .env.example .env
# Edit .env with your API keys
```

Required environment variables:
- `OPENROUTER_API_KEY` or `OPENAI_API_KEY` - For the supervisor and LLM calls
- `SUBAGENT_MODEL` - Model to use for spawned Codex instances (e.g., `anthropic/claude-sonnet-4`)

### Quick Test Run

Try a simple CTF challenge to verify everything works:

```bash
python -m supervisor.supervisor \
  --config-file configs/tests/ctf_easy.yaml \
  --benchmark-mode \
  --duration 10 \
  --skip-todos
```

This runs a 10-minute test on an easy CTF challenge in benchmark mode (no triage process).

For detailed configuration options and usage, see [supervisor-usage.md](docs/supervisor-usage.md).

---

## License

This repository is licensed under the [Apache-2.0 License](LICENSE).

