locals {
  infra_id = jsondecode(file("${path.module}/../install-dir/metadata.json")).infraID
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project                                   = "openshift-lab"
      "kubernetes.io/cluster/${local.infra_id}" = "owned"
    }
  }
}
