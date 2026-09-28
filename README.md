<div align="center">

# 🎙️ rvhack — Voice AI Track

**Round 1: CodeChef assessment → Round 2: build-on-the-spot challenge**

![track](https://img.shields.io/badge/track-Voice%20AI-6c5ce7?style=for-the-badge)
![round1](https://img.shields.io/badge/Round%201-CodeChef-1f6feb?style=for-the-badge)
![round2](https://img.shields.io/badge/Round%202-Build%20Challenge-e17055?style=for-the-badge)
![status](https://img.shields.io/badge/status-preparing-fdcb6e?style=for-the-badge)

*A playbook, a pre-built skeleton, and a demo strategy. Not a pitch deck in Markdown.*

![opensource](https://img.shields.io/badge/stack-100%25%20open--source-2ecc71?style=for-the-badge)

[Rules](#-what-we-know) · [Round 1](#-round-1--codechef-prep) · [Architecture](#-the-architecture-we-pre-build) · [Build day](#-round-2--build-day-timeline) · [Winning](#-how-we-win) · [Demo](#-the-demo-script) · [Checklist](#-pre-event-checklist)

</div>

---

## 📌 What we know

| | |
|---|---|
| **Track** | Voice AI |
| **Round 1** | Online, track-specific, on **CodeChef**. Covers speech recognition, TTS, voice processing, conversational AI. Performance decides who reaches Round 2. |
| **Round 2** | Problem statement is revealed **on the day**. We build from scratch against it. |
| **Allowed** | Any voice/AI API: STT, TTS, LLMs, real-time comms platforms. |
| **Our stack** | **Open-source only** — no paid API keys, no vendor lock-in, no "ran out of credits" mid-demo. Everything below runs locally or self-hosted. |
| **Unknown** | The problem statement, judging rubric weights, time limit, team size limits. *Confirm with organisers.* |

> **Why open-source, not just "allowed":** free-tier API keys are the #1 hackathon demo killer — rate limits, billing holds, and captive wifi blocking outbound calls to a third-party host. A model running in-process on our own laptop has none of those failure modes, and it's an easy differentiator: judges have seen the same three commercial API names in every other team's stack all day.

> **The core insight:** we can't know the problem, but we *can* know the plumbing. Every voice problem statement needs the same loop: **hear → understand → act → speak**. Teams that spend Round 2 wiring up audio I/O lose to teams that arrive with that solved and spend the whole time on the *problem*.

---

## 🧠 Round 1 — CodeChef prep

Click a topic to expand what we need to be able to answer cold.

<details>
<summary><b>🔊 Speech recognition (ASR / STT)</b></summary>

- Pipeline: audio → framing → features (log-mel / MFCC) → acoustic model → decoder (+ language model) → text
- **WER** = (Substitutions + Deletions + Insertions) / Reference words. Be able to compute it by hand and in code (edit distance).
- Streaming vs batch: interim vs final results, chunk size, latency/accuracy tradeoff
- CTC vs attention encoder-decoder vs transducer (RNN-T); why Whisper is 30 s-window seq2seq and not natively streaming
- Endpointing: how the system decides you've finished talking
- Failure modes: accents, noise, code-switching, domain vocabulary, homophones → fixes: keyword boosting, custom vocab, LM rescoring

</details>

<details>
<summary><b>🗣️ Text-to-speech (TTS)</b></summary>

- Classic: text normalisation → grapheme-to-phoneme → prosody → vocoder
- Neural: acoustic model (Tacotron / FastSpeech / VITS-style) + vocoder (HiFi-GAN etc.); modern LLM-style codec models
- Text normalisation matters: "₹1,250", "Dr.", "3/4", dates, phone numbers
- **TTFB** (time to first audio byte) is the metric that matters for conversation, not total synthesis time
- SSML: `<break>`, `<prosody>`, `<say-as>`; streaming synthesis sentence-by-sentence
- MOS as the subjective quality score

</details>

<details>
<summary><b>🎚️ Voice / audio processing (the DSP that shows up in tasks)</b></summary>

- Sampling rate, Nyquist, bit depth. 8 kHz telephony vs 16 kHz ASR vs 24/44.1 kHz TTS output. **Resampling bugs are the #1 silent killer.**
- PCM16 vs float32, mono vs stereo, little-endian byte handling
- FFT, STFT, spectrogram, mel scale, windowing (Hann), hop length
- **VAD** (voice activity detection): energy threshold vs model-based (Silero, WebRTC VAD)
- Noise suppression, **AEC** (acoustic echo cancellation) — why the bot hears itself without it
- Diarization: who spoke when
- Codecs: Opus, μ-law/A-law (telephony), WAV/FLAC

</details>

<details>
<summary><b>💬 Conversational AI</b></summary>

- Intent + slot filling vs LLM tool-calling
- Dialogue state tracking, context window management
- **Turn-taking & barge-in**: user interrupts → stop TTS *and* discard the unspoken part of the assistant's turn from history
- Latency budget (see [below](#-latency-budget))
- Grounding / hallucination control: RAG, tool use, constrained output
- Fallbacks: "I didn't catch that", confirmation for destructive actions

</details>

<details>
<summary><b>⌨️ Likely coding-task shapes</b></summary>

- Implement WER / CER (Levenshtein DP)
- Chunk a PCM stream into fixed frames with overlap; energy-based VAD
- Parse/normalise numbers and dates out of a transcript
- Simulate a dialogue state machine
- Merge/segment timestamps (word-level → sentence-level; diarization merge)
- Sliding-window / two-pointer problems on audio-like arrays

Practise these as plain algorithm problems. CodeChef assessments reward correct + fast, not clever.

</details>

**Mock test:** [`docs/signal-check-mock-test.pdf`](docs/signal-check-mock-test.pdf) — 12 concept MCQs (with answers and explanations) and 5 CodeChef-style coding problems (statement, constraints, sample I/O, approach, reference Python solution) covering the topics above. Print it or read it on any device.

**Round 1 tracker** — tick these off as we go:

- [ ] Implement WER from scratch (Levenshtein, O(nm))
- [ ] Implement frame-based VAD on a raw PCM array
- [ ] Re-derive mel filterbank steps on paper
- [ ] Explain barge-in handling end to end without notes
- [ ] Do 10 medium DP / string problems on CodeChef under a timer
- [ ] Everyone has a working CodeChef account and has done a practice contest

---

## 🏗️ The architecture we pre-build

We arrive with this **already working**. In Round 2 we only swap the *brain* and the *tools*.

```mermaid
flowchart LR
    MIC([🎤 Mic / Phone]) --> VAD[VAD + endpointing]
    VAD --> STT[Streaming STT]
    STT --> ORCH{{Orchestrator<br/>state + turn manager}}
    ORCH --> LLM[LLM<br/>tool-calling]
    LLM --> TOOLS[(Tools / APIs / DB<br/>← Round 2 problem lives here)]
    TOOLS --> LLM
    LLM --> TTS[Streaming TTS]
    TTS --> SPK([🔈 Speaker])
    ORCH -. barge-in .-> TTS
    ORCH --> UI[Live UI: transcript, state, latency]

    style TOOLS fill:#e17055,color:#fff
    style ORCH fill:#6c5ce7,color:#fff
    style UI fill:#00b894,color:#fff
```

The orange box is the *only* part that changes per problem statement. Everything else is reusable.

### Turn-taking state machine

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Listening: speech detected
    Listening --> Thinking: endpoint (silence ≥ threshold)
    Listening --> Listening: more speech
    Thinking --> Speaking: first TTS audio ready
    Speaking --> Idle: playback finished
    Speaking --> Listening: user barge-in<br/>(cancel TTS, truncate history)
    Thinking --> Listening: user resumes talking
```

### Barge-in, the part most teams get wrong

```mermaid
sequenceDiagram
    participant U as User
    participant O as Orchestrator
    participant L as LLM
    participant T as TTS
    L-->>O: token stream
    O->>T: sentence 1
    T-->>U: audio plays
    U->>O: starts talking (VAD fires)
    O->>T: CANCEL
    O->>L: abort generation
    Note over O: history keeps only what was<br/>actually spoken aloud
    O->>O: new turn → Listening
```

If the assistant "remembers" saying a sentence the user never heard, the conversation goes off the rails. Truncate history to what was actually played.

### ⏱️ Latency budget

Target **< 1 s from end of user speech to first audio out.** Below ~700 ms it feels like a person.

| Stage | Target | How |
|---|---|---|
| Endpointing | 200–400 ms | tuned VAD silence window, not a fixed 1 s |
| STT final | 100–300 ms | streaming ASR, use interim results to prefetch |
| LLM first token | 250–500 ms | small/fast model, short prompt, prompt caching |
| TTS first byte | 100–300 ms | streaming TTS, **start on first sentence** |
| **Total** | **< 1 s** | overlap the stages, never run them in series |

We display this live on screen during the demo. Measured numbers beat claims.

### Stack decision — 100% open-source (pick once, in advance)

| Layer | Primary (open-source) | Fallback (open-source) | Why |
|---|---|---|---|
| Transport | **LiveKit** (OSS server, self-hosted) or **Pipecat** | plain WebSocket + browser `AudioWorklet` | both are Apache/BSD-licensed, self-hostable, no managed-cloud dependency |
| VAD | **Silero VAD** | WebRTC VAD (`py-webrtcvad`) | MIT-licensed, tiny, runs fully local, no network hop |
| STT | **faster-whisper** (CTranslate2) or **whisper.cpp** — `base`/`small` for speed | **Vosk** (fully streaming, very low latency) | Whisper variants: best accuracy per param; Vosk: true partial/streaming results with a smaller footprint |
| LLM | **Ollama** running **Llama 3.1 8B** or **Qwen2.5 7B-Instruct** (tool-calling capable) | smaller quantised model (e.g. `llama3.2:3b`) for laptops without a GPU | local inference, zero API cost, no rate limit, no wifi dependency |
| TTS | **Piper** (fast, streaming, CPU-friendly) | **Coqui TTS (XTTS-v2)** for higher-quality/expressive voice if GPU available | Piper: near-instant first-byte, good enough quality, runs on a laptop CPU; Coqui: better prosody when there's headroom |
| UI | Next.js or plain Vite page | — | live transcript + state + latency chart |

**License check** — everything above is MIT/Apache/BSD, safe to self-host and demo without a cloud account:

| Tool | License |
|---|---|
| LiveKit / Pipecat | Apache-2.0 |
| Silero VAD | MIT |
| faster-whisper / whisper.cpp | MIT |
| Vosk | Apache-2.0 |
| Ollama | MIT |
| Llama 3.1 / Qwen2.5 | Meta Llama Community / Apache-2.0 (check model card before redistribution) |
| Piper | MIT |
| Coqui TTS (XTTS-v2) | Coqui Public Model License — **non-commercial**; fine for a hackathon demo, don't ship it commercially without checking |

> ⚠️ Don't pick by hype. Pick the stack we've **already tested together**, then stick with it. A fallback for every layer is mandatory — but since nothing here depends on a remote API, our main risk shifts from "wifi/rate-limits died" to **"this laptop's CPU/GPU is too slow."** Benchmark real latency on the actual demo machine before the event, not on someone's gaming PC.

### Hardware reality check

Local models need local compute. Before committing to a model size, test end-to-end latency **on the exact laptop that will run the demo**:

| Component | No GPU (CPU only) | With a GPU (6GB+ VRAM) |
|---|---|---|
| STT | `faster-whisper` `base`, int8 quantised | `small`/`medium`, fp16 |
| LLM | `llama3.2:3b` or `qwen2.5:3b` via Ollama | `llama3.1:8b` / `qwen2.5:7b` |
| TTS | Piper (always CPU-fast) | Coqui XTTS-v2 for quality |

If the demo machine is CPU-only, default to the smaller column — a fast-but-simple bot beats a smart-but-laggy one on stage.

---

## ⏳ Round 2 — build day timeline

```mermaid
gantt
    title Round 2 (example: 8h build — scale proportionally)
    dateFormat  HH:mm
    axisFormat  %H:%M
    section Understand
    Read problem, pick ONE user, write success metric :a1, 09:00, 30m
    section Adapt
    Swap tools/prompt into skeleton, first e2e voice loop :a2, 09:30, 90m
    section Depth
    Failure handling, edge cases, the "wow" feature :a3, 11:00, 150m
    section Polish
    UI, latency panel, fallback modes :a4, 13:30, 90m
    Freeze code :milestone, 15:00, 0m
    section Ship
    Rehearse demo x3, record backup video, write submission :a5, 15:00, 60m
```

**Hard rules**
1. **Working end-to-end voice loop within the first 2 hours**, however ugly. Improve a running system; never assemble a big-bang one.
2. **Code freeze 1 hour before deadline.** Last-hour features break demos.
3. **Record a backup demo video** before the live one. Wifi will die.
4. One person owns the demo and never touches code in the last hour.

### Roles

| Role | Owns | Name |
|---|---|---|
| 🎧 Audio/Realtime | mic → STT → TTS → speaker, VAD, barge-in, latency | _TBD_ |
| 🧠 Brain/Tools | prompt, tool schemas, problem logic, evals | _TBD_ |
| 🎨 Frontend/Demo | UI, live transcript, visual state, demo script | _TBD_ |
| 🧪 QA/Story | breaks the system, writes test utterances, pitch, submission | _TBD_ |

---

## 🏆 How we win

Judges see 20+ voice bots. Most are the same thing: *"talk to an LLM, it talks back."* That's the baseline, not the differentiator. Where the points actually are:

<details open>
<summary><b>1. Solve the stated problem, for a specific person</b></summary>

Generic "AI assistant" loses to "a night-shift pharmacist with gloved hands who needs to check drug interactions without touching a screen." Name the user, name the moment voice is genuinely *better than a screen*, and say it in the first 20 seconds.

**Test:** if the same product would work as a text chatbot, voice isn't earning its place. Find the reason it must be voice: hands busy, eyes busy, low literacy, phone-only access, accessibility, regional language.

</details>

<details>
<summary><b>2. Feel real-time: latency and interruption</b></summary>

- Sub-second response, visibly measured
- Barge-in works live on stage (interrupt it on purpose in the demo)
- Filler/acknowledgement while a slow tool runs ("checking that now…") instead of dead air

</details>

<details>
<summary><b>3. Do something, not just say something</b></summary>

Voice that triggers real tool calls (book, look up, update a record, call an API) beats voice that answers from vibes. Show the side-effect on screen: the row that changed, the booking that appeared.

</details>

<details>
<summary><b>4. Handle the ugly cases on purpose</b></summary>

Show one deliberately hard moment live:
- user mumbles / changes their mind mid-sentence
- ASR mishears a name → system asks to confirm instead of acting
- low-confidence transcript → clarifying question
- destructive action → explicit spoken confirmation
- Hindi/English code-switching if the problem is India-facing

Judges remember the moment the system *recovered gracefully*, not the happy path.

</details>

<details>
<summary><b>5. Prove it works: small eval, real numbers</b></summary>

Even 20 scripted utterances with a pass/fail table beats "it works great." Put one slide/screen: task success rate, median latency, WER on our test set. Honest numbers, including where it fails.

</details>

<details>
<summary><b>6. Not "AI slop": the anti-checklist</b></summary>

Things that make a project read as generated filler. Avoid all of them:

- ❌ A UI that is a chat window with a mic button and nothing else
- ❌ Claims with no measurement ("blazing fast", "human-like")
- ❌ Features that exist only in the README
- ❌ Generic system prompt: "You are a helpful assistant"
- ❌ Tech-name soup on the slide with no reason each piece was chosen
- ❌ A demo that only works with the exact scripted sentence
- ✅ One sharp problem, one real user, a working loop, a measured result, an honest limitations slide

</details>

### Scoring self-check (we grade ourselves before submitting)

| Criterion | Question we must answer "yes" to | ✅ |
|---|---|---|
| Problem fit | Does it directly solve the given statement, not a nearby one? | ☐ |
| Voice-necessity | Is voice clearly better than text here? | ☐ |
| Technical depth | Streaming, barge-in, tool-calls, or fallbacks, visibly? | ☐ |
| Robustness | Survives a judge saying something unexpected? | ☐ |
| Demo | Works live, has a backup, under 3 minutes? | ☐ |
| Honesty | Do we state limitations and real metrics? | ☐ |

---

## 🎬 The demo script

Three minutes. Rehearsed until boring.

| Time | Beat |
|---|---|
| 0:00–0:20 | **The person + the pain.** One sentence, no tech names. |
| 0:20–1:20 | **Happy path, live.** Real voice, real tool action visible on screen. |
| 1:20–2:00 | **The break.** Interrupt it mid-sentence. Mumble a name. Show it recover. |
| 2:00–2:30 | **The numbers.** Latency panel, eval table. |
| 2:30–3:00 | **Limits + what's next.** What it can't do yet, honestly. |

Backup plan, in order: live mic → pre-recorded audio file fed through the same pipeline → recorded video.

---

## ✅ Pre-event checklist

Everything here is done **before** Round 2, so build day is only the problem.

**Local models** (no API keys needed — pull everything ahead of time, venue wifi is not to be trusted)
- [ ] Ollama installed, `llama3.1:8b` (or `qwen2.5:7b`) **and** a smaller fallback (`llama3.2:3b`) pulled locally
- [ ] `faster-whisper` model weights downloaded (`base` + `small`) — also grab Vosk model as the streaming fallback
- [ ] Piper voice model downloaded; Coqui XTTS-v2 downloaded if the demo machine has a GPU
- [ ] Silero VAD weights cached locally (downloads once, then fully offline)
- [ ] Full pipeline run **with wifi off** end-to-end, at least once, to prove there's no hidden network dependency
- [ ] Benchmarked latency on the actual demo laptop (see [hardware reality check](#hardware-reality-check))

**Skeleton repo** (`/skeleton`, to be built)
- [ ] Browser mic → streaming STT → LLM → streaming TTS → speaker, working
- [ ] Barge-in implemented and tested
- [ ] Tool-calling scaffold: add a tool in one file, one schema
- [ ] Live UI: transcript, state (listening/thinking/speaking), per-stage latency
- [ ] Text-input fallback mode + pre-recorded audio replay mode
- [ ] Runs on a fresh laptop with `one command`

**Logistics**
- [ ] Headset mics + a wired speaker for the demo (laptop speakers cause echo)
- [ ] Phone hotspot as backup network
- [ ] Charger, extension cord
- [ ] Everyone has Git configured and can push

---

## 🗂️ Repo layout

```
rvhack/
├── README.md            ← this file
├── CLAUDE.md            ← engineering rules for ARC
├── backend/             ← ARC runtime (reusable; never imports problem/)
│   ├── app/             ← events, session state, agent loop, tools, adapters
│   └── tests/
├── frontend/            ← Next.js control room
├── problem/             ← hackathon-specific code — the only part that changes per problem
├── evals/               ← deterministic scenario evaluation
├── chaos/               ← failure injection
└── docs/                ← specs (start with docs/phase1-runtime-core.md)
```

From the repo root (needs `uv`, `pnpm`):

| Command | Does |
|---|---|
| `make install` | `uv sync` (backend) + `pnpm install` (workspace) |
| `make dev` | backend on :8000 + control room on :3000; Ctrl+C stops both |
| `make check` | ruff, eslint, `tsc`, pytest |

The browser only talks to :3000; `/api/*` is proxied to the backend.

---

## 🔗 Useful references (all open-source)

- [LiveKit Agents](https://docs.livekit.io/agents/) · [Pipecat](https://github.com/pipecat-ai/pipecat) — realtime voice pipelines
- [Silero VAD](https://github.com/snakers4/silero-vad) — local voice activity detection
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) · [whisper.cpp](https://github.com/ggml-org/whisper.cpp) · [Vosk](https://alphacephei.com/vosk/) — open-source STT
- [Ollama](https://ollama.com/) · [Llama 3.1](https://huggingface.co/meta-llama) · [Qwen2.5](https://huggingface.co/Qwen) — local LLMs with tool-calling
- [Piper TTS](https://github.com/rhasspy/piper) · [Coqui TTS / XTTS-v2](https://github.com/coqui-ai/TTS) — open-source TTS
- [CodeChef](https://www.codechef.com/) — Round 1 platform

---

<div align="center">

**Ship a small thing that works, measured honestly, over a big thing that almost works.**

</div>
