terraform {
  required_version = "~> 1.14"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.8"
    }
  }

  backend "s3" {
    bucket = "irap-terraform-state-y6t2"
    key    = "irap-audit/terraform.tfstate"
    region = "ap-southeast-2"
  }
}

provider "aws" {
  region = "ap-southeast-2"
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
