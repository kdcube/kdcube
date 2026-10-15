.PHONY: check check-backend check-frontend

check:
	bash .github/ci/check.sh all

check-backend:
	bash .github/ci/check.sh backend

check-frontend:
	bash .github/ci/check.sh frontend
