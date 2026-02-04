# Noctune Studio Web (local)

Lightweight browser UI for Noctune Studio:
- Chat (OpenAI-compatible LLM, e.g. llama.cpp)
- Tool-calling: read/search/edit repo files (gated), start/monitor Noctune runs (gated)
- Local persistence: saves sessions under `<repoRoot>/.noctune_cache/studio_chat/sessions/`

## Run

```bash
pnpm install
cp .env.example .env.local
pnpm dev
```

Open:
- http://localhost:3000/studio

