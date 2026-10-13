"""The extract Lambda end to end: S3/EventBridge and SNS events in, DOC and OBL items out.

DynamoDB and S3 are moto; Textract and Bedrock are botocore Stubbers with saved responses.
A Stubber with nothing queued raises if called, which proves "no paid call happened".
"""

import importlib
import json
from dataclasses import dataclass
from pathlib import Path

import boto3
import pytest
from botocore.stub import ANY, Stubber

FIXTURES = Path(__file__).parent.parent / "fixtures"
BUCKET = "onebill-docs-test"
USER = "6f1c2a9e-1111-2222-3333-444455556666"
DOC = "1790000000000aaaaaaaa"
KEY = f"uploads/{USER}/{DOC}"
MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
TOPIC = "arn:aws:sns:us-east-1:123456789012:textract-done"
ROLE = "arn:aws:iam::123456789012:role/textract-publish"


@dataclass
class FakeLambdaContext:
    function_name: str = "extract"
    memory_limit_in_mb: int = 512
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:extract"
    aws_request_id: str = "req-1"


def fixture(*parts):
    return json.loads(FIXTURES.joinpath(*parts).read_text())


def s3_event(key=KEY, size=1000, version=None, time=None):
    obj = {"key": key, "size": size} | ({"version-id": version} if version else {})
    return {
        "version": "0",
        "source": "aws.s3",
        **({"time": time} if time else {}),
        "detail-type": "Object Created",
        "detail": {"bucket": {"name": BUCKET}, "object": obj},
    }


def sns_event(status="SUCCEEDED", job_id="job-123", key=KEY):
    message = {
        "JobId": job_id,
        "Status": status,
        "API": "StartExpenseAnalysis",
        "JobTag": DOC,
        "DocumentLocation": {"S3ObjectName": key, "S3Bucket": BUCKET},
    }
    return {"Records": [{"EventSource": "aws:sns", "Sns": {"Message": json.dumps(message)}}]}


def fake_pdf(pages):
    objects = b"".join(b"<< /Type /Page >>\n" for _ in range(pages))
    return b"%PDF-1.7\n<< /Type /Pages >>\n" + objects


@pytest.fixture
def env(table, monkeypatch):
    monkeypatch.setenv("BUCKET_NAME", BUCKET)
    monkeypatch.setenv("BEDROCK_MODEL_ID", MODEL)
    monkeypatch.setenv("TEXTRACT_TOPIC_ARN", TOPIC)
    monkeypatch.setenv("TEXTRACT_ROLE_ARN", ROLE)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
    import documents.handler as handler

    handler = importlib.reload(handler)
    textract = boto3.client("textract", region_name="us-east-1")
    bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
    monkeypatch.setattr(handler, "textract", textract)
    monkeypatch.setattr(handler, "bedrock", bedrock)
    with Stubber(textract) as t_stub, Stubber(bedrock) as b_stub:
        yield {"handler": handler, "table": table, "s3": s3, "textract": t_stub, "bedrock": b_stub}
        t_stub.assert_no_pending_responses()
        b_stub.assert_no_pending_responses()


def upload(env, content_type="image/jpeg", body=b"\xff\xd8\xff\xe0jpeg", job_id=None):
    """Put a pending DOC and its S3 object; returns the S3 version ID of the upload."""
    env["table"].put_item(
        Item={
            "PK": f"USER#{USER}",
            "SK": f"DOC#{DOC}",
            "type": "DOCUMENT",
            "docId": DOC,
            "status": "pending_upload",
            "s3Key": KEY,
            "contentType": content_type,
        }
    )
    put = env["s3"].put_object(Bucket=BUCKET, Key=KEY, Body=body, ContentType=content_type)
    return put["VersionId"]


def run(env, event):
    return env["handler"].handler(event, FakeLambdaContext())


def doc(env):
    return env["table"].get_item(Key={"PK": f"USER#{USER}", "SK": f"DOC#{DOC}"})["Item"]


def expect_analyze_sync(env, version):
    env["textract"].add_response(
        "analyze_expense",
        fixture("textract", "grocery_sync.json"),
        {"Document": {"S3Object": {"Bucket": BUCKET, "Name": KEY, "Version": version}}},
    )


def expect_bedrock(env, name):
    env["bedrock"].add_response(
        "converse",
        fixture("bedrock", name),
        {
            "modelId": MODEL,
            "system": ANY,
            "messages": ANY,
            "toolConfig": ANY,
            "inferenceConfig": ANY,
        },
    )


# --- photos (sync Textract) ---------------------------------------------------------------------


