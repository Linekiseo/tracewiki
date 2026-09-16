.PHONY: install run test lint backend-smoke backend-integration backend-evaluation backend-full g0-admission-build g0-admission-verify g0-admission-release-verify g0-admission-cleanroom g0-owner-review-build g0-owner-review-verify g1-entry-draft g1-entry-signing-requests g1-entry-assemble g1-entry-verify index-self build frontend-ci frontend-test frontend-typecheck frontend-verify frontend-build native-macos-test native-macos-package native-windows-structure

G0_ADMISSION_MANIFEST ?= artifacts/rag-maturity/g0/admission-20260829-v27/admission.json
G0_OWNER_REVIEW_PACKET ?= artifacts/rag-maturity/g0/owner-review-20260829-v8/review-packet.json
G1_ENTRY_PACKET ?=
G1_ENTRY_PROPOSAL ?=
G1_ENTRY_DRAFT ?=
G1_ENTRY_SIGNING_REQUESTS ?=
G1_ENTRY_ATTESTATIONS ?=
G1_ENTRY_KEYSET ?=
G1_ENTRY_TRUSTED_KEYSET_SHA256 ?=
G1_ENTRY_VERIFICATION_TIME ?=

install:
	uv sync --extra test

run:
	uv run evidence-rag serve --reload

test:
	uv run pytest

lint:
	uv run --frozen ruff check src tests

backend-smoke:
	PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -p no:cacheprovider \
		tests/test_code_rag_contracts.py \
		tests/test_multisource_foundation_v2.py \
		tests/test_wiki_foundation_v1.py

backend-integration:
	PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -p no:cacheprovider \
		tests/test_multisource_runtime_v2.py \
		tests/test_wiki_store_v1.py \
		tests/test_wiki_compiler_v1.py \
		tests/test_wiki_search_navigator_v1.py \
		tests/test_wiki_live_organization_v1.py \
		tests/test_wiki_query_v1.py \
		tests/test_wiki_api_v1.py

backend-evaluation:
	PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -p no:cacheprovider \
		tests/test_code_cb1_run.py \
		tests/test_code_cb2_run.py \
		tests/test_code_cb5_run.py

backend-full: lint
	PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -p no:cacheprovider

g0-admission-build:
	uv run --frozen python -m evidence_rag.evaluation.maturity_g0_admission_v2 \
		build $(G0_ADMISSION_MANIFEST) --root .

g0-admission-verify:
	uv run --frozen python -m evidence_rag.evaluation.maturity_g0_admission_v2 \
		verify $(G0_ADMISSION_MANIFEST) --root .

g0-admission-release-verify:
	uv run --frozen python -m evidence_rag.evaluation.maturity_g0_admission_v2 \
		verify $(G0_ADMISSION_MANIFEST) --root . --require-release-ready

g0-admission-cleanroom:
	@set -eu; cleanroom_parent=$$(mktemp -d /tmp/rag-g0-cleanroom.XXXXXX); \
		uv run --frozen python -m evidence_rag.evaluation.maturity_g0_admission_v2 \
			materialize $(G0_ADMISSION_MANIFEST) $$cleanroom_parent/source --root .; \
		PYTHONPATH=src uv run --frozen python tests/fixtures/g0_history/cleanroom.py \
			--root $$cleanroom_parent/source --admission $(G0_ADMISSION_MANIFEST); \
		printf '%s\n' "$$cleanroom_parent/source"

g0-owner-review-build:
	uv run --frozen python -m evidence_rag.evaluation.maturity_g0_owner_review_v1 \
		build "$(G0_OWNER_REVIEW_PACKET)" --admission "$(G0_ADMISSION_MANIFEST)" --root .

g0-owner-review-verify:
	uv run --frozen python -m evidence_rag.evaluation.maturity_g0_owner_review_v1 \
		verify "$(G0_OWNER_REVIEW_PACKET)" --admission "$(G0_ADMISSION_MANIFEST)" --root .

