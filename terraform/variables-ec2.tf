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
