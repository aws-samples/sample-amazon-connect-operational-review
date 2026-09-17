.PHONY: init plan apply destroy fmt validate clean

init:
	terraform init

plan:
	terraform plan

apply:
	terraform apply

destroy:
	terraform destroy

fmt:
	terraform fmt -recursive

validate:
	terraform fmt -check -recursive
	terraform validate

clean:
	rm -rf .terraform .terraform.lock.hcl
	rm -f lambda_packages/*.zip

# Bootstrap the S3 state backend (one-time setup)
bootstrap-init:
	cd bootstrap && terraform init

bootstrap-apply:
	cd bootstrap && terraform apply

bootstrap-destroy:
	cd bootstrap && terraform destroy
