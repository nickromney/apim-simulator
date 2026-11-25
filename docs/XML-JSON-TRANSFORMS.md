# XML and JSON transforms — adapted subset

`xml-to-json` and `json-to-xml` convert the message in the section where they
run: the request body in inbound, the response body in outbound and on-error.
They are not allowed in backend. After a conversion the message's
`Content-Type` becomes `application/json; charset=utf-8` or
`application/xml; charset=utf-8`.

```xml
<outbound>
  <base />
  <xml-to-json kind="direct" apply="always" consider-accept-header="false" />
</outbound>
```

This is the hands-on lab's calculator step: an XML backend answers in JSON.

## When a conversion happens

| Attribute | Behaviour |
| --- | --- |
| `apply="always"` | Convert whatever the message's content type |
| `apply="content-type-xml"` / `"content-type-json"` | Convert only when the message's `Content-Type` contains `xml` / `json` |
| `consider-accept-header` (default `true`) | Convert only when the client's `Accept` header contains `json` (for `xml-to-json`) or `xml` (for `json-to-xml`). A missing or `*/*` Accept header does not count. `false` skips this check |

Both checks must pass. A message that does not parse is left unchanged, and the
trace records why a conversion was skipped.

## xml-to-json mapping

Microsoft documents `kind` only as "friendly to JavaScript developers" or
"reflects the original XML document's structure". APIM runs on .NET, so the
simulator follows Json.NET's XML conversion for both kinds:

| XML | JSON |
| --- | --- |
| `<order id="7">…</order>` (root) | `{"order": {"@id": "7", …}}` |
| `<note>gift</note>` | `"note": "gift"` |
| `<line sku="a">2</line>` | `"line": {"@sku": "a", "#text": "2"}` |
| two `<line>` siblings | `"line": [ … , … ]` |
| `<empty />` | `"empty": null` |
| `xmlns:s="urn:s"` and `<s:Body>` | `"@xmlns:s": "urn:s"`, `"s:Body"` |

Values stay strings. `always-array-child-elements="true"` makes every child
element an array. The XML declaration is dropped.

## json-to-xml mapping

| JSON | XML |
| --- | --- |
| One top-level property | That property is the root element |
| Several top-level properties, or a top-level array | Wrapped in `<Document>` (array items as `<Item>`) |
| `"a": 1`, `"t": true`, `"n": null` | `<a>1</a>`, `<t>true</t>`, `<n />` |
| `"b": [1, 2]` | `<b>1</b><b>2</b>` |
| `"@id": "x"` or properties inside `attribute-block-name` | Attributes |
| `"#text": "x"` | Element text |
| Properties named `namespace-prefix` or starting `namespace-prefix:` | Namespace declarations |
| Names that are not valid XML | Encoded as .NET does, for example `c d` → `c_x0020_d` |

`namespace-separator` (default `_`) becomes `:` only after a prefix declared
with `namespace-prefix`, so ordinary `snake_case` names are kept. `parse-date`
is accepted; dates are copied as written in both modes.

Microsoft's example output omits the `v1:QueryItem` element that its input
contains. The simulator keeps it.

## Differences from APIM

- The mappings are inferred from Json.NET, not from Microsoft documentation.
- `javascript-friendly` and `direct` produce the same output.
- `parse-date="true"` does not reformat dates.

References: [xml-to-json](https://learn.microsoft.com/en-us/azure/api-management/xml-to-json-policy),
[json-to-xml](https://learn.microsoft.com/en-us/azure/api-management/json-to-xml-policy).
Evidence: `tests/test_policy_xml_json.py` (`POLICY-XML-JSON`).
