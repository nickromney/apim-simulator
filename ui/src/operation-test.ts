export type Example = { name: string; summary?: string | null; description?: string | null; value?: unknown };

export type Parameter = {
  name: string;
  required: boolean;
  type: string;
  description?: string | null;
  default_value?: string | null;
  values?: string[];
  examples?: Example[];
  schema_id?: string | null;
};

type OperationTemplate = {
  url_template: string;
  template_parameters?: Parameter[];
  request?: { query_parameters: Parameter[] } | null;
};

export function operationTestPath(apiPath: string, urlTemplate: string) {
  return `/${[apiPath, urlTemplate.split("?", 1)[0]]
    .map((part) => part.replace(/^\/+|\/+$/g, ""))
    .filter(Boolean)
    .join("/")}`;
}

export function fillOperationTestPath(path: string, values: Record<string, string>) {
  return path.replace(/\{(\*?)([^{}]+)\}/g, (_, wildcard: string, name: string) => {
    const value = Object.hasOwn(values, name) ? values[name] : "value";
    return wildcard ? value.split("/").map(encodeURIComponent).join("/") : encodeURIComponent(value);
  });
}

export function operationTestParameters(operation: OperationTemplate) {
  const pathTemplate = operation.url_template.split("?", 1)[0];
  const metadata = new Map((operation.template_parameters ?? []).map((item) => [item.name.toLowerCase(), item]));
  const parameter = (name: string, item?: Parameter): Parameter =>
    item ? { ...item, name } : { name, type: "string", required: true };
  const pathNames = new Set(Array.from(pathTemplate.matchAll(/\{\*?([^{}]+)\}/g), ([, name]) => name));
  const path = Array.from(pathNames, (name) => parameter(name, metadata.get(name.toLowerCase())));
  for (const name of pathNames) metadata.delete(name.toLowerCase());

  const query = new Map((operation.request?.query_parameters ?? []).map((item) => [item.name.toLowerCase(), item]));
  const literalQuery = new Map<string, string>();
  const queryTemplate = operation.url_template.slice(pathTemplate.length);
  for (const [name, value] of new URLSearchParams(queryTemplate)) {
    if (!name) continue;
    const key = name.toLowerCase();
    const placeholder = /^\{([^{}]+)\}$/.exec(value);
    if (placeholder) {
      const source = placeholder[1].toLowerCase();
      query.set(key, parameter(name, query.get(key) ?? metadata.get(source)));
      metadata.delete(source);
    } else {
      literalQuery.set(name, value);
      query.delete(key);
      metadata.delete(key);
    }
  }
  for (const [name, item] of metadata) if (!query.has(name)) query.set(name, item);

  return { path, query: Array.from(query.values()), literalQuery: Object.fromEntries(literalQuery) };
}
