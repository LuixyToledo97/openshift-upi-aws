variable "route53_zone_id" {
  type        = string
  description = "Zone ID of the base_domain hosted zone"
}

resource "aws_route53_record" "api_external" {
  zone_id = var.route53_zone_id
  name    = "api.${var.cluster_name}.${var.base_domain}"
  type    = "A"

  alias {
    name                   = aws_lb.api_external.dns_name
    zone_id                = aws_lb.api_external.zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "api_internal" {
  zone_id = var.route53_zone_id
  name    = "api-int.${var.cluster_name}.${var.base_domain}"
  type    = "A"

  alias {
    name                   = aws_lb.api_internal.dns_name
    zone_id                = aws_lb.api_internal.zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "etcd_a" {
  count   = var.control_plane_count
  zone_id = var.route53_zone_id
  name    = "etcd-${count.index}.${var.cluster_name}.${var.base_domain}"
  type    = "A"
  ttl     = 60
  records = [aws_instance.master[count.index].private_ip]
}

resource "aws_route53_record" "etcd_srv" {
  zone_id = var.route53_zone_id
  name    = "_etcd-server-ssl._tcp.${var.cluster_name}.${var.base_domain}"
  type    = "SRV"
  ttl     = 60
  records = [
    for i in range(var.control_plane_count) :
    "0 10 2380 etcd-${i}.${var.cluster_name}.${var.base_domain}"
  ]
}

resource "aws_route53_zone" "private" {
  name          = "${var.cluster_name}.${var.base_domain}"
  force_destroy = true

  vpc {
    vpc_id = aws_vpc.main.id
  }

  tags = {
    Name = "${local.infra_id}-int"
  }
}

resource "aws_route53_record" "private_api" {
  zone_id = aws_route53_zone.private.zone_id
  name    = "api.${var.cluster_name}.${var.base_domain}"
  type    = "A"

  alias {
    name                   = aws_lb.api_internal.dns_name
    zone_id                = aws_lb.api_internal.zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "private_api_int" {
  zone_id = aws_route53_zone.private.zone_id
  name    = "api-int.${var.cluster_name}.${var.base_domain}"
  type    = "A"

  alias {
    name                   = aws_lb.api_internal.dns_name
    zone_id                = aws_lb.api_internal.zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "private_etcd_a" {
  count   = var.control_plane_count
  zone_id = aws_route53_zone.private.zone_id
  name    = "etcd-${count.index}.${var.cluster_name}.${var.base_domain}"
  type    = "A"
  ttl     = 60
  records = [aws_instance.master[count.index].private_ip]
}

resource "aws_route53_record" "private_etcd_srv" {
  zone_id = aws_route53_zone.private.zone_id
  name    = "_etcd-server-ssl._tcp.${var.cluster_name}.${var.base_domain}"
  type    = "SRV"
  ttl     = 60
  records = [
    for i in range(var.control_plane_count) :
    "0 10 2380 etcd-${i}.${var.cluster_name}.${var.base_domain}"
  ]
}
