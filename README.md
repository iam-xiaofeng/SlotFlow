# SlotFlow

SlotFlow is a learning-oriented rewrite of the DeerFlow application boundary.

The goal is to keep the core harness and agent capabilities, while replacing
the product-heavy gateway and frontend with a smaller FastAPI + shadcn/ui stack.

## Current Status

- `backend/`: FastAPI skeleton with a verified `/health` endpoint.
- `frontend/`: Next.js + Tailwind skeleton with a minimal shadcn-style button.
- `docs/`: rewrite boundary and WSL development notes.

## Development

Use WSL for all development:

```bash
cd ~/code/SlotFlow
```

The original DeerFlow repository is only used as a reference:

```bash
/mnt/d/test/deer-flow
```

Install dependencies and verify the project:

```bash
cd backend
uv run pytest -q

cd ../frontend
pnpm install
pnpm typecheck
pnpm build
```

Or run all verified checks from the repository root:

```bash
make verify
```
