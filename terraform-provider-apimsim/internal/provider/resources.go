package provider

import (
	"maps"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/nickromney/apim-simulator/terraform-provider-apimsim/internal/client"
)

// The resource catalogue. Each entry is a model (the fields and their roles,
// see codec.go) and a spec (URL, docs, schema for the body attributes). Names
// and defaults follow the management API's request models in
// app/management_api.py.
var resources = []func() resource.Resource{
	register[productModel](spec{
		name: "product",
		path: "products/{product_id}",
		doc:  "A product: a set of APIs that callers subscribe to.",
		attributes: map[string]schema.Attribute{
			"name":                 required("Display name."),
			"description":          optional("Description shown in the developer portal."),
			"state":                defaulted("`published` or `not_published`.", "not_published"),
			"require_subscription": flag("Whether callers need a subscription key.", true),
			"approval_required":    flag("Whether an administrator approves new subscriptions.", false),
			"subscriptions_limit":  optionalInt("Maximum subscriptions per user."),
			"terms":                optional("Terms of use shown when subscribing."),
			"resource_id":          resourceID,
		},
	}),
	register[apiModel](spec{
		name: "api",
		path: "apis/{api_id}",
		doc:  "An API: a path on the gateway, forwarded to an upstream service.",
		attributes: map[string]schema.Attribute{
			"name":                      required("Display name."),
			"path":                      required("Gateway path the API is served under, without a leading slash."),
			"upstream_base_url":         required("Base URL requests are forwarded to."),
			"upstream_path_prefix":      defaulted("Prefix added to the upstream path.", ""),
			"protocols":                 defaultedList("Accepted protocols.", "http", "https"),
			"backend":                   optional("ID of an `apimsim_backend` to forward to instead of `upstream_base_url`."),
			"products":                  adoptedList("IDs of the products that include this API. Leave it out when `apimsim_product_api` manages the links; set it to `[]` to remove every link."),
			"api_version_set":           optional("ID of the version set this API belongs to."),
			"api_version":               optional("Version name within the version set."),
			"subscription_header_names": optionalList("Headers that carry the subscription key."),
			"gateways":                  defaultedList("Gateways that serve this API.", "managed"),
			"resource_id":               resourceID,
		},
	}),
	register[operationModel](spec{
		name: "api_operation",
		path: "apis/{api_id}/operations/{operation_id}",
		doc:  "An operation: one method and URL template within an API.",
		attributes: map[string]schema.Attribute{
			"name":         required("Display name."),
			"method":       defaulted("HTTP method.", "GET"),
			"url_template": required("URL template relative to the API path, such as `/items/{id}`."),
			"description":  optional("Description."),
			"resource_id":  resourceID,
		},
	}),
	register[apiPolicyModel](spec{
		name: "api_policy",
		path: "policies/api/{api_id}",
		doc:  "The policy document at API scope. Destroying it removes the policy, so the API only inherits.",
		attributes: map[string]schema.Attribute{
			"xml": required("Policy XML."),
		},
	}),
	register[productAPIModel](spec{
		name:       "product_api",
		path:       "products/{product_id}/apis/{api_id}",
		doc:        "Adds an API to a product. Use it instead of `products` on `apimsim_api`, not alongside it.",
		attributes: map[string]schema.Attribute{},
	}),
	register[subscriptionModel](spec{
		name: "subscription",
		path: "subscriptions/{subscription_id}",
		doc:  "A subscription: a pair of keys that grants access to its products, one API, or every API.",
		attributes: map[string]schema.Attribute{
			"name":           required("Display name."),
			"state":          defaulted("`active`, `suspended`, `cancelled`, `submitted`, `rejected` or `expired`.", "active"),
			"products":       defaultedList("IDs of the products the subscription grants. Set one scope: `products`, `api_id` or `all_apis`."),
			"api_id":         optional("ID of the one API the subscription grants."),
			"all_apis":       flag("Whether the subscription grants every API.", false),
			"service_scoped": flag("Whether this is the built-in all-access subscription.", false),
			"primary_key":    generatedSecret("Primary key. Generated when left out."),
			"secondary_key":  generatedSecret("Secondary key. Generated when left out."),
			"created_by":     computedString("Who created the subscription."),
			"resource_id":    resourceID,
		},
		decode: flattenSubscriptionKeys,
	}),
	register[backendModel](spec{
		name: "backend",
		path: "backends/{backend_id}",
		doc:  "A backend: a named upstream that APIs and policies can forward to.",
		attributes: map[string]schema.Attribute{
			"url":            defaulted("Base URL. Leave empty for a pool.", ""),
			"description":    optional("Description."),
			"type":           defaulted("`single` or `pool`.", "single"),
			"auth_type":      defaulted("`none`, `basic`, `managed_identity`, ….", "none"),
			"basic_username": optional("User name for basic authentication."),
			"basic_password": secret("Password for basic authentication. The simulator never returns it."),
			"resource_id":    resourceID,
		},
	}),
	register[namedValueModel](spec{
		name: "named_value",
		path: "named-values/{named_value_id}",
		doc:  "A named value that policies reference as `{{name}}`.",
		attributes: map[string]schema.Attribute{
			"display_name": required("Name policies use to reference the value."),
			"value":        secret("The value. When `secret` is true the simulator never returns it."),
			"secret":       flag("Whether the value is a secret.", false),
			"resource_id":  resourceID,
		},
	}),
	register[tagModel](spec{
		name: "tag",
		path: "tags/{tag_id}",
		doc:  "A tag that APIs, operations and products can carry.",
		attributes: map[string]schema.Attribute{
			"display_name": required("Display name."),
			"resource_id":  resourceID,
		},
	}),
	register[policyFragmentModel](spec{
		name: "policy_fragment",
		path: "policy-fragments/{fragment_id}",
		doc:  "A reusable policy fragment, included with `<include-fragment fragment-id=\"…\" />`.",
		attributes: map[string]schema.Attribute{
			"xml":         required("Fragment XML, rooted at `<fragment>`."),
			"resource_id": resourceID,
		},
	}),
}

