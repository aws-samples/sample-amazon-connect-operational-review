# delete_json_data.py — DeleteJsonData Lambda for post-report cleanup
"""
DeleteJsonData Lambda handler that performs best-effort deletion of
JSON analyzer data from S3 after successful HTML report generation.

This Lambda lists and deletes objects under two S3 prefix patterns:
  - data/*/year=YYYY/month=MM/day=DD/{reviewId}.json (hive-partitioned analyzer data)
  - data/shared/{reviewId}/ (shared pre-fetched data from PrepareContext)

Deletion is best-effort: partial failures are logged but do not cause
the Lambda to raise an exception. The state machine's Catch block
provides an additional safety net, routing failures to SkipDelete.
"""

import logging

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# S3 DeleteObjects API limit per request
DELETE_BATCH_SIZE = 1000


def lambda_handler(event, context):
    """DeleteJsonData Lambda handler.

    Lists and deletes JSON data objects associated with a completed review.
    Returns success even on partial failures (best-effort deletion).

    Args:
        event: Input event from Step Functions. Contains:
            - reviewId (str): The unique review execution ID used as the
              object key suffix / prefix component.
            - s3ReportingBucket (str): The S3 bucket containing report data.
        context: Lambda context object.

    Returns:
        dict with status and count of objects deleted. Always returns
        successfully — exceptions are caught internally.
    """
    review_id = event.get("reviewId", "")
    bucket = event.get("s3ReportingBucket", "")

    logger.info(
        "DeleteJsonData invoked: reviewId=%s, bucket=%s",
        review_id,
        bucket,
    )

    if not review_id or not bucket:
        logger.warning(
            "Missing required input — reviewId=%s, bucket=%s. "
            "Returning success (nothing to delete).",
            review_id,
            bucket,
        )
        return {"status": "success", "deletedCount": 0, "message": "missing input"}

    s3_client = boto3.client("s3")
    total_deleted = 0

    # Collect objects from both prefix patterns
    prefixes = _build_prefixes(review_id)

    for prefix in prefixes:
        deleted = _delete_objects_under_prefix(s3_client, bucket, prefix)
        total_deleted += deleted

    logger.info(
        "DeleteJsonData complete: reviewId=%s, totalDeleted=%d",
        review_id,
        total_deleted,
    )

    return {
        "status": "success",
        "deletedCount": total_deleted,
        "reviewId": review_id,
    }


def _build_prefixes(review_id):
    """Build the list of S3 prefixes to scan for deletion.

    We search for objects matching:
      1. data/shared/{reviewId}/ — shared pre-fetched data
      2. Objects ending in {reviewId}.json under data/ — hive-partitioned
         analyzer outputs. We use the broad prefix "data/" and filter by
         suffix during listing, since hive partitions include variable
         year/month/day segments.

    Args:
        review_id: The unique review execution ID.

    Returns:
        List of (prefix, suffix_filter) tuples. suffix_filter is None
        when all objects under the prefix should be deleted, or a string
        suffix to match against each key.
    """
    return [
        # Shared data: delete everything under data/shared/{reviewId}/
        (f"data/shared/{review_id}/", None),
        # Hive-partitioned analyzer data: keys ending in {reviewId}.json
        ("data/", f"{review_id}.json"),
    ]


def _delete_objects_under_prefix(s3_client, bucket, prefix_spec):
    """List and delete all objects matching a prefix/suffix pattern.

    Args:
        s3_client: boto3 S3 client.
        bucket: S3 bucket name.
        prefix_spec: Tuple of (prefix, suffix_filter). If suffix_filter
            is None, all objects under prefix are deleted. Otherwise only
            objects whose key ends with suffix_filter are deleted.

    Returns:
        Number of objects successfully submitted for deletion.
    """
    prefix, suffix_filter = prefix_spec
    deleted_count = 0

    try:
        keys_to_delete = _list_matching_keys(s3_client, bucket, prefix, suffix_filter)

        if not keys_to_delete:
            logger.info(
                "No objects found under prefix=%s (filter=%s)", prefix, suffix_filter
            )
            return 0

        logger.info(
            "Found %d objects to delete under prefix=%s (filter=%s)",
            len(keys_to_delete),
            prefix,
            suffix_filter,
        )

        # Delete in batches of 1000 (S3 API limit)
        for i in range(0, len(keys_to_delete), DELETE_BATCH_SIZE):
            batch = keys_to_delete[i : i + DELETE_BATCH_SIZE]
            deleted_count += _delete_batch(s3_client, bucket, batch)

    except Exception as e:
        logger.warning(
            "Error during deletion for prefix=%s: %s. "
            "Continuing best-effort (deleted %d so far).",
            prefix,
            str(e),
            deleted_count,
        )

    return deleted_count


def _list_matching_keys(s3_client, bucket, prefix, suffix_filter):
    """List all object keys matching prefix and optional suffix filter.

    Paginates through the full listing to handle buckets with many objects.

    Args:
        s3_client: boto3 S3 client.
        bucket: S3 bucket name.
        prefix: S3 key prefix to list under.
        suffix_filter: Optional suffix to filter keys. If None, all keys
            under the prefix are included.

    Returns:
        List of S3 object keys matching the criteria.
    """
    keys = []
    paginator = s3_client.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if suffix_filter is None or key.endswith(suffix_filter):
                keys.append(key)

    return keys


def _delete_batch(s3_client, bucket, keys):
    """Delete a batch of S3 objects (up to 1000).

    Logs any per-object errors but does not raise exceptions.

    Args:
        s3_client: boto3 S3 client.
        bucket: S3 bucket name.
        keys: List of S3 object keys to delete (max 1000).

    Returns:
        Number of objects successfully deleted (batch size minus errors).
    """
    try:
        response = s3_client.delete_objects(
            Bucket=bucket,
            Delete={
                "Objects": [{"Key": k} for k in keys],
                "Quiet": True,
            },
        )

        errors = response.get("Errors", [])
        if errors:
            logger.warning(
                "Partial deletion failure: %d/%d objects failed. "
                "First error: Key=%s Code=%s Message=%s",
                len(errors),
                len(keys),
                errors[0].get("Key", ""),
                errors[0].get("Code", ""),
                errors[0].get("Message", ""),
            )
            return len(keys) - len(errors)

        return len(keys)

    except Exception as e:
        logger.warning(
            "delete_objects call failed for batch of %d: %s",
            len(keys),
            str(e),
        )
        return 0
