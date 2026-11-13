import assert from "node:assert/strict";
import test from "node:test";
import { fillOperationTestPath, operationTestParameters, operationTestPath } from "../src/operation-test.ts";

test("path fields use the template spelling and include undocumented placeholders", () => {
  const id = { name: "widgetid", type: "integer", required: true, default_value: "42" };
  const result = operationTestParameters({
    url_template: "/widgets/{widgetId}/files/{*tail}",
    template_parameters: [id],
  });
  assert.deepEqual(result.path, [
    { ...id, name: "widgetId" },
    { name: "tail", type: "string", required: true },
  ]);
  assert.deepEqual(result.query, []);
});

test("operation test paths preserve wildcard separators and normalize a root API", () => {
  const path = operationTestPath("/", "/widgets/{widgetId}/files/{*tail}?mode=full");
  assert.equal(fillOperationTestPath(path, { widgetId: "42", tail: "dir/file #1" }), "/widgets/42/files/dir/file%20%231");
  assert.equal(operationTestPath("/", "/"), "/");
});

test("query fields use URL keys and preserve literal routing constraints", () => {
  const limit = { name: "pageSize", type: "integer", required: true, default_value: "25" };
  const result = operationTestParameters({
    url_template: "/widgets?mode=full%20detail&limit={pageSize}&empty=",
    template_parameters: [limit],
  });
  assert.deepEqual(result.query, [{ ...limit, name: "limit" }]);
  assert.deepEqual(result.literalQuery, { mode: "full detail", empty: "" });
});
