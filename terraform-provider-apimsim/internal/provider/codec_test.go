package provider

import (
	"context"
	"encoding/json"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/nickromney/apim-simulator/terraform-provider-apimsim/internal/client"
)

type childModel struct {
	ID       types.String `tfsdk:"id"`
	Parent   types.String `tfsdk:"parent_id" apim:"path"`
	Child    types.String `tfsdk:"child_id" apim:"path"`
	Name     types.String `tfsdk:"name"`
	Password types.String `tfsdk:"password"`
	Limit    types.Int64  `tfsdk:"limit"`
	Tags     types.List   `tfsdk:"tags"`
	Created  types.String `tfsdk:"created" apim:"read_only"`
}

func childCodec(t *testing.T) codec {
	t.Helper()
	c, err := newCodec(reflect.TypeFor[childModel](), "parents/{parent_id}/children/{child_id}")
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestURLEscapesPathParametersInTemplateOrder(t *testing.T) {
	m := &childModel{Parent: types.StringValue("p 1"), Child: types.StringValue("c/2")}

	got, err := childCodec(t).url(m)

	if err != nil || got != "parents/p%201/children/c%2F2" {
		t.Fatalf("url = %q, %v", got, err)
	}
}

func TestURLRejectsUnknownPathParameters(t *testing.T) {
	m := &childModel{Parent: types.StringValue("p"), Child: types.StringUnknown()}

	if _, err := childCodec(t).url(m); err == nil || !strings.Contains(err.Error(), "child_id") {
		t.Fatalf("err = %v", err)
	}
}

func TestBodySendsNullsSkipsUnknownsAndLeavesOutPathAndReadOnly(t *testing.T) {
	m := &childModel{
		Parent:   types.StringValue("p"),
		Child:    types.StringValue("c"),
		Name:     types.StringValue("n"),
		Password: types.StringNull(),
		Limit:    types.Int64Unknown(),
		Tags:     types.ListValueMust(types.StringType, nil),
		Created:  types.StringValue("ignored"),
	}

	body, err := childCodec(t).body(m)

	want := client.Object{"name": "n", "password": nil, "tags": []any{}}
	if err != nil || !reflect.DeepEqual(body, want) {
		t.Fatalf("body = %#v, %v", body, err)
	}
}

func TestApplyKeepsMaskedSecretsAndNullsUnreturnedComputedValues(t *testing.T) {
	m := &childModel{
		Parent:   types.StringValue("p"),
		Child:    types.StringValue("c"),
		Password: types.StringValue("hunter2"),
		Created:  types.StringUnknown(),
	}
	response := client.Object{
		"name":     "n",
		"password": "***",
		"limit":    json.Number("7"),
		"tags":     []any{"a", "b"},
	}

	if err := childCodec(t).apply(m, response); err != nil {
		t.Fatal(err)
	}

	if m.ID.ValueString() != "p/c" || m.Name.ValueString() != "n" || m.Limit.ValueInt64() != 7 {
		t.Errorf("id/name/limit = %v %v %v", m.ID, m.Name, m.Limit)
	}
	if m.Password.ValueString() != "hunter2" {
		t.Errorf("masked secret overwrote the configured value: %v", m.Password)
	}
	if !m.Created.IsNull() {
		t.Errorf("unreturned computed value should be null, got %v", m.Created)
	}
	if got := m.Tags.Elements(); len(got) != 2 {
		t.Errorf("tags = %v", got)
	}
}

func TestApplyRejectsWrongJSONTypes(t *testing.T) {
	m := &childModel{Parent: types.StringValue("p"), Child: types.StringValue("c")}

	err := childCodec(t).apply(m, client.Object{"limit": "seven"})

	if err == nil || !strings.Contains(err.Error(), "limit") {
		t.Fatalf("err = %v", err)
	}
}

func TestImportAttributes(t *testing.T) {
	c := childCodec(t)

	got, err := c.importAttributes("p/c")
	if err != nil || !reflect.DeepEqual(got, map[string]string{"id": "p/c", "parent_id": "p", "child_id": "c"}) {
		t.Fatalf("got %v, %v", got, err)
	}
	for _, bad := range []string{"p", "p/c/x", "p/", "/c"} {
		if _, err := c.importAttributes(bad); err == nil || !strings.Contains(err.Error(), "{parent_id}/{child_id}") {
			t.Errorf("%q: err = %v", bad, err)
		}
	}
}

func TestNewCodecRejectsMisdeclaredModels(t *testing.T) {
	type noID struct {
		Name types.String `tfsdk:"name"`
	}
	type badType struct {
		ID   types.String `tfsdk:"id"`
		Name string       `tfsdk:"name"`
	}
	type untagged struct {
		ID   types.String `tfsdk:"id"`
		Name types.String `tfsdk:"name" apim:"secret"`
	}
	type unusedPath struct {
		ID    types.String `tfsdk:"id"`
		Owner types.String `tfsdk:"owner_id" apim:"path"`
	}
	cases := map[string]struct {
		model reflect.Type
		path  string
		want  string
	}{
		"missing id":            {reflect.TypeFor[noID](), "things", "tfsdk:\"id\""},
		"unsupported type":      {reflect.TypeFor[badType](), "things", "unsupported type"},
		"unknown role":          {reflect.TypeFor[untagged](), "things", "unknown apim tag"},
		"undeclared parameter":  {reflect.TypeFor[unusedPath](), "things/{thing_id}", "{thing_id}"},
		"unused path parameter": {reflect.TypeFor[unusedPath](), "things", "declares 1 path fields"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := newCodec(tc.model, tc.path); err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("err = %v, want it to mention %q", err, tc.want)
			}
		})
	}
}

// Every catalogue entry's schema must declare exactly the model's attributes
// and pass the framework's own implementation checks.
func TestEveryResourceSchemaMatchesItsModel(t *testing.T) {
	ctx := context.Background()
	for _, factory := range resources {
		r := factory()
		var meta resource.MetadataResponse
		r.Metadata(ctx, resource.MetadataRequest{ProviderTypeName: "apimsim"}, &meta)
		t.Run(meta.TypeName, func(t *testing.T) {
			var resp resource.SchemaResponse
			r.Schema(ctx, resource.SchemaRequest{}, &resp)
			if diags := resp.Schema.ValidateImplementation(ctx); diags.HasError() {
				t.Fatalf("schema: %v", diags)
			}

			var schemaNames []string
			for name := range resp.Schema.Attributes {
				schemaNames = append(schemaNames, name)
			}
			modelNames := r.(interface{ attributes() []string }).attributes()
			slices.Sort(schemaNames)
			slices.Sort(modelNames)
			if !slices.Equal(schemaNames, modelNames) {
				t.Fatalf("schema %v\nmodel  %v", schemaNames, modelNames)
			}
		})
	}
}
