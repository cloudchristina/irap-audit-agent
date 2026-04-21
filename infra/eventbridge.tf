resource "aws_cloudwatch_event_rule" "weekly_extraction" {
  name                = "irap-weekly-extraction"
  description         = "Trigger IRAP RDS extractor every Sunday night AEST"
  schedule_expression = "cron(0 14 ? * SUN *)"
}

resource "aws_cloudwatch_event_target" "extractor" {
  rule      = aws_cloudwatch_event_rule.weekly_extraction.name
  target_id = "irap-rds-extractor"
  arn       = aws_lambda_function.extractor.arn

  depends_on = [aws_lambda_permission.eventbridge_invoke_extractor]
}

resource "aws_lambda_permission" "eventbridge_invoke_extractor" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.extractor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly_extraction.arn
}
