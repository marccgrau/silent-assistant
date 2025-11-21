# silent-assistant

A Pipecat voice agent running as a **silent advisor**: listens to a two-human call, performs Deepgram diarized STT, sends agent-only JSON advice via RTVI, and logs audio/utterances/advice to Supabase. No TTS output.

## Configuration

- **Bot Type**: Web (silent, listen-only)
- **Transports**: Daily (WebRTC), SmallWebRTC
- **Pipeline**:
  - STT: Deepgram Flux with diarization
  - LLM: OpenAI (advisor prompt loaded from `server/prompts/`)
  - TTS: none (advisor emits RTVI JSON only)
- **Features**: audio recording (merged WAV), diarized transcription, agent advice via RTVI `server-message`, Supabase logging, smart-turn v3

## Setup

### Server

1) **Navigate**:
```bash
cd server
```
2) **Install deps**:
```bash
uv sync
```
3) **Secrets (keys only)**:
```bash
cp .env.example .env
# Fill Deepgram, OpenAI, Supabase service-role, Daily API keys
```
4) **Non-secrets + prompts**:
```bash
cp config.example.toml config.toml
# Edit Deepgram options, advisor model, Supabase URL/bucket, participant hints, etc.
# Update prompts/advisor_system_prompt.txt for custom behavior.
```
5) **Run**:
```bash
uv run bot.py --transport daily        # or
uv run bot.py --transport smallwebrtc
```

### Client

1) **Navigate**:
```bash
cd client
```
2) **Install deps**:
```bash
npm install
```
3) **Env**:
```bash
cp env.example .env.local
# Edit BOT_START_URL/BOT_START_PUBLIC_API_KEY if needed (defaults to localhost:7860)
```
4) **Run dev**:
```bash
npm run dev
```
5) **Open**: http://localhost:3000

## UI Overview

- Join a Daily/SmallWebRTC session (transport selector + connect button).
- Panels:
  - **Transcript**: finalized user transcriptions with speaker badge and timestamp.
  - **Current Advice**: latest `customer_advice` RTVI `server-message` (agent-only).
- Audio-out from the bot is disabled; the UI is for monitoring and guidance.

## Project Structure

```
silent-assistant/
├── server/
│   ├── bot.py               # Silent advisor pipeline
│   ├── config.example.toml  # Non-secret settings template
│   ├── config.toml          # Your settings (git-ignored)
│   ├── prompts/             # Advisor prompt files
│   ├── env.example          # Secrets template
│   ├── .env                 # Your keys (git-ignored)
│   ├── Dockerfile
│   └── pcc-deploy.toml
├── client/
│   ├── src/                 # React UI (transcript + advice panels)
│   ├── package.json
│   └── ...
└── README.md
```

## Deploying to Pipecat Cloud

This project is configured for Pipecat Cloud. See the [Pipecat Quickstart Guide](https://docs.pipecat.ai/getting-started/quickstart#step-2%3A-deploy-to-production) and [Pipecat Cloud Documentation](https://docs.pipecat.ai/deployment/pipecat-cloud/introduction).

## Outstanding TODOs

- Harden RTVI event handling across transports (confirm event names/payload shapes).
- Add UI for advice history + error states (e.g., missing advice, Supabase failures).
- Add telemetry/logging in the client for advice receipt.
- Add tests/linters for new client components if desired.

## Learn More

- [Pipecat Documentation](https://docs.pipecat.ai/)
- [Voice UI Kit Documentation](https://voiceuikit.pipecat.ai/)
- [Pipecat GitHub](https://github.com/pipecat-ai/pipecat)
- [Pipecat Examples](https://github.com/pipecat-ai/pipecat-examples)
- [Discord Community](https://discord.gg/pipecat)
