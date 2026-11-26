package provider

import (
	"encoding/json"
	"fmt"
	"net/url"
	"reflect"
	"slices"
	"strings"

	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/nickromney/apim-simulator/terraform-provider-apimsim/internal/client"
)

// A model struct declares a resource. Each field carries a `tfsdk` tag, which
// is both the Terraform attribute name and the JSON key, and optionally an
// `apim` tag giving its role:
//
//	(none)           sent on PUT and read back from GET
//	apim:"path"      a segment of the resource URL; never in the body
//	apim:"read_only" read back from GET, never sent
//
// The field tagged tfsdk:"id" is the Terraform ID: the path parameters joined
// by "/", which is also the import ID.
type role int

const (
	roleBody role = iota
	rolePath
	roleReadOnly
	roleID
)

type field struct {
	name  string
	index int
	role  role
}

// codec turns a model into its URL and request body, and folds a response
// back into the model. It is built once per resource type, from reflection.
type codec struct {
	path   string  // URL template, e.g. "apis/{api_id}/operations/{operation_id}"
	params []field // path fields, in template order
	fields []field
}

var supportedTypes = map[reflect.Type]bool{
	reflect.TypeFor[types.String]():  true,
	reflect.TypeFor[types.Bool]():    true,
	reflect.TypeFor[types.Int64]():   true,
	reflect.TypeFor[types.Float64](): true,
	reflect.TypeFor[types.List]():    true, // of strings
}

func newCodec(model reflect.Type, path string) (codec, error) {
	if model.Kind() != reflect.Struct {
		return codec{}, fmt.Errorf("model must be a struct, got %s", model)
	}
	c := codec{path: path}
	byName := map[string]field{}
	for i := range model.NumField() {
		f := model.Field(i)
		name := f.Tag.Get("tfsdk")
		if name == "" {
			return codec{}, fmt.Errorf("field %s has no tfsdk tag", f.Name)
		}
		if !supportedTypes[f.Type] {
			return codec{}, fmt.Errorf("field %s has unsupported type %s", f.Name, f.Type)
		}
		r, err := parseRole(name, f.Tag.Get("apim"))
		if err != nil {
			return codec{}, fmt.Errorf("field %s: %w", f.Name, err)
		}
		c.fields = append(c.fields, field{name: name, index: i, role: r})
		byName[name] = c.fields[len(c.fields)-1]
	}
	if byName["id"].role != roleID {
		return codec{}, fmt.Errorf(`model needs a field tagged tfsdk:"id"`)
	}

	declared := 0
	for _, f := range c.fields {
		if f.role == rolePath {
			declared++
		}
	}
	for _, segment := range strings.Split(path, "/") {
		if name, ok := parameter(segment); ok {
			f, found := byName[name]
			if !found || f.role != rolePath {
				return codec{}, fmt.Errorf(`path parameter {%s} needs a field tagged apim:"path"`, name)
			}
			c.params = append(c.params, f)
		}
	}
	if declared != len(c.params) {
		return codec{}, fmt.Errorf("model declares %d path fields but %q uses %d", declared, path, len(c.params))
	}
	return c, nil
}

func parseRole(name, tag string) (role, error) {
	switch {
	case name == "id" && tag == "":
		return roleID, nil
	case name == "id":
		return 0, fmt.Errorf("the id field takes no apim tag")
	case tag == "":
		return roleBody, nil
	case tag == "path":
		return rolePath, nil
	case tag == "read_only":
		return roleReadOnly, nil
	}
	return 0, fmt.Errorf("unknown apim tag %q", tag)
}

func parameter(segment string) (string, bool) {
	if strings.HasPrefix(segment, "{") && strings.HasSuffix(segment, "}") {
		return segment[1 : len(segment)-1], true
	}
	return "", false
}

// attributes lists every attribute name the model declares.
func (c codec) attributes() []string {
	names := make([]string, len(c.fields))
	for i, f := range c.fields {
		names[i] = f.name
	}
	return names
}

// url fills the path template from the model's path fields.
func (c codec) url(model any) (string, error) {
	values, err := c.pathValues(model)
	if err != nil {
		return "", err
	}
	segments := strings.Split(c.path, "/")
	next := 0
	for i, segment := range segments {
		if _, ok := parameter(segment); ok {
			segments[i] = url.PathEscape(values[next])
			next++
		}
	}
	return strings.Join(segments, "/"), nil
}

func (c codec) pathValues(model any) ([]string, error) {
	v := reflect.ValueOf(model).Elem()
	values := make([]string, len(c.params))
	for i, f := range c.params {
		s := v.Field(f.index).Interface().(types.String)
		if s.IsNull() || s.IsUnknown() || s.ValueString() == "" {
			return nil, fmt.Errorf("%s must be known and non-empty", f.name)
		}
		values[i] = s.ValueString()
	}
	return values, nil
}

