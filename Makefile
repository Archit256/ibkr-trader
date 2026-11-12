.PHONY: test build dev

test:
	cd backend && .venv/bin/pytest -v

build:
	cd frontend && npm run build

dev:
	@trap 'kill 0' EXIT; \
	(cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000 --timeout-graceful-shutdown 2) & \
	(cd frontend && npm run dev) & \
	wait