g1-entry-verify:
	@test -n "$(G1_ENTRY_PACKET)" -a -n "$(G1_ENTRY_ATTESTATIONS)" -a \
		-n "$(G1_ENTRY_KEYSET)" -a -n "$(G1_ENTRY_TRUSTED_KEYSET_SHA256)" -a \
		-n "$(G1_ENTRY_VERIFICATION_TIME)" || \
		(echo "G1 Entry packet, attestations, keyset, trust digest, and time are required" >&2; exit 2)
	uv run --frozen python -m evidence_rag.evaluation.maturity_g1_entry_v1 \
		"$(G1_ENTRY_PACKET)" --at "$(G1_ENTRY_VERIFICATION_TIME)" \
		--attestations "$(G1_ENTRY_ATTESTATIONS)" --keyset "$(G1_ENTRY_KEYSET)" \
		--trusted-keyset-sha256 "$(G1_ENTRY_TRUSTED_KEYSET_SHA256)" \
		--g0-admission "$(G0_ADMISSION_MANIFEST)"

g1-entry-draft:
	@test -n "$(G1_ENTRY_PROPOSAL)" -a -n "$(G1_ENTRY_VERIFICATION_TIME)" || \
		(echo "G1 Entry proposal and time are required" >&2; exit 2)
	uv run --frozen python -m evidence_rag.evaluation.maturity_g1_entry_handoff_v1 \
		draft "$(G1_ENTRY_PROPOSAL)" --at "$(G1_ENTRY_VERIFICATION_TIME)"

g1-entry-signing-requests:
	@test -n "$(G1_ENTRY_DRAFT)" -a -n "$(G1_ENTRY_VERIFICATION_TIME)" || \
		(echo "G1 Entry draft and request time are required" >&2; exit 2)
	uv run --frozen python -m evidence_rag.evaluation.maturity_g1_entry_handoff_v1 \
		requests "$(G1_ENTRY_DRAFT)" --at "$(G1_ENTRY_VERIFICATION_TIME)" \
		--g0-admission "$(G0_ADMISSION_MANIFEST)"

g1-entry-assemble:
	@test -n "$(G1_ENTRY_DRAFT)" -a -n "$(G1_ENTRY_SIGNING_REQUESTS)" -a \
		-n "$(G1_ENTRY_ATTESTATIONS)" -a -n "$(G1_ENTRY_KEYSET)" -a \
		-n "$(G1_ENTRY_TRUSTED_KEYSET_SHA256)" -a -n "$(G1_ENTRY_VERIFICATION_TIME)" || \
		(echo "G1 Entry draft, requests, attestations, keyset, trust digest, and time are required" >&2; exit 2)
	uv run --frozen python -m evidence_rag.evaluation.maturity_g1_entry_handoff_v1 \
		assemble "$(G1_ENTRY_DRAFT)" --requests "$(G1_ENTRY_SIGNING_REQUESTS)" \
		--attestations "$(G1_ENTRY_ATTESTATIONS)" --keyset "$(G1_ENTRY_KEYSET)" \
		--trusted-keyset-sha256 "$(G1_ENTRY_TRUSTED_KEYSET_SHA256)" \
		--at "$(G1_ENTRY_VERIFICATION_TIME)" --g0-admission "$(G0_ADMISSION_MANIFEST)"

frontend-ci:
	npm --prefix frontend run verify:install-policy
	npm --prefix frontend ci

frontend-test: frontend-ci
	npm --prefix frontend test

frontend-typecheck: frontend-ci
	npm --prefix frontend run typecheck

frontend-verify: frontend-test frontend-typecheck

frontend-build: frontend-verify
	npm --prefix frontend run build

build: frontend-build

native-macos-test:
	cd native/macos && swift format lint --strict --recursive Sources Tests && swift test

native-macos-package: native-macos-test
	cd native/macos && ./scripts/build_and_run.sh --package

native-windows-structure:
	cd native/windows && ./ci/verify-structure.sh

index-self:
	uv run evidence-rag index . --ignore rag_ui_v2
