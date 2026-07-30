# --- Bootstrap (temporary) ---
resource "aws_instance" "bootstrap" {
  ami                    = var.rhcos_ami
  instance_type          = var.bootstrap_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.master.id]
  iam_instance_profile   = aws_iam_instance_profile.master.name
  user_data              = local.bootstrap_user_data

  root_block_device {
    volume_size = var.bootstrap_volume_size
    volume_type = var.bootstrap_volume_type
  }

  tags = {
    Name = "${var.cluster_name}-bootstrap"
  }
}

resource "aws_lb_target_group_attachment" "bootstrap_api_ext" {
  target_group_arn = aws_lb_target_group.api_external.arn
  target_id        = aws_instance.bootstrap.id
  port             = 6443
}

resource "aws_lb_target_group_attachment" "bootstrap_api_int" {
  target_group_arn = aws_lb_target_group.api_internal.arn
  target_id        = aws_instance.bootstrap.id
  port             = 6443
}

resource "aws_lb_target_group_attachment" "bootstrap_mcs" {
  target_group_arn = aws_lb_target_group.machine_config.arn
  target_id        = aws_instance.bootstrap.id
  port             = 22623
}

# --- Masters (final) ---
resource "aws_instance" "master" {
  count                  = var.control_plane_count
  ami                    = var.rhcos_ami
  instance_type          = var.master_instance_type
  subnet_id              = aws_subnet.private.id
  vpc_security_group_ids = [aws_security_group.master.id]
  iam_instance_profile   = aws_iam_instance_profile.master.name
  user_data              = base64encode(file("${path.module}/../install-dir/master.ign"))

  root_block_device {
    volume_size = var.master_volume_size
    volume_type = var.master_volume_type
  }

  tags = {
    Name = "${var.cluster_name}-master-${count.index}"
  }
}

resource "aws_lb_target_group_attachment" "master_api_ext" {
  count            = var.control_plane_count
  target_group_arn = aws_lb_target_group.api_external.arn
  target_id        = aws_instance.master[count.index].id
  port             = 6443
}

resource "aws_lb_target_group_attachment" "master_api_int" {
  count            = var.control_plane_count
  target_group_arn = aws_lb_target_group.api_internal.arn
  target_id        = aws_instance.master[count.index].id
  port             = 6443
}

resource "aws_lb_target_group_attachment" "master_mcs" {
  count            = var.control_plane_count
  target_group_arn = aws_lb_target_group.machine_config.arn
  target_id        = aws_instance.master[count.index].id
  port             = 22623
}

# --- Workers ---
resource "aws_instance" "worker" {
  count                  = var.worker_count
  ami                    = var.rhcos_ami
  instance_type          = var.worker_instance_type
  subnet_id              = aws_subnet.private.id
  vpc_security_group_ids = [aws_security_group.worker.id]
  iam_instance_profile   = aws_iam_instance_profile.worker.name
  user_data              = base64encode(file("${path.module}/../install-dir/worker.ign"))

  root_block_device {
    volume_size = var.worker_volume_size
    volume_type = var.worker_volume_type
  }

  tags = {
    Name = "${var.cluster_name}-worker-${count.index}"
  }
}
