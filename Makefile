.PHONY: check check-backend check-frontend

check:
	bash scripts/ci/check.sh all

check-backend:
	bash scripts/ci/check.sh backend

check-frontend:
	bash scripts/ci/check.sh frontend