type productModel struct {
	ID                  types.String `tfsdk:"id"`
	ProductID           types.String `tfsdk:"product_id" apim:"path"`
	Name                types.String `tfsdk:"name"`
	Description         types.String `tfsdk:"description"`
	State               types.String `tfsdk:"state"`
	RequireSubscription types.Bool   `tfsdk:"require_subscription"`
	ApprovalRequired    types.Bool   `tfsdk:"approval_required"`
	SubscriptionsLimit  types.Int64  `tfsdk:"subscriptions_limit"`
	Terms               types.String `tfsdk:"terms"`
	ResourceID          types.String `tfsdk:"resource_id" apim:"read_only"`
}

type apiModel struct {
	ID                      types.String `tfsdk:"id"`
	APIID                   types.String `tfsdk:"api_id" apim:"path"`
	Name                    types.String `tfsdk:"name"`
	Path                    types.String `tfsdk:"path"`
	UpstreamBaseURL         types.String `tfsdk:"upstream_base_url"`
	UpstreamPathPrefix      types.String `tfsdk:"upstream_path_prefix"`
	Protocols               types.List   `tfsdk:"protocols"`
	Backend                 types.String `tfsdk:"backend"`
	Products                types.List   `tfsdk:"products"`
	APIVersionSet           types.String `tfsdk:"api_version_set"`
	APIVersion              types.String `tfsdk:"api_version"`
	SubscriptionHeaderNames types.List   `tfsdk:"subscription_header_names"`
	Gateways                types.List   `tfsdk:"gateways"`
	ResourceID              types.String `tfsdk:"resource_id" apim:"read_only"`
}

type operationModel struct {
	ID          types.String `tfsdk:"id"`
	APIID       types.String `tfsdk:"api_id" apim:"path"`
	OperationID types.String `tfsdk:"operation_id" apim:"path"`
	Name        types.String `tfsdk:"name"`
	Method      types.String `tfsdk:"method"`
	URLTemplate types.String `tfsdk:"url_template"`
	Description types.String `tfsdk:"description"`
	ResourceID  types.String `tfsdk:"resource_id" apim:"read_only"`
}

type apiPolicyModel struct {
	ID    types.String `tfsdk:"id"`
	APIID types.String `tfsdk:"api_id" apim:"path"`
	XML   types.String `tfsdk:"xml"`
}

type productAPIModel struct {
	ID        types.String `tfsdk:"id"`
	ProductID types.String `tfsdk:"product_id" apim:"path"`
	APIID     types.String `tfsdk:"api_id" apim:"path"`
}

type subscriptionModel struct {
	ID             types.String `tfsdk:"id"`
	SubscriptionID types.String `tfsdk:"subscription_id" apim:"path"`
	Name           types.String `tfsdk:"name"`
	State          types.String `tfsdk:"state"`
	Products       types.List   `tfsdk:"products"`
	APIID          types.String `tfsdk:"api_id"`
	AllAPIs        types.Bool   `tfsdk:"all_apis"`
	ServiceScoped  types.Bool   `tfsdk:"service_scoped"`
	PrimaryKey     types.String `tfsdk:"primary_key"`
	SecondaryKey   types.String `tfsdk:"secondary_key"`
	CreatedBy      types.String `tfsdk:"created_by" apim:"read_only"`
	ResourceID     types.String `tfsdk:"resource_id" apim:"read_only"`
}

// flattenSubscriptionKeys lifts {"keys": {"primary", "secondary"}} into the
// primary_key and secondary_key fields a PUT accepts.
func flattenSubscriptionKeys(response client.Object) client.Object {
	keys, ok := response["keys"].(map[string]any)
	if !ok {
		return response
	}
	flat := maps.Clone(response)
	flat["primary_key"] = keys["primary"]
	flat["secondary_key"] = keys["secondary"]
	return flat
}

type backendModel struct {
	ID            types.String `tfsdk:"id"`
	BackendID     types.String `tfsdk:"backend_id" apim:"path"`
	URL           types.String `tfsdk:"url"`
	Description   types.String `tfsdk:"description"`
	Type          types.String `tfsdk:"type"`
	AuthType      types.String `tfsdk:"auth_type"`
	BasicUsername types.String `tfsdk:"basic_username"`
	BasicPassword types.String `tfsdk:"basic_password"`
	ResourceID    types.String `tfsdk:"resource_id" apim:"read_only"`
}

type namedValueModel struct {
	ID           types.String `tfsdk:"id"`
	NamedValueID types.String `tfsdk:"named_value_id" apim:"path"`
	DisplayName  types.String `tfsdk:"display_name"`
	Value        types.String `tfsdk:"value"`
	Secret       types.Bool   `tfsdk:"secret"`
	ResourceID   types.String `tfsdk:"resource_id" apim:"read_only"`
}

type tagModel struct {
	ID          types.String `tfsdk:"id"`
	TagID       types.String `tfsdk:"tag_id" apim:"path"`
	DisplayName types.String `tfsdk:"display_name"`
	ResourceID  types.String `tfsdk:"resource_id" apim:"read_only"`
}

type policyFragmentModel struct {
	ID         types.String `tfsdk:"id"`
	FragmentID types.String `tfsdk:"fragment_id" apim:"path"`
	XML        types.String `tfsdk:"xml"`
	ResourceID types.String `tfsdk:"resource_id" apim:"read_only"`
}
