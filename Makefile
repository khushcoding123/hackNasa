# ORA targets. On Windows run these from Git Bash, or copy the commands.
PY ?= python

.PHONY: setup data demo test train stress figures

setup:
	$(PY) -m pip install -r requirements-dev.txt

data:
	$(PY) -m moonmesh.data
	$(PY) -m ora.data --earth
	$(PY) -m ora.lunar_windows

# The headline figure: terrain vs unit-disk vs rewired network, with 95% bands.
demo:
	$(PY) -m ora demo --seeds 200

test:
	$(PY) -m pytest tests -q

train:
	$(PY) -m ora.train

stress:
	$(PY) -m ora stress

figures: demo
	$(PY) -m ora benchmark
	$(PY) -m ora tall-towers