// importAttributes splits an import ID into the attributes it sets.
func (c codec) importAttributes(id string) (map[string]string, error) {
	parts := strings.Split(id, "/")
	form := make([]string, len(c.params))
	for i, f := range c.params {
		form[i] = "{" + f.name + "}"
	}
	if len(parts) != len(c.params) || slices.Contains(parts, "") {
		return nil, fmt.Errorf("import ID %q must have the form %s", id, strings.Join(form, "/"))
	}
	attrs := map[string]string{"id": id}
	for i, f := range c.params {
		attrs[f.name] = parts[i]
	}
	return attrs, nil
}

// body is the PUT payload. Null attributes are sent as explicit JSON nulls, so
// removing an attribute from configuration clears it on the server even where
// the API merges a PUT into the stored resource. Unknown values are omitted.
func (c codec) body(model any) (client.Object, error) {
	v := reflect.ValueOf(model).Elem()
	out := client.Object{}
	for _, f := range c.fields {
		if f.role != roleBody {
			continue
		}
		value := v.Field(f.index).Interface().(attr.Value)
		if value.IsUnknown() {
			continue
		}
		encoded, err := toJSON(value)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", f.name, err)
		}
		out[f.name] = encoded
	}
	return out, nil
}

// apply folds a server response into the model and sets the ID. A masked
// secret keeps the model's own value, since the server will not reveal it. A
// computed value the server did not return becomes null: Terraform rejects a
// value that is still unknown after apply.
func (c codec) apply(model any, response client.Object) error {
	v := reflect.ValueOf(model).Elem()
	values, err := c.pathValues(model)
	if err != nil {
		return err
	}
	for _, f := range c.fields {
		target := v.Field(f.index)
		switch f.role {
		case roleID:
			target.Set(reflect.ValueOf(types.StringValue(strings.Join(values, "/"))))
		case roleBody, roleReadOnly:
			raw, present := response[f.name]
			if present && !client.IsMasked(raw) {
				decoded, err := fromJSON(target.Type(), raw)
				if err != nil {
					return fmt.Errorf("%s: %w", f.name, err)
				}
				target.Set(reflect.ValueOf(decoded))
			}
		}
		if target.Interface().(attr.Value).IsUnknown() {
			target.Set(reflect.ValueOf(null(target.Type())))
		}
	}
	return nil
}

func toJSON(value attr.Value) (any, error) {
	if value.IsNull() {
		return nil, nil
	}
	switch v := value.(type) {
	case types.String:
		return v.ValueString(), nil
	case types.Bool:
		return v.ValueBool(), nil
	case types.Int64:
		return v.ValueInt64(), nil
	case types.Float64:
		return v.ValueFloat64(), nil
	case types.List:
		items := make([]any, 0, len(v.Elements()))
		for _, element := range v.Elements() {
			encoded, err := toJSON(element)
			if err != nil {
				return nil, err
			}
			items = append(items, encoded)
		}
		return items, nil
	}
	return nil, fmt.Errorf("unsupported value %T", value)
}

func fromJSON(t reflect.Type, raw any) (attr.Value, error) {
	if raw == nil {
		return null(t), nil
	}
	mismatch := fmt.Errorf("server returned %T, want %s", raw, t)
	switch t {
	case reflect.TypeFor[types.String]():
		if s, ok := raw.(string); ok {
			return types.StringValue(s), nil
		}
	case reflect.TypeFor[types.Bool]():
		if b, ok := raw.(bool); ok {
			return types.BoolValue(b), nil
		}
	case reflect.TypeFor[types.Int64]():
		if n, ok := raw.(json.Number); ok {
			i, err := n.Int64()
			return types.Int64Value(i), err
		}
	case reflect.TypeFor[types.Float64]():
		if n, ok := raw.(json.Number); ok {
			f, err := n.Float64()
			return types.Float64Value(f), err
		}
	case reflect.TypeFor[types.List]():
		items, ok := raw.([]any)
		if !ok {
			return nil, mismatch
		}
		elements := make([]attr.Value, len(items))
		for i, item := range items {
			s, ok := item.(string)
			if !ok {
				return nil, fmt.Errorf("list item %d: server returned %T, want string", i, item)
			}
			elements[i] = types.StringValue(s)
		}
		list, diags := types.ListValue(types.StringType, elements)
		if diags.HasError() {
			return nil, fmt.Errorf("%v", diags)
		}
		return list, nil
	}
	return nil, mismatch
}

func null(t reflect.Type) attr.Value {
	switch t {
	case reflect.TypeFor[types.Bool]():
		return types.BoolNull()
	case reflect.TypeFor[types.Int64]():
		return types.Int64Null()
	case reflect.TypeFor[types.Float64]():
		return types.Float64Null()
	case reflect.TypeFor[types.List]():
		return types.ListNull(types.StringType)
	}
	return types.StringNull()
}
