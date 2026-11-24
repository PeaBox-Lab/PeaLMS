.PHONY: run test migrate sweep compile clean

run:
	LMS_DEMO_MODE=true python -m lms.server

test:
	python -m unittest discover -s tests -v

migrate:
	python -m lms.server --migrate-only

sweep:
	python -m lms.server --sweep-notifications

compile:
	python -m compileall -q lms tests

clean:
	rm -f "$${LMS_DB_PATH:-$$HOME/.corporate-lms/lms.db}"
