"""Sync the database and raw dumps with Cloudflare R2 (S3-compatible).

Required environment variables:
  R2_ENDPOINT_URL       https://<account-id>.r2.cloudflarestorage.com
  R2_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY
  R2_BUCKET
"""

import os
from pathlib import Path

DB_KEY = "grocery.db"


def _client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def _bucket() -> str:
    return os.environ["R2_BUCKET"]


def pull_db(db_path: Path):
    client = _client()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        client.download_file(_bucket(), DB_KEY, str(db_path))
        print(f"pulled {DB_KEY} -> {db_path}")
    except client.exceptions.ClientError as err:
        if err.response["Error"]["Code"] in ("404", "NoSuchKey"):
            print("no remote database yet, starting fresh")
        else:
            raise


def push_db(db_path: Path):
    _client().upload_file(str(db_path), _bucket(), DB_KEY)
    print(f"pushed {db_path} -> {DB_KEY}")


def push_raw(chain: str, date: str):
    from .scrapers import common

    path = common.raw_path(chain, date)
    key = f"raw/{chain}/{path.name}"
    _client().upload_file(str(path), _bucket(), key)
    print(f"pushed {path} -> {key}")
