# EC2 Instance Connect Endpoint — the way `ocplab ssh` reaches the nodes.
#
# Masters and workers live in the PRIVATE subnet with no public IP, so the
# "SSH from my IP" rules in security-groups.tf, while correct, are unreachable
# from outside the VPC: there is simply no route in. Rather than give the nodes
# public addresses (which would mean moving them to the public subnet and
# giving up the topology this lab exists to reproduce) or standing up a bastion
# (another instance to pay for, patch, and order correctly during teardown),
# the endpoint acts as an identity-aware TCP proxy: traffic is authenticated
# and authorized by IAM before it ever reaches the VPC.
#
# It costs nothing. Per the AWS documentation: "There is no additional cost for
# using EC2 Instance Connect Endpoints. If you use an EC2 Instance Connect
# Endpoint to connect to an instance in a different Availability Zone, there is
# an additional charge for data transfer across Availability Zones." This lab
# is single-AZ, so even that doesn't apply.
#
# Created unconditionally, not on demand: being able to get a shell on a node
# is a debugging necessity, and a debugging tool you have to provision first is
# one you don't have when you need it.
#
# Worth knowing:
#   - It is the slowest resource in the whole stack to create: ~5 minutes,
#     measured on 2026-08-03, against seconds for everything else. AWS
#     documents this as "this can take a few minutes" — it provisions service
#     infrastructure, not just an ENI. That is ~5 minutes added to every
#     deploy, which is the accepted cost of creating it unconditionally.
#   - Only one endpoint is allowed per VPC per subnet.
#   - 20 concurrent connections, and a single TCP connection lasts at most 1h.
#   - The endpoint provisions its own ENI in the subnet via an EC2
#     service-linked role. CLAUDE.md trap #4 is about exactly this kind of ENI
#     blocking a destroy with DependencyViolation, so it was the open worry
#     when this was added — but verified on 2026-08-03: it tears down in ~13
#     seconds, in order, well before the VPC, leaving nothing behind. Slow to
#     create and fast to remove, which is the opposite of the router ELB.

resource "aws_security_group" "instance_connect" {
  name        = "${var.cluster_name}-eic-endpoint"
  description = "EC2 Instance Connect Endpoint: outbound SSH to the cluster nodes"
  vpc_id      = aws_vpc.main.id

  # Outbound only. The endpoint has no inbound rules of its own — reaching it
  # is an IAM decision made by AWS before traffic arrives, not a network one.
  egress {
    description = "SSH to any instance in the VPC"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = {
    Name = "${var.cluster_name}-eic-endpoint"
  }
}

resource "aws_ec2_instance_connect_endpoint" "main" {
  # Same subnet as the nodes: no cross-subnet routing to arrange, and the same
  # AZ, which is what keeps the data-transfer charge at zero.
  subnet_id          = aws_subnet.private.id
  security_group_ids = [aws_security_group.instance_connect.id]

  # Left off (the default) deliberately. With client IP preservation ON, the
  # node's security group would have to allow the operator's own client IP;
  # with it OFF, traffic arrives from the endpoint's ENI and the nodes' existing
  # "all internal VPC traffic" rule already covers it — so no security-group
  # change is needed on masters or workers.
  preserve_client_ip = false

  tags = {
    Name = "${var.cluster_name}-eic-endpoint"
  }
}
