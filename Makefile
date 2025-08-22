PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: venv upgrade-pip install run fmt clean

venv:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt || true
	$(PIP) install -e . || true

upgrade-pip:
	$(PIP) install --upgrade pip

install:
	$(PIP) install -r requirements.txt

run:
	$(PY) cursor_notifier.py $(ARGS)

build:
	$(PY) -m pip install --upgrade build
	$(PY) -m build

publish-test:
	$(PY) -m pip install --upgrade twine
	$(PY) -m twine upload --repository testpypi dist/*

publish:
	$(PY) -m pip install --upgrade twine
	$(PY) -m twine upload dist/*

clean:
	rm -rf .venv __pycache__ **/__pycache__ .pytest_cache