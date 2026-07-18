.PHONY: test-backend test-frontend typecheck-frontend dead-code-frontend build-frontend verify

test-backend:
	cd backend && uv run pytest -q

test-frontend:
	cd frontend && pnpm test

typecheck-frontend:
	cd frontend && pnpm typecheck

dead-code-frontend:
	cd frontend && pnpm check:dead-code

build-frontend:
	cd frontend && pnpm build

verify: test-backend test-frontend typecheck-frontend dead-code-frontend build-frontend

# Start local frontend and backend development servers.
dev:
	@echo "Starting servers (Ctrl+C to stop both)..."
	@(cd frontend && pnpm dev) & \
	(cd backend && uv run uvicorn app.main:app --env-file ./.env --reload --reload-dir app)

# Stop local development servers by port (avoids pkill -f matching this recipe's
# own shell, which was killing make itself).
kill:
	@fuser -k 3000/tcp 2>/dev/null || true
	@fuser -k 8000/tcp 2>/dev/null || true
