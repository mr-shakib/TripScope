#!/bin/sh
# Renders the SeaweedFS S3 identity file from environment variables, then starts the stock entrypoint.
# Two identities: the application (read/write on TripScope buckets) and ClickHouse (read-only).
set -eu

: "${S3_BUCKET:?}" "${S3_TEST_BUCKET:?}"
: "${S3_ACCESS_KEY:?}" "${S3_SECRET_KEY:?}"
: "${S3_CLICKHOUSE_ACCESS_KEY:?}" "${S3_CLICKHOUSE_SECRET_KEY:?}"

CONFIG=/tmp/tripscope-s3.json
umask 077
cat > "$CONFIG" <<JSON
{
  "identities": [
    {
      "name": "tripscope_app",
      "credentials": [{"accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}"}],
      "actions": [
        "Read:${S3_BUCKET}", "Write:${S3_BUCKET}", "List:${S3_BUCKET}", "Tagging:${S3_BUCKET}",
        "Read:${S3_TEST_BUCKET}", "Write:${S3_TEST_BUCKET}", "List:${S3_TEST_BUCKET}", "Tagging:${S3_TEST_BUCKET}"
      ]
    },
    {
      "name": "tripscope_clickhouse",
      "credentials": [{"accessKey": "${S3_CLICKHOUSE_ACCESS_KEY}", "secretKey": "${S3_CLICKHOUSE_SECRET_KEY}"}],
      "actions": ["Read:${S3_BUCKET}", "List:${S3_BUCKET}", "Read:${S3_TEST_BUCKET}", "List:${S3_TEST_BUCKET}"]
    }
  ]
}
JSON
chown seaweed:seaweed "$CONFIG" 2>/dev/null || true

exec /entrypoint.sh "$@" -s3.config="$CONFIG"
