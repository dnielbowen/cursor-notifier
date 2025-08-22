PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: venv upgrade-pip install run fmt clean

venv:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt || true

upgrade-pip:
	$(PIP) install --upgrade pip

install:
	$(PIP) install -r requirements.txt

run:
	$(PY) cursor_notifier.py $(ARGS)

clean:
	rm -rf .venv __pycache__ **/__pycache__ .pytest_cache