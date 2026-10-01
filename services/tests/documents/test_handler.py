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
BUCKET = "onetally-docs-test"
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


def s3_event(key=KEY, size=1000):
    return {
        "version": "0",
        "source": "aws.s3",
        "detail-type": "Object Created",
        "detail": {"bucket": {"name": BUCKET}, "object": {"key": key, "size": size}},
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


def upload(env, content_type="image/jpeg", body=b"\xff\xd8\xff\xe0jpeg"):
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
    env["s3"].put_object(Bucket=BUCKET, Key=KEY, Body=body, ContentType=content_type)


def run(env, event):
    return env["handler"].handler(event, FakeLambdaContext())


def doc(env):
    return env["table"].get_item(Key={"PK": f"USER#{USER}", "SK": f"DOC#{DOC}"})["Item"]


def expect_analyze_sync(env):
    env["textract"].add_response(
        "analyze_expense",
        fixture("textract", "grocery_sync.json"),
        {"Document": {"S3Object": {"Bucket": BUCKET, "Name": KEY}}},
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


def test_photo_upload_becomes_a_ready_document_with_a_deadline(env):
    upload(env)
    expect_analyze_sync(env)
    expect_bedrock(env, "grocery_correct.json")

    run(env, s3_event())

    item = doc(env)
    assert item["status"] == "ready", item.get("issues")
    assert item["vendor"] == "TARGET"
    assert item["totalCents"] == 2618
    assert item["promptVersion"]
    assert item["attempts"] == 1
    obligations = env["table"].query(
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :d",
        ExpressionAttributeValues={":d": "DUE#2026-10-28"},
    )["Items"]
    assert [o["kind"] for o in obligations] == ["return_by"]


def test_duplicate_event_makes_no_paid_calls(env):
    upload(env)
    expect_analyze_sync(env)
    expect_bedrock(env, "grocery_correct.json")
    run(env, s3_event())

    run(env, s3_event())  # nothing queued on the stubbers: any Textract/Bedrock call would raise

    assert doc(env)["status"] == "ready"


def test_event_for_unexpected_key_is_ignored(env):
    run(env, s3_event(key="tmp/something"))  # no exception, no calls


def test_extraction_failure_marks_document_failed(env):
    upload(env)
    expect_analyze_sync(env)
    expect_bedrock(env, "no_tool_call.json")

    run(env, s3_event())

    item = doc(env)
    assert item["status"] == "failed"
    assert "couldn't" in item["failureReason"].lower()


def test_oversized_object_is_refused_before_ocr(env):
    upload(env)

    run(env, s3_event(size=11 * 1024 * 1024))

    assert doc(env)["status"] == "failed"


# --- PDFs (async Textract via SNS) --------------------------------------------------------------


def test_pdf_starts_an_async_job(env):
    upload(env, content_type="application/pdf", body=fake_pdf(2))
    env["textract"].add_response(
        "start_expense_analysis",
        {"JobId": "job-123"},
        {
            "DocumentLocation": {"S3Object": {"Bucket": BUCKET, "Name": KEY}},
            "JobTag": DOC,
            "NotificationChannel": {"SNSTopicArn": TOPIC, "RoleArn": ROLE},
            "ClientRequestToken": DOC,
        },
    )

    run(env, s3_event())

    item = doc(env)
    assert item["status"] == "processing"
    assert item["textractJobId"] == "job-123"


def test_pdf_over_ten_pages_is_refused_before_ocr(env):
    upload(env, content_type="application/pdf", body=fake_pdf(11))

    run(env, s3_event())

    item = doc(env)
    assert item["status"] == "failed"
    assert "10 pages" in item["failureReason"]


def test_file_that_is_not_really_a_pdf_is_refused(env):
    upload(env, content_type="application/pdf", body=b"<html>not a pdf</html>")

    run(env, s3_event())

    assert doc(env)["status"] == "failed"


def test_sns_completion_fetches_results_and_saves(env):
    upload(env, content_type="application/pdf", body=fake_pdf(1))
    env["table"].update_item(
        Key={"PK": f"USER#{USER}", "SK": f"DOC#{DOC}"},
        UpdateExpression="SET #s = :p, textractJobId = :j",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":p": "processing", ":j": "job-123"},
    )
    result = fixture("textract", "grocery_sync.json") | {"JobStatus": "SUCCEEDED"}
    env["textract"].add_response("get_expense_analysis", result, {"JobId": "job-123"})
    expect_bedrock(env, "grocery_correct.json")

    run(env, sns_event())

    assert doc(env)["status"] == "ready"


def test_sns_failure_marks_document_failed(env):
    upload(env, content_type="application/pdf", body=fake_pdf(1))
    env["table"].update_item(
        Key={"PK": f"USER#{USER}", "SK": f"DOC#{DOC}"},
        UpdateExpression="SET #s = :p",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":p": "processing"},
    )

    run(env, sns_event(status="FAILED"))

    assert doc(env)["status"] == "failed"
