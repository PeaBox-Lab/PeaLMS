.PHONY: run test clean
run:
	LMS_DEMO_MODE=true python -m lms.server

test:
	python -m unittest discover -s tests -v

clean:
	rm -f "$${LMS_DB_PATH:-$$HOME/.corporate-lms/lms.db}"
