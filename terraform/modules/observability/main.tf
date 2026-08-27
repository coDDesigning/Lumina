locals {
  alarms = {
    alb_5xx = {
      namespace           = "AWS/ApplicationELB"
      metric_name         = "HTTPCode_Target_5XX_Count"
      statistic           = "Sum"
      extended_statistic  = null
      period              = 300
      evaluation_periods  = 1
      datapoints_to_alarm = 1
      threshold           = 5
      comparison_operator = "GreaterThanOrEqualToThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = { LoadBalancer = var.alb_arn_suffix }
    }
    alb_latency = {
      namespace           = "AWS/ApplicationELB"
      metric_name         = "TargetResponseTime"
      statistic           = null
      extended_statistic  = "p95"
      period              = 300
      evaluation_periods  = 2
      datapoints_to_alarm = 2
      threshold           = 2
      comparison_operator = "GreaterThanThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = { LoadBalancer = var.alb_arn_suffix }
    }
    unhealthy_targets = {
      namespace           = "AWS/ApplicationELB"
      metric_name         = "UnHealthyHostCount"
      statistic           = "Maximum"
      extended_statistic  = null
      period              = 60
      evaluation_periods  = 2
      datapoints_to_alarm = 2
      threshold           = 1
      comparison_operator = "GreaterThanOrEqualToThreshold"
      treat_missing_data  = "breaching"
      dimensions = {
        LoadBalancer = var.alb_arn_suffix
        TargetGroup  = var.target_group_arn_suffix
      }
    }
    api_cpu = {
      namespace           = "AWS/ECS"
      metric_name         = "CPUUtilization"
      statistic           = "Average"
      extended_statistic  = null
      period              = 300
      evaluation_periods  = 3
      datapoints_to_alarm = 2
      threshold           = 80
      comparison_operator = "GreaterThanThreshold"
      treat_missing_data  = "breaching"
      dimensions = {
        ClusterName = var.ecs_cluster_name
        ServiceName = var.api_service_name
      }
    }
    worker_missing = {
      namespace           = "ECS/ContainerInsights"
      metric_name         = "RunningTaskCount"
      statistic           = "Minimum"
      extended_statistic  = null
      period              = 60
      evaluation_periods  = 2
      datapoints_to_alarm = 2
      threshold           = 1
      comparison_operator = "LessThanThreshold"
      treat_missing_data  = "breaching"
      dimensions = {
        ClusterName = var.ecs_cluster_name
        ServiceName = var.worker_service_name
      }
    }
    rds_cpu = {
      namespace           = "AWS/RDS"
      metric_name         = "CPUUtilization"
      statistic           = "Average"
      extended_statistic  = null
      period              = 300
      evaluation_periods  = 3
      datapoints_to_alarm = 2
      threshold           = 80
      comparison_operator = "GreaterThanThreshold"
      treat_missing_data  = "breaching"
      dimensions          = { DBInstanceIdentifier = var.rds_instance_identifier }
    }
    rds_free_memory = {
      namespace           = "AWS/RDS"
      metric_name         = "FreeableMemory"
      statistic           = "Minimum"
      extended_statistic  = null
      period              = 300
      evaluation_periods  = 3
      datapoints_to_alarm = 2
      threshold           = 268435456
      comparison_operator = "LessThanThreshold"
      treat_missing_data  = "breaching"
      dimensions          = { DBInstanceIdentifier = var.rds_instance_identifier }
    }
    rds_free_storage = {
      namespace           = "AWS/RDS"
      metric_name         = "FreeStorageSpace"
      statistic           = "Minimum"
      extended_statistic  = null
      period              = 300
      evaluation_periods  = 3
      datapoints_to_alarm = 2
      threshold           = 5368709120
      comparison_operator = "LessThanThreshold"
      treat_missing_data  = "breaching"
      dimensions          = { DBInstanceIdentifier = var.rds_instance_identifier }
    }
    proxy_pinned = {
      namespace           = "AWS/RDS"
      metric_name         = "DatabaseConnectionsCurrentlySessionPinned"
      statistic           = "Maximum"
      extended_statistic  = null
      period              = 300
      evaluation_periods  = 2
      datapoints_to_alarm = 2
      threshold           = 10
      comparison_operator = "GreaterThanOrEqualToThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = { DBProxyName = var.rds_proxy_name }
    }
    queue_age = {
      namespace           = "Lumina/Worker"
      metric_name         = "OldestQueuedAgeSeconds"
      statistic           = "Maximum"
      extended_statistic  = null
      period              = 60
      evaluation_periods  = 3
      datapoints_to_alarm = 2
      threshold           = 300
      comparison_operator = "GreaterThanThreshold"
      treat_missing_data  = "breaching"
      dimensions          = { Service = "worker", Environment = var.environment }
    }
    failed_jobs = {
      namespace           = "Lumina/Worker"
      metric_name         = "JobsFailed"
      statistic           = "Sum"
      extended_statistic  = null
      period              = 300
      evaluation_periods  = 1
      datapoints_to_alarm = 1
      threshold           = 1
      comparison_operator = "GreaterThanOrEqualToThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = { Service = "worker", Environment = var.environment }
    }
    aged_tombstones = {
      namespace           = "Lumina/Worker"
      metric_name         = "AgedTombstones"
      statistic           = "Maximum"
      extended_statistic  = null
      period              = 300
      evaluation_periods  = 1
      datapoints_to_alarm = 1
      threshold           = 1
      comparison_operator = "GreaterThanOrEqualToThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = { Service = "course_purge", Environment = var.environment }
    }
    ai_provider_errors = {
      namespace           = "Lumina/AI"
      metric_name         = "ProviderErrors"
      statistic           = "Sum"
      extended_statistic  = null
      period              = 300
      evaluation_periods  = 1
      datapoints_to_alarm = 1
      threshold           = 5
      comparison_operator = "GreaterThanOrEqualToThreshold"
      treat_missing_data  = "notBreaching"
      dimensions          = { Service = "api", Environment = var.environment }
    }
  }
}

