variable "aws_region" {
  type        = string
  default     = "eu-west-1"
  description = "AWS region where the cluster is deployed."
}

variable "availability_zone" {
  type        = string
  default     = "eu-west-1a"
  description = "Single AZ used to minimize cost (no multi-AZ high availability)."
}

variable "aws_profile" {
  type        = string
  default     = "openshift-lab"
  description = "Profile from ~/.aws/credentials used by the AWS provider."
}

variable "cluster_name" {
  type        = string
  default     = "ocp4lab"
  description = "Short cluster name (used in resource names and DNS)."
}

variable "base_domain" {
  type        = string
  default     = "aws.example.com"
  description = "Base domain delegated to Route53."
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "public_subnet_cidr" {
  type    = string
  default = "10.0.1.0/24"
}

variable "private_subnet_cidr" {
  type    = string
  default = "10.0.2.0/24"
}

variable "control_plane_count" {
  type        = number
  default     = 3
  description = "Number of masters. Must be odd (etcd quorum)."
}
