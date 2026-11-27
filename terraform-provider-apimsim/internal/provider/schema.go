package provider

import (
	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/booldefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/listdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

// Attribute constructors. Every attribute the server gives a default to is
// declared with that same default, so leaving it out of configuration resets
// it rather than leaving the old value behind. The round-trip contract test in
// tests/test_management_round_trip.py keeps the server side of that honest.

func required(doc string) schema.StringAttribute {
	return schema.StringAttribute{Required: true, Description: doc}
}

func optional(doc string) schema.StringAttribute {
	return schema.StringAttribute{Optional: true, Description: doc}
}

// secret is write-only on the server: it comes back masked, so Terraform keeps
// the configured value and cannot detect a change made outside it.
func secret(doc string) schema.StringAttribute {
	return schema.StringAttribute{Optional: true, Sensitive: true, Description: doc}
}

func defaulted(doc, value string) schema.StringAttribute {
	return schema.StringAttribute{Optional: true, Computed: true, Default: stringdefault.StaticString(value), Description: doc}
}

func flag(doc string, value bool) schema.BoolAttribute {
	return schema.BoolAttribute{Optional: true, Computed: true, Default: booldefault.StaticBool(value), Description: doc}
}

func optionalInt(doc string) schema.Int64Attribute {
	return schema.Int64Attribute{Optional: true, Description: doc}
}

func optionalList(doc string) schema.ListAttribute {
	return schema.ListAttribute{Optional: true, ElementType: types.StringType, Description: doc}
}

// adoptedList is a list another resource may also write, such as an API's
// products, which apimsim_product_api links. Left out of configuration it is
// unknown in any plan that updates the resource, so the PUT leaves it out and
// the server keeps what it holds. Carrying the state value forward instead
// would send stale links back. Set it to [] to clear it.
func adoptedList(doc string) schema.ListAttribute {
	return schema.ListAttribute{
		Optional:    true,
		Computed:    true,
		ElementType: types.StringType,
		Description: doc,
	}
}

// generatedSecret is a secret the server makes up when configuration leaves
// it out, such as a subscription key. It is read back in plain text, so a
// change made outside Terraform shows up in state.
func generatedSecret(doc string) schema.StringAttribute {
	return schema.StringAttribute{
		Optional:      true,
		Computed:      true,
		Sensitive:     true,
		Description:   doc,
		PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()},
	}
}

func defaultedList(doc string, values ...string) schema.ListAttribute {
	elements := make([]attr.Value, len(values))
	for i, v := range values {
		elements[i] = types.StringValue(v)
	}
	return schema.ListAttribute{
		Optional:    true,
		Computed:    true,
		ElementType: types.StringType,
		Default:     listdefault.StaticValue(types.ListValueMust(types.StringType, elements)),
		Description: doc,
	}
}

func computedString(doc string) schema.StringAttribute {
	return schema.StringAttribute{
		Computed:      true,
		Description:   doc,
		PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()},
	}
}

// resourceID is the Azure-style ID the simulator reports for most resources.
var resourceID = schema.StringAttribute{
	Computed:      true,
	Description:   "Azure-style resource ID reported by the simulator.",
	PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()},
}
