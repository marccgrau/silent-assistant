## Goal
- Turn the bot into a silent advisor for two-human calls (customer ↔ agent). Use Deepgram STT with diarization, generate agent-only advice after each customer utterance, emit advice via RTVI as JSON (no TTS), and persist utterances/advice/audio to Supabase (Postgres + Storage).

## Conversation & Pipeline Plan
- Transport audio in/out as today but **no audio out** from the bot; only listen/observe.
- Deepgram STT with diarization enabled (Nova models, e.g., nova-3-general; Flux does not support diarization); capture speaker labels, timestamps, confidence. Maintain a rolling transcript buffer with speaker tags.
- Identify the “customer” speaker (e.g., first non-agent utterance, or via transport metadata if available) and route only customer finalized utterances to the advisor logic.
- On finalized customer utterance:
  - Build an LLM prompt with recent transcript (both speakers), conversation goals/guardrails, and produce concise agent guidance + optional short rationale.
  - Emit RTVI event with fixed JSON payload for the frontend, do not queue TTS/output audio.
  - Log utterance and advice to Supabase.
- Keep recording the full session audio; on disconnect/end, upload merged WAV to Supabase Storage and persist a conversation record linking to the storage URL.

## RTVI Advice Payload (draft)
- Sent via RTVI processor `emit_event` on each customer utterance:
  ```json
  {
    "type": "customer_advice",
    "conversation_id": "<uuid>",
    "utterance_id": "<uuid>",
    "customer_text": "<transcribed text>",
    "speaker": "customer",
    "advice": "<short agent-facing guidance>",
    "rationale": "<optional brief reasoning>",
    "timestamp": "<ISO8601>",
    "context_seq": <int>, 
    "metadata": {
      "turn_start": "<ISO8601>",
      "turn_end": "<ISO8601>",
      "diarization_speaker": "<dg_speaker_label>"
    }
  }
  ```

## Supabase Persistence
- Tables:
  - `conversations(id, started_at, ended_at, transport, storage_url, extra jsonb)`
  - `utterances(id, conversation_id, role, text, diarization_speaker, start_ts, end_ts, seq, raw jsonb)`
  - `advice(id, conversation_id, utterance_id, content, rationale, created_at, metadata jsonb)`
- Storage: bucket `call-audio` (or configurable); store merged WAV per conversation; keep URL in `conversations.storage_url`.
- Use service-role key for server writes; keep the key in `.env`, and set URL/bucket in `server/config.toml`.

## Config & Secrets Split
- Secrets (API keys) in `server/.env`.
- Non-secret settings in `server/config.toml` (copy from `config.example.toml`).
- Prompts in dedicated files under `server/prompts/` (advisor: `advisor_system_prompt.txt`).

## Implementation Subtasks
- [x] Add env/config for Supabase URL/key/bucket, conversation metadata (agent id, customer id), Deepgram diarization params, and advisory model settings.
- [x] Rework `server/bot.py` pipeline: drop TTS/output, enable Deepgram diarization, track transcript buffer with speaker labels, and gate LLM calls on finalized customer turns.
- [x] Implement advisor logic: construct prompts from recent turns, call LLM, emit RTVI JSON payload, and ensure it is solely agent-visible.
- [x] Wire Supabase persistence: async client, insert conversation + utterance + advice rows, and upload merged WAV to storage on disconnect.
- [ ] Update logging/metrics to capture RTVI advice emits and Supabase write outcomes; handle retries/failures gracefully.
- [ ] (Optional) Client: listen for `customer_advice` RTVI events and render agent UI; add minimal telemetry of receipt.
