#!/bin/bash
# Run all the regression tests.
DATA=./src/importers_tests
# supress DeprecationWarning for dependency import jpype in jaydebeapi
export PYTHONWARNINGS="ignore::DeprecationWarning:jpype"
python3 src/importers/hibiscus.py test $DATA/hibiscus
