output "bucket_names" {
  value = {
    codebase_snapshots = aws_s3_bucket.codebase_snapshots.bucket
    incident_logs      = aws_s3_bucket.incident_logs.bucket
    model_artifacts    = aws_s3_bucket.model_artifacts.bucket
  }
}
