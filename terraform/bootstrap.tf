resource "aws_s3_bucket" "ignition" {
  bucket = "${var.cluster_name}-bootstrap-ignition-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name = "${var.cluster_name}-bootstrap-ignition"
  }
}

data "aws_caller_identity" "current" {}

resource "aws_s3_object" "bootstrap_ignition" {
  bucket = aws_s3_bucket.ignition.id
  key    = "bootstrap.ign"
  source = "${path.module}/../install-dir/bootstrap.ign"
  etag   = filemd5("${path.module}/../install-dir/bootstrap.ign")
}

data "aws_iam_policy_document" "bootstrap_ignition_bucket" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.ignition.arn}/*"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.master.arn, aws_iam_role.worker.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "ignition" {
  bucket = aws_s3_bucket.ignition.id
  policy = data.aws_iam_policy_document.bootstrap_ignition_bucket.json
}

locals {
  bootstrap_ignition_url = "s3://${aws_s3_bucket.ignition.id}/bootstrap.ign"

  # Small Ignition "wrapper" that downloads the real bootstrap.ign from S3
  bootstrap_user_data = base64encode(jsonencode({
    ignition = {
      version = "3.2.0"
      config = {
        merge = [{
          source = "s3://${aws_s3_bucket.ignition.id}/bootstrap.ign"
        }]
      }
    }
  }))
}
