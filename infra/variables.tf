variable "openai_api_key" {
  description = "Chave da API da OpenAI, gravada no Parameter Store via value_wo."
  type        = string
  sensitive   = true
  ephemeral   = true
  nullable    = false
}

variable "alert_email" {
  description = "E-mail que recebe os avisos do kill switch (assinatura SNS)."
  type        = string
  sensitive   = true
  nullable    = false

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email precisa ser um endereço de e-mail válido."
  }
}