resource "aws_sns_topic" "alarms" {
  name = "${var.name_prefix}-alarms"
  tags = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each = local.alarms

  alarm_name          = "${var.name_prefix}-${replace(each.key, "_", "-")}"
  alarm_description   = "Lumina production alarm: ${replace(each.key, "_", " ")}"
  namespace           = each.value.namespace
  metric_name         = each.value.metric_name
  statistic           = each.value.statistic
  extended_statistic  = each.value.extended_statistic
  period              = each.value.period
  evaluation_periods  = each.value.evaluation_periods
  datapoints_to_alarm = each.value.datapoints_to_alarm
  threshold           = each.value.threshold
  comparison_operator = each.value.comparison_operator
  treat_missing_data  = each.value.treat_missing_data
  dimensions          = each.value.dimensions
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = "${var.name_prefix}-operations"
  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "API health"
          region = data.aws_region.current.name
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", var.alb_arn_suffix, { stat = "Sum" }],
            [".", "TargetResponseTime", ".", ".", { stat = "p95", yAxis = "right" }],
          ]
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "Worker queue"
          region = data.aws_region.current.name
          metrics = [
            ["Lumina/Worker", "QueuedJobs", "Service", "worker", "Environment", var.environment, { stat = "Maximum" }],
            [".", "OldestQueuedAgeSeconds", ".", ".", ".", ".", { stat = "Maximum", yAxis = "right" }],
            [".", "JobsFailed", ".", ".", ".", ".", { stat = "Sum" }],
          ]
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "Database and proxy"
          region = data.aws_region.current.name
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.rds_instance_identifier],
            [".", "DatabaseConnections", ".", "."],
            [".", "DatabaseConnectionsCurrentlySessionPinned", "DBProxyName", var.rds_proxy_name],
          ]
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "AI provider health"
          region = data.aws_region.current.name
          metrics = [
            ["Lumina/AI", "ProviderCalls", "Service", "api", "Environment", var.environment, "Provider", "*", { stat = "Sum" }],
            [".", "ProviderLatencyMs", ".", ".", ".", ".", ".", ".", { stat = "p95", yAxis = "right" }],
            [".", "ProviderErrors", ".", ".", ".", ".", ".", ".", { stat = "Sum" }],
          ]
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "Course purge & tombstones"
          region = data.aws_region.current.name
          metrics = [
            ["Lumina/Worker", "CoursesExamined", "Service", "course_purge", "Environment", var.environment, { stat = "Sum" }],
            [".", "CoursesPurged", ".", ".", ".", ".", { stat = "Sum" }],
            [".", "CoursesFailed", ".", ".", ".", ".", { stat = "Sum" }],
            [".", "AgedTombstones", ".", ".", ".", ".", { stat = "Maximum", yAxis = "right" }],
            [".", "OldestTombstoneAgeSeconds", ".", ".", ".", ".", { stat = "Maximum", yAxis = "right" }],
          ]
        }
      },
    ]
  })
}

data "aws_region" "current" {}
