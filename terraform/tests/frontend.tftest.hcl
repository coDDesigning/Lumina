mock_provider "aws" {}

variables {
  name_prefix                = "lumina-test"
  bucket_name                = "lumina-test-frontend-123456789012"
  cloudfront_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555"
  frontend_domain_name       = "app.example.com"
  frontend_dns_record_name   = "app.example.com"
  api_origin_domain_name     = "api-origin.example.com"
  frontend_dns_cutover       = true
  alb_dns_name               = "test-alb.us-east-1.elb.amazonaws.com"
  alb_zone_id                = "Z35SXDOTRQ7X7K"
  route53_zone_id            = "Z1234567890"
  tags                       = { Environment = "test" }
}

run "retain_alb_alias_before_cutover" {
  command = plan

  module {
    source = "./modules/frontend"
  }

  variables {
    frontend_dns_cutover = false
  }

  assert {
    condition     = one(aws_route53_record.frontend_a).alias[0].name == "test-alb.us-east-1.elb.amazonaws.com"
    error_message = "The first apply must retain the existing frontend A alias on the ALB."
  }

  assert {
    condition     = length(aws_route53_record.frontend_aaaa) == 0
    error_message = "The CloudFront AAAA alias must not exist before DNS cutover."
  }
}

run "frontend_delivery_contract" {
  command = plan

  module {
    source = "./modules/frontend"
  }

  assert {
    condition     = aws_s3_bucket_ownership_controls.this.rule[0].object_ownership == "BucketOwnerEnforced"
    error_message = "The frontend bucket must reject ACL ownership."
  }

  assert {
    condition = (
      aws_s3_bucket_public_access_block.this.block_public_acls &&
      aws_s3_bucket_public_access_block.this.block_public_policy &&
      aws_s3_bucket_public_access_block.this.ignore_public_acls &&
      aws_s3_bucket_public_access_block.this.restrict_public_buckets
    )
    error_message = "Every S3 public access block must remain enabled."
  }

  assert {
    condition = one([
      for origin in aws_cloudfront_distribution.this.origin : origin
      if origin.origin_id == "frontend-s3"
    ]).origin_path == "/current"
    error_message = "CloudFront must serve the atomic current release prefix."
  }

  assert {
    condition = [
      for behavior in aws_cloudfront_distribution.this.ordered_cache_behavior : behavior.path_pattern
    ] == ["/api", "/api/*", "/assets/*"]
    error_message = "The exact API paths must precede the static assets behavior."
  }

  assert {
    condition = alltrue([
      for behavior in slice(aws_cloudfront_distribution.this.ordered_cache_behavior, 0, 2) :
      toset(behavior.allowed_methods) == toset(["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"])
    ])
    error_message = "Both API behaviors must forward every supported HTTP method."
  }

  assert {
    condition = alltrue([
      for behavior in slice(aws_cloudfront_distribution.this.ordered_cache_behavior, 0, 2) :
      behavior.cache_policy_id == data.aws_cloudfront_cache_policy.caching_disabled.id &&
      behavior.origin_request_policy_id == data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    ])
    error_message = "Both API behaviors must disable caching and omit the viewer Host header."
  }

  assert {
    condition     = length(aws_cloudfront_distribution.this.custom_error_response) == 0
    error_message = "SPA routing must not use CloudFront custom error responses."
  }

  assert {
    condition = alltrue([
      for behavior in slice(aws_cloudfront_distribution.this.ordered_cache_behavior, 0, 2) :
      behavior.target_origin_id == "api-alb"
    ])
    error_message = "Both API behaviors must target the ALB origin."
  }

  assert {
    condition = one([
      for origin in aws_cloudfront_distribution.this.origin : origin
      if origin.origin_id == "api-alb"
    ]).custom_origin_config[0].origin_protocol_policy == "https-only"
    error_message = "CloudFront must connect to the API origin over HTTPS."
  }

  assert {
    condition = one([
      for origin in aws_cloudfront_distribution.this.origin : origin
      if origin.origin_id == "api-alb"
    ]).custom_origin_config[0].origin_read_timeout == 120
    error_message = "CloudFront must leave enough response time for the bounded hosted AI retry budget."
  }

  assert {
    condition = (
      aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].override &&
      aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_type_options[0].override &&
      aws_cloudfront_response_headers_policy.security.security_headers_config[0].frame_options[0].frame_option == "DENY" &&
      aws_cloudfront_response_headers_policy.security.security_headers_config[0].strict_transport_security[0].preload
    )
    error_message = "The frontend response policy must enforce the configured browser security headers."
  }

  assert {
    condition = one([
      for rule in aws_s3_bucket_lifecycle_configuration.this.rule : rule
      if rule.id == "expire-noncurrent-versions"
    ]).expiration[0].expired_object_delete_marker
    error_message = "Expired current-prefix delete markers must be removed after noncurrent versions expire."
  }

  assert {
    condition     = aws_cloudfront_origin_access_control.this.signing_behavior == "always" && aws_cloudfront_origin_access_control.this.signing_protocol == "sigv4"
    error_message = "The frontend S3 origin must require signed OAC requests."
  }

  assert {
    condition     = one(aws_cloudfront_distribution.this.default_cache_behavior[0].function_association).event_type == "viewer-request"
    error_message = "The static default behavior must own the SPA rewrite function."
  }

  assert {
    condition     = aws_cloudfront_distribution.this.viewer_certificate[0].minimum_protocol_version == "TLSv1.2_2021"
    error_message = "CloudFront viewers must use TLS 1.2 or newer."
  }

  assert {
    condition     = length(aws_route53_record.frontend_a) == 1 && length(aws_route53_record.frontend_aaaa) == 1
    error_message = "A configured Route53 zone must create both frontend aliases."
  }
}

run "reject_non_us_east_1_certificate" {
  command = plan

  module {
    source = "./modules/frontend"
  }

  variables {
    cloudfront_certificate_arn = "arn:aws:acm:eu-west-1:123456789012:certificate/11111111-2222-3333-4444-555555555555"
  }

  expect_failures = [var.cloudfront_certificate_arn]
}

run "reject_invalid_frontend_domain" {
  command = plan

  module {
    source = "./modules/frontend"
  }

  variables {
    frontend_domain_name = "App..example.com"
  }

  expect_failures = [var.frontend_domain_name]
}

run "reject_mismatched_frontend_record" {
  command = plan

  module {
    source = "./modules/frontend"
  }

  variables {
    frontend_dns_record_name = "other.example.com"
  }

  expect_failures = [var.frontend_dns_record_name]
}

run "ignore_frontend_record_when_dns_is_external" {
  command = plan

  module {
    source = "./modules/frontend"
  }

  variables {
    frontend_dns_record_name = "unused"
    route53_zone_id          = ""
  }

  assert {
    condition     = length(aws_route53_record.frontend_a) == 0 && length(aws_route53_record.frontend_aaaa) == 0
    error_message = "External DNS must not create or validate managed frontend aliases."
  }
}