def test_photo_upload_becomes_a_ready_document_without_reminders(env):
    version = upload(env)
    expect_analyze_sync(env, version)  # OCR reads exactly the claimed upload
    expect_bedrock(env, "grocery_correct.json")

    run(env, s3_event(version=version))

    item = doc(env)
    assert item["status"] == "ready", item.get("issues")
    assert item["vendor"] == "TARGET"
    assert item["totalCents"] == 2618
    assert item["promptVersion"]
    assert item["attempts"] == 1
    assert item["s3VersionId"] == version
    obligations = env["table"].query(
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :d",
        ExpressionAttributeValues={":d": "DUE#2026-10-28"},
    )["Items"]
    assert obligations == []  # receipts make no reminders (return windows aren't tracked)


def stored_versions(env):
    listing = env["s3"].list_object_versions(Bucket=BUCKET, Prefix=KEY)
    return listing.get("Versions", [])


def test_the_photo_is_deleted_once_read(env):
    version = upload(env)
    expect_analyze_sync(env, version)
    expect_bedrock(env, "grocery_correct.json")

    run(env, s3_event(version=version))

    assert doc(env)["status"] == "ready"
    assert stored_versions(env) == []  # the exact version is gone: no copy left in the bucket


def test_a_refused_upload_is_deleted_too(env):
    version = upload(env, content_type="application/pdf", body=b"not a pdf")

    run(env, s3_event(version=version))

    assert doc(env)["status"] == "failed"
    assert stored_versions(env) == []


def test_duplicate_event_makes_no_paid_calls(env):
    version = upload(env)
    expect_analyze_sync(env, version)
    expect_bedrock(env, "grocery_correct.json")
    run(env, s3_event(version=version))

    run(
        env, s3_event(version=version)
    )  # nothing queued on the stubbers: any Textract/Bedrock call would raise

    assert doc(env)["status"] == "ready"


def test_daily_page_budget_refuses_before_ocr(env):
    from documents import store

    for _ in range(store.DAILY_PAGE_BUDGET):
        store.consume_page_budget(env["table"], 1, store_now())
    version = upload(env)

    run(env, s3_event(version=version))  # nothing queued: an OCR call would raise

    item = doc(env)
    assert item["status"] == "failed"
    assert "limit" in item["failureReason"].lower()


def store_now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def test_event_for_unexpected_key_is_ignored(env):
    run(env, s3_event(key="tmp/something"))  # no exception, no calls


def test_extraction_failure_marks_document_failed(env):
    version = upload(env)
    expect_analyze_sync(env, version)
    expect_bedrock(env, "no_tool_call.json")

    run(env, s3_event(version=version))

    item = doc(env)
    assert item["status"] == "failed"
    assert "couldn't" in item["failureReason"].lower()


def test_oversized_object_is_refused_before_ocr(env):
    upload(env)

    run(env, s3_event(size=11 * 1024 * 1024))

    assert doc(env)["status"] == "failed"


# --- PDFs (async Textract via SNS) --------------------------------------------------------------


def test_pdf_starts_an_async_job(env):
    version = upload(env, content_type="application/pdf", body=fake_pdf(2))
    env["textract"].add_response(
        "start_expense_analysis",
        {"JobId": "job-123"},
        {
            "DocumentLocation": {"S3Object": {"Bucket": BUCKET, "Name": KEY, "Version": version}},
            "JobTag": DOC,
            "NotificationChannel": {"SNSTopicArn": TOPIC, "RoleArn": ROLE},
            "ClientRequestToken": DOC,
        },
    )

    run(env, s3_event(version=version))

    item = doc(env)
    assert item["status"] == "processing"
    assert item["textractJobId"] == "job-123"
    assert len(stored_versions(env)) == 1  # kept until the async job has read it


def test_pdf_over_ten_pages_is_refused_before_ocr(env):
    upload(env, content_type="application/pdf", body=fake_pdf(11))

    run(env, s3_event())

    item = doc(env)
    assert item["status"] == "failed"
    assert "10 pages" in item["failureReason"]


def test_pdf_with_an_unknown_page_count_is_refused_before_ocr(env):
    upload(env, content_type="application/pdf", body=b"%PDF-1.7\n(no recognizable pages)")

    run(env, s3_event())

    item = doc(env)
    assert item["status"] == "failed"
    assert "how many pages" in item["failureReason"]


def test_file_that_is_not_really_a_pdf_is_refused(env):
    upload(env, content_type="application/pdf", body=b"<html>not a pdf</html>")

    run(env, s3_event())

    assert doc(env)["status"] == "failed"


def waiting_for_job(env, job_id="job-123"):
    upload(env, content_type="application/pdf", body=fake_pdf(1))
    env["table"].update_item(
        Key={"PK": f"USER#{USER}", "SK": f"DOC#{DOC}"},
        UpdateExpression="SET #s = :p, textractJobId = :j",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":p": "processing", ":j": job_id},
    )


def test_sns_completion_fetches_results_and_saves(env):
    waiting_for_job(env)
    result = fixture("textract", "grocery_sync.json") | {"JobStatus": "SUCCEEDED"}
    env["textract"].add_response("get_expense_analysis", result, {"JobId": "job-123"})
    expect_bedrock(env, "grocery_correct.json")

    run(env, sns_event())

    assert doc(env)["status"] == "ready"


