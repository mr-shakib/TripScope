#!/bin/bash
# Creates the isolated database used by integration tests. Runs once on an empty volume.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE DATABASE tripscope_test OWNER "$POSTGRES_USER";
SQL
