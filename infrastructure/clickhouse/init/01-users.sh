#!/bin/bash
# Creates TripScope databases, least-privilege users and the reader settings profile.
# Runs once, on the first start of an empty ClickHouse volume (docker-entrypoint-initdb.d).
set -euo pipefail

require_secret() {
    local name="$1" value="${!1:-}"
    if [[ ! "$value" =~ ^[A-Za-z0-9._~-]{16,128}$ ]]; then
        echo "ERROR: $name must be 16-128 chars of [A-Za-z0-9._~-]" >&2
        exit 1
    fi
}
require_secret CLICKHOUSE_WRITER_PASSWORD
require_secret CLICKHOUSE_READER_PASSWORD

WRITER_USER="${CLICKHOUSE_WRITER_USER:-tripscope_writer}"
READER_USER="${CLICKHOUSE_READER_USER:-tripscope_reader}"
QUERY_TIMEOUT="${ANALYTICS_QUERY_TIMEOUT_SECONDS:-30}"

clickhouse client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --multiquery <<SQL
CREATE DATABASE IF NOT EXISTS tripscope;
CREATE DATABASE IF NOT EXISTS tripscope_test;

-- Analytics/AI reader: SELECT only, hard server-side limits the client cannot raise.
CREATE SETTINGS PROFILE IF NOT EXISTS tripscope_reader_profile SETTINGS
    readonly = 2,
    max_execution_time = ${QUERY_TIMEOUT} MAX ${QUERY_TIMEOUT},
    max_result_rows = 100000 MAX 100000,
    result_overflow_mode = 'throw' CONST,
    max_memory_usage = 2000000000 MAX 2000000000,
    max_threads = 8 MAX 8;

CREATE USER IF NOT EXISTS ${READER_USER}
    IDENTIFIED WITH sha256_password BY '${CLICKHOUSE_READER_PASSWORD}'
    SETTINGS PROFILE tripscope_reader_profile;
GRANT SELECT ON tripscope.* TO ${READER_USER};
GRANT SELECT ON tripscope_test.* TO ${READER_USER};

-- Pipeline writer: DDL/DML on TripScope databases and read access to the lake named collection.
CREATE USER IF NOT EXISTS ${WRITER_USER}
    IDENTIFIED WITH sha256_password BY '${CLICKHOUSE_WRITER_PASSWORD}';
GRANT SELECT, INSERT, ALTER, CREATE TABLE, DROP TABLE, TRUNCATE, OPTIMIZE, SHOW TABLES ON tripscope.* TO ${WRITER_USER};
GRANT SELECT, INSERT, ALTER, CREATE TABLE, DROP TABLE, TRUNCATE, OPTIMIZE, SHOW TABLES ON tripscope_test.* TO ${WRITER_USER};
GRANT S3 ON *.* TO ${WRITER_USER};
GRANT NAMED COLLECTION ON tripscope_lake TO ${WRITER_USER};
SQL
echo "TripScope ClickHouse users initialised"