def test_duplicate_sns_delivery_makes_no_paid_calls(env):
    waiting_for_job(env)
    result = fixture("textract", "grocery_sync.json") | {"JobStatus": "SUCCEEDED"}
    env["textract"].add_response("get_expense_analysis", result, {"JobId": "job-123"})
    expect_bedrock(env, "grocery_correct.json")
    run(env, sns_event())

    run(env, sns_event())  # document is no longer processing: ignored, no Textract/Bedrock call

    assert doc(env)["status"] == "ready"


def test_sns_message_for_a_different_job_is_ignored(env):
    waiting_for_job(env, job_id="job-123")

    run(env, sns_event(job_id="someone-elses-job"))

    assert doc(env)["status"] == "processing"


def test_too_many_pages_after_ocr_skips_the_model(env):
    waiting_for_job(env)
    result = fixture("textract", "grocery_sync.json") | {
        "JobStatus": "SUCCEEDED",
        "DocumentMetadata": {"Pages": 40},
    }
    env["textract"].add_response("get_expense_analysis", result, {"JobId": "job-123"})

    run(env, sns_event())  # no Bedrock response queued: a model call would raise

    item = doc(env)
    assert item["status"] == "failed"
    assert "10 pages" in item["failureReason"]


def test_sns_failure_marks_document_failed(env):
    waiting_for_job(env)

    run(env, sns_event(status="FAILED"))

    assert doc(env)["status"] == "failed"


def logged(caplog, message):
    """The structured fields of a Powertools log entry (its `extra` keys)."""
    for record in caplog.records:
        if record.getMessage() == message:
            return {k: v for k, v in vars(record).items() if not k.startswith("_")}
    raise AssertionError(f"{message!r} was not logged")


def test_stage_timings_are_logged_without_document_content(env, caplog):
    """Durations only, to find where scan latency goes (design: target p50 ≤ 8 s)."""
    version = upload(env)
    expect_analyze_sync(env, version)
    expect_bedrock(env, "grocery_correct.json")

    run(env, s3_event(version=version, time="2026-10-01T12:00:00Z"))

    entry = logged(caplog, "document_processed")
    timings = entry["timingsMs"]
    assert set(timings) >= {"queue", "textract", "model", "save", "handler"}
    assert all(isinstance(v, int) and v >= 0 for k, v in timings.items() if k != "model")
    assert isinstance(timings["model"], list)
    assert len(timings["model"]) == 1  # one Bedrock call, no repair
    assert "TARGET" not in json.dumps(entry, default=str)  # never document content


def test_page_budget_is_skipped_when_daily_limits_are_off(env, monkeypatch):
    from documents import store

    monkeypatch.setenv("DAILY_LIMITS", "off")  # read per event, so no reload is needed
    for _ in range(store.DAILY_PAGE_BUDGET):
        store.consume_page_budget(env["table"], 1, store_now())
    version = upload(env)
    expect_analyze_sync(env, version)
    expect_bedrock(env, "grocery_correct.json")

    run(env, s3_event(version=version))

    assert doc(env)["status"] == "ready"


# --- photo tiles next to the OCR text (phase 2c) ------------------------------------------------


def jpeg_bytes():
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (600, 800), "white").save(out, "JPEG")
    return out.getvalue()


def spy_on_extract(env, monkeypatch):
    seen = []
    real = env["handler"].extract

    def spy(client, model_id, expense, images=()):
        seen.append(list(images))
        return real(client, model_id, expense, images)

    monkeypatch.setattr(env["handler"], "extract", spy)
    return seen


def test_photo_tiles_are_off_by_default(env, monkeypatch):
    seen = spy_on_extract(env, monkeypatch)
    version = upload(env, body=jpeg_bytes())
    expect_analyze_sync(env, version)
    expect_bedrock(env, "grocery_correct.json")

    run(env, s3_event(version=version))

    assert seen == [[]]


def test_with_photo_tiles_on_the_photo_goes_with_the_text(env, monkeypatch):
    monkeypatch.setattr(env["handler"], "PHOTO_TILES", True)
    seen = spy_on_extract(env, monkeypatch)
    version = upload(env, body=jpeg_bytes())
    expect_analyze_sync(env, version)
    expect_bedrock(env, "grocery_correct.json")

    run(env, s3_event(version=version))

    assert len(seen[0]) == 1
    assert doc(env)["status"] == "ready"


def test_a_photo_that_cant_be_prepared_is_read_from_text_alone(env, monkeypatch):
    monkeypatch.setattr(env["handler"], "PHOTO_TILES", True)
    seen = spy_on_extract(env, monkeypatch)
    version = upload(env)  # not a real JPEG
    expect_analyze_sync(env, version)
    expect_bedrock(env, "grocery_correct.json")

    run(env, s3_event(version=version))

    assert seen == [[]]
    assert doc(env)["status"] == "ready"
