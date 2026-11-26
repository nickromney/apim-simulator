// Package provider is the apimsim Terraform provider: it configures a running
// APIM simulator through its management API.
package provider

import (
	"cmp"
	"context"
	"os"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/provider/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/nickromney/apim-simulator/terraform-provider-apimsim/internal/client"
)

const defaultEndpoint = "http://localhost:8000"

type apimsimProvider struct {
	version string
}

var _ provider.Provider = (*apimsimProvider)(nil)

// New returns the provider factory providerserver.Serve expects.
func New(version string) func() provider.Provider {
	return func() provider.Provider { return &apimsimProvider{version: version} }
}

func (p *apimsimProvider) Metadata(_ context.Context, _ provider.MetadataRequest, resp *provider.MetadataResponse) {
	resp.TypeName = "apimsim"
	resp.Version = p.version
}

func (p *apimsimProvider) Schema(_ context.Context, _ provider.SchemaRequest, resp *provider.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "Configures a running APIM simulator through its management API.",
		Attributes: map[string]schema.Attribute{
			"endpoint": schema.StringAttribute{
				Optional:    true,
				Description: "Simulator base URL. Defaults to `APIMSIM_ENDPOINT`, then " + defaultEndpoint + ".",
			},
			"tenant_key": schema.StringAttribute{
				Optional:    true,
				Sensitive:   true,
				Description: "Tenant access key, sent as `X-Apim-Tenant-Key`. Defaults to `APIMSIM_TENANT_KEY`.",
			},
		},
	}
}

type providerConfig struct {
	Endpoint  types.String `tfsdk:"endpoint"`
	TenantKey types.String `tfsdk:"tenant_key"`
}

func (p *apimsimProvider) Configure(ctx context.Context, req provider.ConfigureRequest, resp *provider.ConfigureResponse) {
	var cfg providerConfig
	resp.Diagnostics.Append(req.Config.Get(ctx, &cfg)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if cfg.Endpoint.IsUnknown() || cfg.TenantKey.IsUnknown() {
		resp.Diagnostics.AddError(
			"Provider configuration is not known yet",
			"endpoint and tenant_key must be known at plan time; they cannot come from a resource that is not created yet.",
		)
		return
	}
	endpoint := cmp.Or(cfg.Endpoint.ValueString(), os.Getenv("APIMSIM_ENDPOINT"), defaultEndpoint)
	tenantKey := cmp.Or(cfg.TenantKey.ValueString(), os.Getenv("APIMSIM_TENANT_KEY"))

	c := client.New(endpoint, tenantKey, nil)
	resp.ResourceData = c
}

func (p *apimsimProvider) Resources(context.Context) []func() resource.Resource {
	return resources
}

func (p *apimsimProvider) DataSources(context.Context) []func() datasource.DataSource {
	return nil
}
