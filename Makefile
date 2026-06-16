.PHONY: test-backend typecheck-frontend build-frontend verify

test-backend:
	cd backend && uv run pytest -q

typecheck-frontend:
	cd frontend && pnpm typecheck

build-frontend:
	cd frontend && pnpm build

verify: test-backend typecheck-frontend build-frontend

# Start local frontend and backend development servers.
dev:
	@echo "Starting servers (Ctrl+C to stop both)..."
	@(cd frontend && pnpm dev) & \
	(cd backend && uv run uvicorn app.main:app --env-file ./.env --reload)

# Stop common local development server processes.
kill:
	@pkill -f "pnpm dev" || true
	@pkill -f "uvicorn" || true
