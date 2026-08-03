variable "rhcos_ami" {
  type        = string
  default     = "ami-0754b5979bce4f62f"
  description = "RHCOS AMI for eu-west-1"
}

variable "master_instance_type" {
  type    = string
  default = "m5.xlarge"
}

variable "worker_instance_type" {
  type    = string
  default = "m5.large"
}

variable "bootstrap_instance_type" {
  type    = string
  default = "m5.xlarge"
}

# Spot is offered for the bootstrap and the workers only, never the control
# plane. In UPI there is no ControlPlaneMachineSet and no Machine API to
# replace a reclaimed master — Red Hat's docs are explicit that a cluster
# without preexisting control-plane Machines "cannot use a control plane
# machine set or enable the use of a control plane machine set after
# installation". Recovering a lost master means editing etcd membership by
# hand, so the saving is not worth it. ocplab's validation rejects the
# combination rather than leaving it to be discovered later.
#
# One-time requests with the default 'terminate' behaviour, deliberately.
# AWS supports persistent requests here (which would stop instead of
# terminate, preserving the disk), but a persistent request outlives the
# instance it launched and can relaunch it — inside a VPC that may be halfway
# through being destroyed. A predictable teardown is worth more here than a
# node that recovers itself; recovering a worker is `terraform apply`, which
# recreates it, plus the CSR approval ocplab already does.
variable "bootstrap_spot" {
  type    = bool
  default = false
}

variable "worker_spot" {
  type    = bool
  default = false
}

variable "worker_count" {
  type    = number
  default = 2
}

variable "master_volume_size" {
  type    = number
  default = 120
}

variable "master_volume_type" {
  type    = string
  default = "gp3"
}

variable "worker_volume_size" {
  type    = number
  default = 120
}

variable "worker_volume_type" {
  type    = string
  default = "gp3"
}

variable "bootstrap_volume_size" {
  type    = number
  default = 120
}

variable "bootstrap_volume_type" {
  type    = string
  default = "gp3"
}
