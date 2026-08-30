data "aws_route53_zone" "lumina" {
  name = "lumina-study.com"
}
output "zone_id" {
  value = data.aws_route53_zone.lumina.zone_id
}
