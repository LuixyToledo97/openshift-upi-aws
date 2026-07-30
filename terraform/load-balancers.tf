# --- External NLB (public API) ---
resource "aws_lb" "api_external" {
  name               = "${var.cluster_name}-api-ext"
  internal           = false
  load_balancer_type = "network"
  subnets            = [aws_subnet.public.id]

  tags = {
    Name = "${var.cluster_name}-api-ext"
  }
}

resource "aws_lb_target_group" "api_external" {
  name        = "${var.cluster_name}-api-ext-tg"
  port        = 6443
  protocol    = "TCP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  health_check {
    protocol            = "HTTPS"
    port                = 6443
    path                = "/readyz"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 10
  }

  tags = {
    Name = "${var.cluster_name}-api-ext-tg"
  }
}

resource "aws_lb_listener" "api_external" {
  load_balancer_arn = aws_lb.api_external.arn
  port              = 6443
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api_external.arn
  }
}

# --- Internal NLB (API + Machine Config, internal use) ---
resource "aws_lb" "api_internal" {
  name               = "${var.cluster_name}-api-int"
  internal           = true
  load_balancer_type = "network"
  subnets            = [aws_subnet.private.id]

  tags = {
    Name = "${var.cluster_name}-api-int"
  }
}

resource "aws_lb_target_group" "api_internal" {
  name        = "${var.cluster_name}-api-int-tg"
  port        = 6443
  protocol    = "TCP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  health_check {
    protocol            = "HTTPS"
    port                = 6443
    path                = "/readyz"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 10
  }

  tags = {
    Name = "${var.cluster_name}-api-int-tg"
  }
}

resource "aws_lb_listener" "api_internal" {
  load_balancer_arn = aws_lb.api_internal.arn
  port              = 6443
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api_internal.arn
  }
}

resource "aws_lb_target_group" "machine_config" {
  name        = "${var.cluster_name}-mcs-tg"
  port        = 22623
  protocol    = "TCP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  health_check {
    protocol            = "HTTPS"
    port                = 22623
    path                = "/healthz"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 10
  }

  tags = {
    Name = "${var.cluster_name}-mcs-tg"
  }
}

resource "aws_lb_listener" "machine_config" {
  load_balancer_arn = aws_lb.api_internal.arn
  port              = 22623
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.machine_config.arn
  }
}
