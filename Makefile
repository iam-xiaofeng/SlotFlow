.PHONY: test-backend typecheck-frontend build-frontend verify

test-backend:
	cd backend && uv run pytest -q

typecheck-frontend:
	cd frontend && pnpm typecheck

build-frontend:
	cd frontend && pnpm build

verify: test-backend typecheck-frontend build-frontend
