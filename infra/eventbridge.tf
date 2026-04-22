resource "aws_cloudwatch_event_rule" "weekly_extraction" {
  name                = "irap-weekly-extraction"
  description         = "Trigger IRAP RDS extractor every Sunday night AEST"
  schedule_expression = "cron(0 14 ? * SUN *)"
}

resource "aws_cloudwatch_event_target" "extractor" {
  rule      = aws_cloudwatch_event_rule.weekly_extraction.name
  target_id = "irap-rds-extractor"
  arn       = aws_lambda_function.extractor.arn

  # Pass the scheduled invocation time so the Lambda uses a deterministic
  # window [window_end - window_days, window_end) rather than wall-clock
  # NOW(). On retry EventBridge re-sends the same $.time, so the resulting
  # S3 key is identical and put_object is idempotent.
  input_transformer {
    input_paths = {
      time = "$.time"
    }
    input_template = <<EOF
{
  "window_end": <time>,
  "window_days": 7
}
EOF
  }

  depends_on = [aws_lambda_permission.eventbridge_invoke_extractor]
}

resource "aws_lambda_permission" "eventbridge_invoke_extractor" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.extractor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly_extraction.arn
}
