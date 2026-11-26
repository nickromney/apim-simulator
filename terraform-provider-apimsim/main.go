// Command terraform-provider-apimsim serves the apimsim provider to Terraform
// or OpenTofu.
package main

import (
	"context"
	"flag"
	"log"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"

	"github.com/nickromney/apim-simulator/terraform-provider-apimsim/internal/provider"
)

// version is set at build time with -ldflags "-X main.version=...".
var version = "dev"

func main() {
	debug := flag.Bool("debug", false, "run with support for debuggers such as delve")
	flag.Parse()

	err := providerserver.Serve(context.Background(), provider.New(version), providerserver.ServeOpts{
		Address: "registry.terraform.io/nickromney/apimsim",
		Debug:   *debug,
	})
	if err != nil {
		log.Fatal(err)
	}
}
