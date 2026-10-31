terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.69"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.8"
    }
  }

  backend "s3" {
    bucket       = "guardrails-tfstate-moderator"
    key          = "guardrails-moderator/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
