# Deploying ARC

One command on any machine with Docker:

```bash
make deploy          # = docker compose up -d --build
# open http://localhost:8080, click "start mic", talk
make deploy-logs     # backend + proxy logs
make deploy-down
```

The first run builds three images and pulls the LLM (~1 GB) into a volume. After that,
**nothing needs the network**: speech models are baked into the backend image and the LLM
lives in the `ollama` volume. Do the first run on good wifi, before the event.

## What runs

| Service | Image | Role |
|---|---|---|
| `caddy` | `caddy:2` | the only exposed port. `/api/*` (health, traces, the session WebSocket) → backend; everything else → frontend |
| `frontend` | `frontend/Dockerfile` (384 MB) | production Next.js build of the control room |
| `backend` | `backend/Dockerfile` (2.1 GB) | ARC runtime + `problem/`, with Silero, Whisper `base.en` and the Piper voice baked in |
| `ollama` | `deploy/ollama/Dockerfile` (222 MB) | CPU-only Ollama built from the official release |
| `ollama-pull` | same | one-shot: pulls `ARC_LLM_MODEL` into the volume, then exits |

Startup order is enforced with health checks: ollama healthy → model pulled → backend
healthy (models loaded) → caddy. The backend then pre-loads the LLM with the system prompt
and tool schemas (logged as `warm-up finished`).

## The microphone needs HTTPS (except on localhost)

Browsers only allow `getUserMedia` in a secure context. `http://localhost:8080` counts as
secure; `http://<server-ip>:8080` does **not**, and the mic button will report
"no microphone".

On a server with a domain pointing at it:

```bash
ARC_SITE=voice.example.com ARC_HTTP_PORT=80 ARC_HTTPS_PORT=443 make deploy
```

Caddy obtains and renews a Let's Encrypt certificate automatically (ports 80 and 443 must
be reachable from the internet). Codespaces and similar tunnels already serve HTTPS, so
forwarding port 8080 is enough there.

## Configuration

| Variable | Default | |
|---|---|---|
| `ARC_LLM_MODEL` | `qwen2.5:1.5b` | any Ollama model with tool calling; `qwen2.5:3b` for better wording if the machine is fast |
| `ARC_SITE` | `:80` | Caddy site address; a domain enables automatic HTTPS |
| `ARC_HTTP_PORT` / `ARC_HTTPS_PORT` | `8080` / `8443` | host ports |

Whisper model and Piper voice are build arguments of `backend/Dockerfile`
(`WHISPER_MODEL`, `PIPER_VOICE`).

## GPUs and Macs

- **NVIDIA GPU:** in `compose.yaml`, replace the `ollama` service's `build:` with
  `image: ollama/ollama` (it ships CUDA) and uncomment its `deploy:` block. Requires
  `nvidia-container-toolkit` on the host. `ollama-pull` then needs the same image.
- **macOS:** Docker on a Mac cannot use the Apple GPU, so the LLM runs on CPU inside the VM.
  For the live demo on a Mac, run natively with `make dev` instead: native Ollama uses Metal
  and is several times faster.

## Measured (4-core CPU, no GPU, this stack)

Spoken question "Does this charger work with my Edge 50 Pro?", end of speech →

| | |
|---|---|
| STT (Whisper base.en int8) | 1.1 s |
| first audio ("Let me check that.") | 2.4 s |
| first answer audio | 6.4 s |

Same numbers as `make dev` on the same machine: the containers add no measurable latency.

## Troubleshooting

- **`ollama-pull` fails with `i/o timeout` / containers can't reach each other.** Seen in
  GitHub Codespaces: a legacy iptables table with `FORWARD` policy `DROP` blocks Compose
  bridge networks. Allow bridge forwarding (not persistent across restarts):
  `sudo iptables-legacy -I FORWARD -i br-+ -j ACCEPT && sudo iptables-legacy -I FORWARD -o br-+ -j ACCEPT`
- **"no space left on device" while pulling images.** `docker builder prune -af` frees the
  build cache. The official `ollama/ollama` image is several GB because of bundled CUDA
  libraries; the CPU image used here avoids that.
- **Mic says "no microphone".** Not a secure context — see the HTTPS section above.
- **Slow first answer after deploy.** The LLM warm-up runs in the background after the
  backend is healthy; wait for `warm-up finished` in `make deploy-logs`.
