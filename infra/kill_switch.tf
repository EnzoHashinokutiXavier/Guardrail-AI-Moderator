resource "aws_sns_topic" "alertas" {
  name = "guardrails-alertas"
}

resource "aws_sns_topic_subscription" "alertas_email" {
  topic_arn = aws_sns_topic.alertas.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
