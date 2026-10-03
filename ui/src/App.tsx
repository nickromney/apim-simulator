import { type FormEvent, startTransition, useEffect, useMemo, useRef, useState } from "react";

type PolicyScope = { scope_type: string; scope_name: string };
type Example = { name: string; summary?: string | null; description?: string | null; value?: unknown };
type Parameter = {
  name: string;
  required: boolean;
  type: string;
  description?: string | null;
  default_value?: string | null;
  values?: string[];
  examples?: Example[];
  schema_id?: string | null;
};
type Representation = { content_type: string; schema_id?: string | null; examples?: Example[] };
type OperationRequest = {
  description?: string | null;
  headers: Parameter[];
  query_parameters: Parameter[];
  representations: Representation[];
};
type OperationResponse = {
  status_code: number | "default";
  description?: string | null;
  headers: Parameter[];
  representations: Representation[];
};
type OperationSummary = {
  id: string;
  name: string;
  method: string;
  url_template: string;
  description?: string | null;
  policy_scope: PolicyScope;
  template_parameters?: Parameter[];
  request?: OperationRequest | null;
  responses?: OperationResponse[];
  api_version_set?: string | null;
  api_version?: string | null;
  subscription_header_names?: string[] | null;
  subscription_query_param_names?: string[] | null;
};
type ApiSummary = {
  id: string;
  name: string;
  path: string;
  upstream_base_url: string;
  upstream_path_prefix?: string;
  protocols?: string[];
  products: string[];
  translate_required_query_parameters?: "template" | "query";
  api_version_set?: string | null;
  api_version?: string | null;
  subscription_header_names?: string[] | null;
  subscription_query_param_names?: string[] | null;
  policy_scope: PolicyScope;
  operations: OperationSummary[];
  schemas?: ApiSchema[];
};
type ApiSchema = {
  id: string;
  content_type: string;
  value?: string | null;
  definitions?: Record<string, unknown>;
  components?: { schemas?: Record<string, unknown> };
};
type RouteSummary = {
  name: string;
  path_prefix: string;
  methods: string[] | null;
  upstream_base_url: string;
  upstream_path_prefix: string;
  product: string | null;
  products: string[];
  policy_scope?: PolicyScope;
};
type ProductSummary = {
  id: string;
  name: string;
  description?: string | null;
  state?: string;
  require_subscription: boolean;
  approval_required?: boolean;
};
type SubscriptionSummary = {
  id: string;
  name: string;
  state: string;
  products: string[];
  keys: { primary: string; secondary: string };
};
type BackendSummary = { id: string; url: string; description?: string | null; auth_type: string };
type SummaryPayload = {
  gateway_policy_scope: PolicyScope;
  apis: ApiSummary[];
  routes: RouteSummary[];
  products: ProductSummary[];
  subscriptions: SubscriptionSummary[];
  backends: BackendSummary[];
  subscription_key_names?: { header_names: string[]; query_param_names: string[] };
  api_version_sets?: {
    id: string;
    versioning_scheme: "Header" | "Query" | "Segment";
    version_header_name?: string | null;
    version_query_name?: string | null;
  }[];
};
type TraceItem = {
  trace_id: string;
  created_at: string;
  route: string;
  status: number;
  correlation_id: string;
  incoming_host: string;
  forwarded_host: string;
  forwarded_proto: string;
  client_ip: string;
  upstream_url: string | null;
  elapsed_ms?: number;
  policy_steps?: Record<string, unknown>[];
  [key: string]: unknown;
};
type ReplayResult = {
  request?: unknown;
  response: {
    status_code: number;
    headers: Record<string, string>;
    body_text: string | null;
    body_base64: string | null;
  };
  trace_id: string | null;
  trace: TraceItem | null;
};
type WorkflowTab = "policy" | "details" | "test" | "revisions" | "changelog";
type ManagementArea = "apis" | "subscriptions" | "traces";
type AuthenticationMode = "operator-token" | "local-tenant";
type ApiRevisionRecord = {
  id: string;
  description?: string | null;
  is_current?: boolean | null;
  is_online?: boolean | null;
  definition?: Record<string, unknown>;
};
type ApiReleaseRecord = { id: string; name?: string | null; notes?: string | null; revision: string };

const STORAGE_KEY = "apim-console-settings";
class ManagementResponseError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
const defaultHeaders = `{
  "x-apim-trace": "true"
}`;
const localDemoDefaults = {
  baseUrl: "http://localhost:8000",
  tenantKey: "local-dev-tenant-key",
  replayMethod: "GET",
  replayPath: "/api/health",
  replayHeaders: defaultHeaders,
  replayBody: "",
} as const;

function scopeId(scope: PolicyScope): string {
  return `${scope.scope_type}:${scope.scope_name}`;
}

function flattenPolicyScopes(summary: SummaryPayload | null, includeGateway = true): PolicyScope[] {
  if (!summary) return [];
  const scopes: PolicyScope[] = includeGateway ? [summary.gateway_policy_scope] : [];
  for (const product of summary.products) scopes.push({ scope_type: "product", scope_name: product.id });
  for (const api of summary.apis) {
    scopes.push(api.policy_scope);
    for (const operation of api.operations) scopes.push(operation.policy_scope);
  }
  for (const route of summary.routes) if (route.policy_scope) scopes.push(route.policy_scope);
  return scopes;
}

function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function policyStageNames(xml: string, stage: string): string[] {
  const content = xml.match(new RegExp(`<${stage}\\b[^>]*>([\\s\\S]*?)<\\/${stage}\\s*>`, "i"))?.[1];
  if (!content) return [];
  const names: string[] = [];
  let depth = 0;
  for (const match of content.matchAll(/<\/?([A-Za-z][\w:.-]*)\b[^>]*>/g)) {
    const token = match[0];
    const name = match[1];
    if (token.startsWith("</")) {
      depth = Math.max(0, depth - 1);
    } else {
      if (depth === 0 && name) names.push(name.replace(/^.+:/, ""));
      if (!token.endsWith("/>")) depth += 1;
    }
  }
  return names;
}

function policyDisplayName(name: string): string {
  return name.replace(/-/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function sampleSchema(schemaId: string | null | undefined, api: ApiSummary, seen = new Set<string>()): unknown {
  if (!schemaId || seen.has(schemaId)) return {};
  const schemas = api.schemas ?? [];
  const record = schemas.find((item) => item.id === schemaId);
  if (!record) return {};
  let root: unknown;
  try {
    root = record.value ? JSON.parse(record.value) : undefined;
  } catch {
    root = undefined;
  }
  const schema = (root && typeof root === "object" ? root : undefined) as Record<string, unknown> | undefined;
  const definitions = {
    ...((schema?.definitions as Record<string, unknown> | undefined) ?? {}),
    ...(record.definitions ?? {}),
  };
  const components = {
    ...((schema?.components && typeof schema.components === "object"
      ? ((schema.components as Record<string, unknown>).schemas as Record<string, unknown> | undefined)
      : undefined) ?? {}),
    ...(record.components?.schemas ?? {}),
  };
  const registry = { ...definitions, ...components };
  return sampleNode(registry[schemaId] ?? schema, registry, new Set([...seen, schemaId]));
}

function sampleNode(value: unknown, registry: Record<string, unknown>, seen: Set<string>): unknown {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const schema = value as Record<string, unknown>;
  if ("example" in schema) return schema.example;
  if ("default" in schema) return schema.default;
  if (Array.isArray(schema.enum) && schema.enum.length > 0) return schema.enum[0];
  if (typeof schema.$ref === "string") {
    const ref = schema.$ref.split("/").at(-1) ?? "";
    if (seen.has(ref)) return {};
    return sampleNode(registry[ref], registry, new Set([...seen, ref]));
  }
  if (schema.type === "object" || schema.properties) {
    const properties = (schema.properties ?? {}) as Record<string, unknown>;
    return Object.fromEntries(
      Object.entries(properties).map(([key, child]) => [key, sampleNode(child, registry, seen)]),
    );
  }
  if (schema.type === "array") return [];
  if (schema.type === "integer" || schema.type === "number") return 0;
  if (schema.type === "boolean") return false;
  return "";
}

function parameterValue(parameter: Parameter): string {
  if (parameter.default_value != null) return parameter.default_value;
  if (parameter.values?.length) return parameter.values[0];
  if (parameter.examples?.[0]?.value !== undefined) return String(parameter.examples[0].value);
  if (["integer", "number"].includes(parameter.type.toLowerCase())) return "1";
  if (parameter.type.toLowerCase() === "boolean") return "true";
  return "value";
}

function initialTestBody(operation: OperationSummary, api: ApiSummary): string {
  const representation = operation.request?.representations?.[0];
  const example = representation?.examples?.find((item) => item.value !== undefined)?.value;
  if (example !== undefined) return typeof example === "string" ? example : prettyJson(example);
  if (representation?.schema_id) return prettyJson(sampleSchema(representation.schema_id, api));
  return "";
}

function apiPathFor(api: ApiSummary, operation: OperationSummary): string {
  const pathTemplate = operation.url_template.split("?", 1)[0] ?? operation.url_template;
  return `/${[api.path, pathTemplate]
    .filter(Boolean)
    .map((part) => part.replace(/^\/+|\/+$/g, ""))
    .join("/")}`;
}

function apiRevisionPath(apiPath: string, revisionId: string, operationPath: string): string {
  const prefix = `/${apiPath.replace(/^\/+|\/+$/g, "")};rev=${encodeURIComponent(revisionId)}`;
  const suffix = operationPath.split("?", 1)[0]?.replace(/^\/+|\/+$/g, "") ?? "";
  return suffix ? `${prefix}/${suffix}` : prefix;
}

function App() {
  const [baseUrl, setBaseUrl] = useState(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "{}");
      return typeof stored?.baseUrl === "string" ? stored.baseUrl : localDemoDefaults.baseUrl;
    } catch {
      return localDemoDefaults.baseUrl;
    }
  });
  const [authenticationMode, setAuthenticationMode] = useState<AuthenticationMode>("operator-token");
  const [tenantKey, setTenantKey] = useState("");
  const [operatorToken, setOperatorToken] = useState("");
  const [scopedApiId, setScopedApiId] = useState("");
  const credential =
    authenticationMode === "operator-token" ? operatorToken.trim().replace(/^Bearer\s+/i, "") : tenantKey.trim();
  const [summary, setSummary] = useState<SummaryPayload | null>(null);
  const [traces, setTraces] = useState<TraceItem[]>([]);
  const [selectedTraceId, setSelectedTraceId] = useState("");
  const [selectedApiId, setSelectedApiId] = useState("");
  const [selectedOperationId, setSelectedOperationId] = useState("");
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState<WorkflowTab>("details");
  const [activeArea, setActiveArea] = useState<ManagementArea>("apis");
  const [selectedScopeId, setSelectedScopeId] = useState("");
  const [revisions, setRevisions] = useState<ApiRevisionRecord[]>([]);
  const [selectedRevisionId, setSelectedRevisionId] = useState("");
  const [revisionDescription, setRevisionDescription] = useState("");
  const [revisionOnline, setRevisionOnline] = useState(true);
  const [revisionDefinition, setRevisionDefinition] = useState<Record<string, unknown>>({});
  const [revisionOperationId, setRevisionOperationId] = useState("");
  const [revisionTestPath, setRevisionTestPath] = useState("");
  const [promoteWithChangelog, setPromoteWithChangelog] = useState(false);
  const [revisionChangelogNotes, setRevisionChangelogNotes] = useState("");
  const [revisionDirty, setRevisionDirty] = useState(false);
  const [newRevisionDirty, setNewRevisionDirty] = useState(false);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [newRevisionId, setNewRevisionId] = useState("");
  const [newRevisionDescription, setNewRevisionDescription] = useState("");
  const [releases, setReleases] = useState<ApiReleaseRecord[]>([]);
  const [releaseId, setReleaseId] = useState("");
  const [releaseNotes, setReleaseNotes] = useState("");
  const [releaseRevisionId, setReleaseRevisionId] = useState("");
  const [releaseDirty, setReleaseDirty] = useState(false);
  const [releaseLoading, setReleaseLoading] = useState(false);
  const [policyXml, setPolicyXml] = useState("");
  const [policyDirty, setPolicyDirty] = useState(false);
  const [policyLoading, setPolicyLoading] = useState(false);
  const [policyMessage, setPolicyMessage] = useState("");
  const [effectiveMode, setEffectiveMode] = useState(false);
  const [effectiveXml, setEffectiveXml] = useState("");
  const [effectiveProductId, setEffectiveProductId] = useState("");
  const [apiDraft, setApiDraft] = useState({
    id: "",
    name: "",
    path: "",
    upstream_base_url: "",
    upstream_path_prefix: "",
    translate_required_query_parameters: "template" as "template" | "query",
  });
  const [operationDraft, setOperationDraft] = useState({
    id: "",
    name: "",
    method: "GET",
    url_template: "/",
    description: "",
  });
  const [apiDirty, setApiDirty] = useState(false);
  const [operationDirty, setOperationDirty] = useState(false);
  const [isNewApi, setIsNewApi] = useState(false);
  const [apiCreationMode, setApiCreationMode] = useState<"choose" | "http" | "openapi">("choose");
  const [openApiUrl, setOpenApiUrl] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [importFormat, setImportFormat] = useState("openapi+json");
  const [importValue, setImportValue] = useState("");
  const [importUpstream, setImportUpstream] = useState("");
  const [importDirty, setImportDirty] = useState(false);
  const [testPath, setTestPath] = useState("");
  const [testPathParams, setTestPathParams] = useState<Record<string, string>>({});
  const [testQuery, setTestQuery] = useState<Record<string, string>>({});
  const [testHeaders, setTestHeaders] = useState<Record<string, string>>({ "x-apim-trace": "true" });
  const [testBody, setTestBody] = useState("");
  const [testSubscriptionKey, setTestSubscriptionKey] = useState("");
  const [manualMethod, setManualMethod] = useState("GET");
  const [manualPath, setManualPath] = useState("/api/health");
  const [manualHeaders, setManualHeaders] = useState(`{"x-apim-trace":"true"}`);
  const [manualBody, setManualBody] = useState("");
  const [replayResult, setReplayResult] = useState<ReplayResult | null>(null);
  const [statusMessage, setStatusMessage] = useState("Connect to the simulator to load the console.");
  const [busy, setBusy] = useState(false);
  const scopeRequest = useRef(0);
  const dashboardRequest = useRef(0);
  const revisionRequest = useRef(0);
  const releaseRequest = useRef(0);
  const connectionControl = useRef<HTMLDetailsElement>(null);
  const authenticationFailed = useRef(false);

  const scopes = useMemo(() => flattenPolicyScopes(summary, !scopedApiId.trim()), [summary, scopedApiId]);
  const selectedApi = summary?.apis.find((api) => api.id === selectedApiId) ?? null;
  const selectedOperation = selectedApi?.operations.find((operation) => operation.id === selectedOperationId) ?? null;
  const revisionOperationMap =
    (revisionDefinition.operations as Record<string, Record<string, unknown>> | undefined) ?? {};
  const selectedRevisionOperation = revisionOperationMap[revisionOperationId] ?? null;
  const selectedRevisionIsCurrent = Boolean(
    revisions.find((revision) => revision.id === selectedRevisionId)?.is_current,
  );
  const selectedTrace = traces.find((trace) => trace.trace_id === selectedTraceId) ?? traces[0] ?? null;
  const hasUnsaved =
    policyDirty || apiDirty || operationDirty || importDirty || revisionDirty || newRevisionDirty || releaseDirty;
  const revisionsBusy = revisionLoading || releaseLoading || busy;
  const filteredApis = (summary?.apis ?? []).filter((api) => {
    const query = search.trim().toLowerCase();
    return (
      !query ||
      `${api.id} ${api.name} ${api.path} ${api.operations.map((operation) => `${operation.id} ${operation.name} ${operation.method} ${operation.url_template}`).join(" ")}`
        .toLowerCase()
        .includes(query)
    );
  });

  useEffect(() => {
    try {
      // Replace legacy settings too, removing any previously persisted credentials.
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ baseUrl }));
    } catch {
      // Storage restrictions must not prevent an in-memory connection.
    }
  }, [baseUrl]);

  useEffect(() => {
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!hasUnsaved) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [hasUnsaved]);

  useEffect(() => {
    if (!selectedApi || !selectedOperation) return;
    const pathParameters = (selectedOperation.template_parameters ?? []).filter((parameter) =>
      selectedOperation.url_template.split("?", 1)[0]?.includes(`{${parameter.name}}`),
    );
    const queryParameters = selectedOperation.request?.query_parameters ?? [];
    const nextPathParams = Object.fromEntries(
      pathParameters.map((parameter) => [parameter.name, parameterValue(parameter)]),
    );
    const nextQuery = Object.fromEntries(
      queryParameters.map((parameter) => [parameter.name, parameterValue(parameter)]),
    );
    for (const parameter of selectedOperation.template_parameters ?? []) {
      if (!pathParameters.some((item) => item.name === parameter.name))
        nextQuery[parameter.name] ??= parameterValue(parameter);
    }
    const nextHeaders = Object.fromEntries([
      ["x-apim-trace", "true"],
      ...(selectedOperation.request?.headers ?? []).map(
        (parameter) => [parameter.name, parameterValue(parameter)] as const,
      ),
    ]);
    setTestPath(apiPathFor(selectedApi, selectedOperation));
    setTestPathParams(nextPathParams);
    setTestQuery(nextQuery);
    setTestHeaders(nextHeaders);
    setTestBody(initialTestBody(selectedOperation, selectedApi));
    setTestSubscriptionKey("");
  }, [selectedApi, selectedOperation]);

  async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
    const headers = new Headers(init?.headers);
    headers.delete("X-Apim-Tenant-Key");
    headers.delete("Authorization");
    if (!credential)
      throw new Error(
        authenticationMode === "operator-token"
          ? "Enter a signed operator token to connect."
          : "Enter a local tenant key to connect.",
      );
    if (authenticationMode === "operator-token") headers.set("Authorization", `Bearer ${credential}`);
    else headers.set("X-Apim-Tenant-Key", credential);
    if (init?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, { ...init, headers });
    if (response.status === 401) {
      authenticationFailed.current = true;
      if (authenticationMode === "operator-token") setOperatorToken("");
      else setTenantKey("");
      setSummary(null);
      setTraces([]);
      setReplayResult(null);
      if (connectionControl.current) connectionControl.current.open = true;
      const message =
        authenticationMode === "operator-token"
          ? "Management access expired or was rejected. Enter a valid signed operator token and reconnect."
          : "Management access was rejected. Enter a valid local tenant key and reconnect.";
      setStatusMessage(message);
      throw new Error(message);
    }
    if (!response.ok) {
      const text = await response.text();
      const safeText = [credential, operatorToken.trim(), tenantKey.trim()].reduce(
        (value, secret) => (secret ? value.replaceAll(secret, "[redacted]") : value),
        text,
      );
      throw new ManagementResponseError(response.status, `${response.status} ${response.statusText}: ${safeText}`);
    }
    return (await response.json()) as T;
  }

  async function permittedMetadata<T>(path: string, deniedValue: T): Promise<T> {
    try {
      return await apiFetch<T>(path);
    } catch (error) {
      if (error instanceof ManagementResponseError && error.status === 403) return deniedValue;
      throw error;
    }
  }

  async function loadDashboardSummary(): Promise<{ payload: SummaryPayload; metadataOnly: boolean }> {
    const apiId = scopedApiId.trim();
    if (!apiId) {
      try {
        return { payload: await apiFetch<SummaryPayload>("/apim/management/summary"), metadataOnly: false };
      } catch (error) {
        if (!(error instanceof ManagementResponseError) || error.status !== 403) throw error;
      }
    }
    const [apis, status, products, versionSets] = await Promise.all([
      apiId
        ? apiFetch<ApiSummary>(`/apim/management/apis/${encodeURIComponent(apiId)}`).then((api) => [api])
        : apiFetch<ApiSummary[]>("/apim/management/apis"),
      apiId
        ? Promise.resolve({ gateway_policy_scope: { scope_type: "gateway", scope_name: "gateway" } })
        : apiFetch<{ gateway_policy_scope: PolicyScope }>("/apim/management/status"),
      apiId ? Promise.resolve([]) : permittedMetadata<ProductSummary[]>("/apim/management/products", []),
      apiId
        ? Promise.resolve([])
        : permittedMetadata<NonNullable<SummaryPayload["api_version_sets"]>>("/apim/management/api-version-sets", []),
    ]);
    return {
      payload: {
        gateway_policy_scope: status.gateway_policy_scope,
        apis,
        products,
        api_version_sets: versionSets,
        routes: [],
        subscriptions: [],
        backends: [],
      },
      metadataOnly: true,
    };
  }

  function discardUnsaved(): boolean {
    if (!hasUnsaved) return true;
    if (!window.confirm("Discard unsaved API, operation, import, policy, revision, or release edits?")) return false;
    setApiDirty(false);
    setOperationDirty(false);
    setPolicyDirty(false);
    setImportDirty(false);
    setRevisionDirty(false);
    setPromoteWithChangelog(false);
    setRevisionChangelogNotes("");
    setReleaseDirty(false);
    setNewRevisionDirty(false);
    return true;
  }

  function setApiForm(api: ApiSummary) {
    setApiDraft({
      id: api.id,
      name: api.name,
      path: api.path,
      upstream_base_url: api.upstream_base_url,
      upstream_path_prefix: api.upstream_path_prefix ?? "",
      translate_required_query_parameters: api.translate_required_query_parameters ?? "template",
    });
    setApiDirty(false);
    setIsNewApi(false);
  }

  function setOperationForm(operation: OperationSummary | null, apiId: string) {
    if (!operation) {
      setOperationDraft({ id: "", name: "", method: "GET", url_template: "/", description: "" });
      setOperationDirty(false);
      return;
    }
    setOperationDraft({
      id: operation.id,
      name: operation.name,
      method: operation.method,
      url_template: operation.url_template,
      description: operation.description ?? "",
    });
    setOperationDirty(false);
    setSelectedApiId(apiId);
  }

  async function loadPolicy(scope: PolicyScope) {
    const requestId = ++scopeRequest.current;
    setSelectedScopeId(scopeId(scope));
    setEffectiveMode(false);
    setEffectiveXml("");
    setPolicyXml("");
    setPolicyDirty(false);
    setPolicyLoading(true);
    setPolicyMessage(`Loading ${scope.scope_type} policy…`);
    try {
      const policy = await apiFetch<{ xml: string }>(
        `/apim/management/policies/${scope.scope_type}/${encodeURIComponent(scope.scope_name)}`,
      );
      if (requestId !== scopeRequest.current) return;
      startTransition(() => {
        setPolicyXml(policy.xml);
        setPolicyDirty(false);
        setPolicyMessage(`Loaded ${scope.scope_type} policy for ${scope.scope_name}.`);
      });
    } catch (error) {
      if (requestId !== scopeRequest.current) return;
      setPolicyMessage(error instanceof Error ? error.message : "Unable to load policy.");
    } finally {
      if (requestId === scopeRequest.current) setPolicyLoading(false);
    }
  }

  async function loadEffectivePolicy(scope: PolicyScope, productId = effectiveProductId) {
    const requestId = ++scopeRequest.current;
    setPolicyLoading(true);
    setEffectiveXml("");
    const query = productId ? `&product_id=${encodeURIComponent(productId)}` : "";
    setPolicyMessage("Loading effective policy…");
    try {
      const policy = await apiFetch<{ xml: string }>(
        `/apim/management/policies/${scope.scope_type}/${encodeURIComponent(scope.scope_name)}?effective=true${query}`,
      );
      if (requestId !== scopeRequest.current || selectedScopeId !== scopeId(scope)) return;
      setEffectiveXml(policy.xml);
      setPolicyMessage(
        `Effective policy for ${scope.scope_type} / ${scope.scope_name}${productId ? ` with ${productId}` : ""}.`,
      );
    } catch (error) {
      if (requestId !== scopeRequest.current || selectedScopeId !== scopeId(scope)) return;
      setPolicyMessage(error instanceof Error ? error.message : "Unable to load effective policy.");
    } finally {
      if (requestId === scopeRequest.current) setPolicyLoading(false);
    }
  }

  async function refreshDashboard(
    preferredScope?: string,
    preferredApiId?: string,
    allowDirty = false,
    preferredOperationId?: string,
  ) {
    const resetAuthoring = hasUnsaved;
    if (resetAuthoring && !allowDirty && !discardUnsaved()) return;
    if (!credential) {
      setStatusMessage(
        authenticationMode === "operator-token"
          ? "Enter a signed operator token to connect."
          : "Enter a local tenant key to connect.",
      );
      return;
    }
    const requestId = ++dashboardRequest.current;
    authenticationFailed.current = false;
    revisionRequest.current += 1;
    releaseRequest.current += 1;
    setRevisionLoading(false);
    setReleaseLoading(false);
    scopeRequest.current += 1;
    setBusy(true);
    setStatusMessage("Refreshing APIs, policies, and traces.");
    try {
      const [{ payload: summaryPayload, metadataOnly }, tracesPayload] = await Promise.all([
        loadDashboardSummary(),
        scopedApiId.trim()
          ? Promise.resolve({ items: [] })
          : permittedMetadata<{ items: TraceItem[] }>("/apim/management/traces", { items: [] }),
      ]);
      if (requestId !== dashboardRequest.current) return;
      const nextApis = summaryPayload.apis ?? [];
      const nextApi = nextApis.find((api) => api.id === (preferredApiId ?? selectedApiId)) ?? nextApis[0] ?? null;
      const nextScope =
        flattenPolicyScopes(summaryPayload, !scopedApiId.trim()).find((scope) => scopeId(scope) === preferredScope) ??
        flattenPolicyScopes(summaryPayload, !scopedApiId.trim()).find((scope) => scopeId(scope) === selectedScopeId) ??
        flattenPolicyScopes(summaryPayload, !scopedApiId.trim())[0];
      startTransition(() => {
        setSummary(summaryPayload);
        if (connectionControl.current) connectionControl.current.open = false;
        setTraces(tracesPayload.items ?? []);
        if (!selectedTraceId && tracesPayload.items?.[0]) setSelectedTraceId(tracesPayload.items[0].trace_id);
        if (nextApi) {
          setSelectedApiId(nextApi.id);
          setApiForm(nextApi);
          const firstOperation =
            nextApi.operations.find((operation) => operation.id === (preferredOperationId ?? selectedOperationId)) ??
            nextApi.operations[0] ??
            null;
          setSelectedOperationId(firstOperation?.id ?? "");
          setOperationForm(firstOperation, nextApi.id);
        } else {
          setSelectedApiId("");
          setSelectedOperationId("");
          setApiDraft({
            id: "",
            name: "",
            path: "",
            upstream_base_url: "",
            upstream_path_prefix: "",
            translate_required_query_parameters: "template",
          });
          setOperationDraft({ id: "", name: "", method: "GET", url_template: "/", description: "" });
        }
      });
      if (nextScope && !policyDirty) await loadPolicy(nextScope);
      if (!authenticationFailed.current)
        setStatusMessage(
          metadataOnly
            ? "Connected with permitted metadata. Subscription credentials and unavailable trace contents are not loaded."
            : "Console is in sync with the simulator.",
        );
    } catch (error) {
      if (requestId === dashboardRequest.current)
        setStatusMessage(error instanceof Error ? error.message : "Unable to refresh the console.");
    } finally {
      if (requestId === dashboardRequest.current) setBusy(false);
    }
  }

  function loadLocalDemo() {
    if (!discardUnsaved()) return;
    setBaseUrl(localDemoDefaults.baseUrl);
    setAuthenticationMode("local-tenant");
    setOperatorToken("");
    setScopedApiId("");
    setTenantKey(localDemoDefaults.tenantKey);
    setSummary(null);
    if (connectionControl.current) connectionControl.current.open = true;
    setTraces([]);
    setSelectedTraceId("");
    setSelectedApiId("");
    setSelectedOperationId("");
    setSelectedScopeId("");
    setPolicyXml("");
    setEffectiveXml("");
    setPolicyMessage("");
    setManualMethod(localDemoDefaults.replayMethod);
    setManualPath(localDemoDefaults.replayPath);
    setManualHeaders(localDemoDefaults.replayHeaders);
    setManualBody(localDemoDefaults.replayBody);
    setReplayResult(null);
    setStatusMessage("Loaded the default local demo values. Press Connect to sync with the simulator.");
  }

  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if ((apiDirty || operationDirty || importDirty) && !discardUnsaved()) return;
    const scope = scopes.find((item) => scopeId(item) === selectedScopeId);
    if (!scope) return setPolicyMessage("No policy scope is selected.");
    setBusy(true);
    setPolicyMessage(`Saving ${scope.scope_type} policy…`);
    try {
      const saved = await apiFetch<{ xml: string }>(
        `/apim/management/policies/${scope.scope_type}/${encodeURIComponent(scope.scope_name)}`,
        { method: "PUT", body: JSON.stringify({ xml: policyXml }) },
      );
      setPolicyXml(saved.xml);
      setPolicyDirty(false);
      setPolicyMessage(`Saved ${scope.scope_type} policy for ${scope.scope_name}.`);
      await refreshDashboard(selectedScopeId, selectedApiId, true);
    } catch (error) {
      setPolicyMessage(error instanceof Error ? error.message : "Policy update failed.");
    } finally {
      setBusy(false);
    }
  }

  async function saveApi(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if ((operationDirty || policyDirty || importDirty) && !discardUnsaved()) return;
    if (!apiDraft.id.trim() || !apiDraft.path.trim()) return setStatusMessage("API ID and path are required.");
    setBusy(true);
    try {
      const existing =
        selectedApi && selectedApi.id === apiDraft.id
          ? await apiFetch<Record<string, unknown>>(`/apim/management/apis/${encodeURIComponent(apiDraft.id)}`)
          : {};
      const policy =
        selectedApi && selectedApi.id === apiDraft.id
          ? await apiFetch<{ xml: string }>(`/apim/management/policies/api/${encodeURIComponent(apiDraft.id)}`)
          : { xml: "" };
      await apiFetch(`/apim/management/apis/${encodeURIComponent(apiDraft.id)}`, {
        method: "PUT",
        body: JSON.stringify({
          ...existing,
          name: apiDraft.name || apiDraft.id,
          path: apiDraft.path,
          upstream_base_url: apiDraft.upstream_base_url,
          upstream_path_prefix: apiDraft.upstream_path_prefix,
          translate_required_query_parameters: apiDraft.translate_required_query_parameters,
          policies_xml: policy.xml,
        }),
      });
      setApiDirty(false);
      setIsNewApi(false);
      setSelectedApiId(apiDraft.id);
      setStatusMessage(`Saved API ${apiDraft.id}.`);
      await refreshDashboard(`api:${apiDraft.id}`, apiDraft.id, true);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to save API.");
    } finally {
      setBusy(false);
    }
  }

  async function createApiFromOpenApi(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!apiDraft.id.trim() || !apiDraft.path.trim()) {
      setStatusMessage("Name (resource ID) and API URL suffix are required.");
      return;
    }
    if (!importValue.trim()) {
      setStatusMessage("Choose a specification file or enter an OpenAPI URL.");
      return;
    }
    if (!credential) {
      setStatusMessage(
        authenticationMode === "operator-token"
          ? "Enter a signed operator token to connect."
          : "Enter a local tenant key to connect.",
      );
      return;
    }
    setBusy(true);
    setStatusMessage(`Creating API ${apiDraft.id}…`);
    let apiCreated = false;
    try {
      await apiFetch(`/apim/management/apis/${encodeURIComponent(apiDraft.id)}`, {
        method: "PUT",
        body: JSON.stringify({
          name: apiDraft.name || apiDraft.id,
          path: apiDraft.path,
          upstream_base_url: apiDraft.upstream_base_url,
          upstream_path_prefix: apiDraft.upstream_path_prefix,
          translate_required_query_parameters: apiDraft.translate_required_query_parameters,
          policies_xml: "",
        }),
      });
      apiCreated = true;
      const response = await apiFetch<{ api: ApiSummary; import: { operation_count: number; diagnostics: string[] } }>(
        `/apim/management/apis/${encodeURIComponent(apiDraft.id)}/import`,
        {
          method: "POST",
          body: JSON.stringify({
            name: apiDraft.name || apiDraft.id,
            path: apiDraft.path,
            content_format: importFormat,
            content_value: importValue.trim(),
            upstream_base_url: apiDraft.upstream_base_url.trim() || undefined,
            translate_required_query_parameters: apiDraft.translate_required_query_parameters,
          }),
        },
      );
      setApiDirty(false);
      setImportDirty(false);
      setIsNewApi(false);
      await refreshDashboard(`api:${apiDraft.id}`, apiDraft.id, true);
      setStatusMessage(
        `Created ${apiDraft.id} and imported ${response.import.operation_count} operations${response.import.diagnostics.length ? ` · ${response.import.diagnostics.join(" ")}` : ""}`,
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : "OpenAPI API creation failed.";
      if (apiCreated) {
        setApiDirty(false);
        setImportDirty(false);
        setIsNewApi(false);
        await refreshDashboard(`api:${apiDraft.id}`, apiDraft.id, true);
        setStatusMessage(
          `API ${apiDraft.id} was created, but the OpenAPI import failed: ${message} Fix the specification and use Import document in Settings to retry.`,
        );
      } else {
        setStatusMessage(message);
      }
    } finally {
      setBusy(false);
    }
  }

  async function deleteApi() {
    if (!selectedApi || !window.confirm(`Delete API ${selectedApi.id} and its operations?`)) return;
    setBusy(true);
    try {
      await apiFetch(`/apim/management/apis/${encodeURIComponent(selectedApi.id)}`, { method: "DELETE" });
      setSelectedApiId("");
      setSelectedOperationId("");
      setApiDirty(false);
      setOperationDirty(false);
      setStatusMessage(`Deleted API ${selectedApi.id}.`);
      await refreshDashboard();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to delete API.");
    } finally {
      setBusy(false);
    }
  }

  async function importOpenApi(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if ((apiDirty || operationDirty || policyDirty) && !discardUnsaved()) return;
    if (!selectedApi && !isNewApi) return setStatusMessage("Select or save an API before importing a specification.");
    const apiId = apiDraft.id;
    if (!apiId) return setStatusMessage("Save the API ID before importing a specification.");
    setBusy(true);
    setStatusMessage(`Importing OpenAPI document into ${apiId}…`);
    try {
      const response = await apiFetch<{ api: ApiSummary; import: { operation_count: number; diagnostics: string[] } }>(
        `/apim/management/apis/${encodeURIComponent(apiId)}/import`,
        {
          method: "POST",
          body: JSON.stringify({
            name: selectedApi?.name ?? apiDraft.name ?? apiId,
            path: selectedApi?.path ?? apiDraft.path,
            content_format: importFormat,
            content_value: importValue,
            upstream_base_url: importUpstream.trim() || undefined,
            translate_required_query_parameters:
              selectedApi?.translate_required_query_parameters ?? apiDraft.translate_required_query_parameters,
          }),
        },
      );
      setApiDirty(false);
      setImportDirty(false);
      setShowImport(false);
      setStatusMessage(
        `Imported ${response.import.operation_count} operations${response.import.diagnostics.length ? ` · ${response.import.diagnostics.join(" ")}` : ""}`,
      );
      await refreshDashboard(`api:${apiId}`, apiId, true);
      setSelectedApiId(apiId);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "OpenAPI import failed.");
    } finally {
      setBusy(false);
    }
  }

  async function loadApiRevisions(apiId: string, preferredRevisionId?: string): Promise<ApiRevisionRecord[]> {
    const requestId = ++revisionRequest.current;
    setRevisionLoading(true);
    try {
      const items = await apiFetch<ApiRevisionRecord[]>(`/apim/management/apis/${encodeURIComponent(apiId)}/revisions`);
      if (requestId !== revisionRequest.current || selectedApiId !== apiId) return [];
      setRevisions(items);
      const numericIds = items.map((revision) => Number(revision.id)).filter(Number.isInteger);
      const nextRevisionId = String(Math.max(1, ...numericIds) + 1);
      setNewRevisionId((current) => current || nextRevisionId);
      const selected =
        items.find((revision) => revision.id === preferredRevisionId) ??
        items.find((revision) => revision.is_current) ??
        items[0];
      if (selected) await loadApiRevision(apiId, selected.id, requestId);
      else {
        setSelectedRevisionId("");
        setRevisionDefinition({});
        setRevisionDescription("");
      }
      return items;
    } catch (error) {
      if (requestId !== revisionRequest.current || selectedApiId !== apiId) return [];
      setStatusMessage(error instanceof Error ? error.message : "Unable to load API revisions.");
      return [];
    } finally {
      if (requestId === revisionRequest.current) setRevisionLoading(false);
    }
  }

  async function loadApiRevision(apiId: string, revisionId: string, requestId = revisionRequest.current) {
    const revision = await apiFetch<ApiRevisionRecord>(
      `/apim/management/apis/${encodeURIComponent(apiId)}/revisions/${encodeURIComponent(revisionId)}`,
    );
    if (requestId !== revisionRequest.current || selectedApiId !== apiId) return;
    setSelectedRevisionId(revision.id);
    setRevisionDescription(revision.description ?? "");
    setRevisionOnline(revision.is_online ?? true);
    const definition = revision.definition ?? {};
    setRevisionDefinition(definition);
    const operationMap = (definition.operations as Record<string, Record<string, unknown>> | undefined) ?? {};
    const firstOperationId = Object.keys(operationMap)[0] ?? "";
    setRevisionOperationId(firstOperationId);
    setReplayResult(null);
    if (selectedApi) {
      setRevisionTestPath(
        apiRevisionPath(
          String(definition.path ?? selectedApi.path),
          revisionId,
          String(operationMap[firstOperationId]?.url_template ?? "/"),
        ),
      );
    }
    setRevisionDirty(false);
    setNewRevisionDirty(false);
    setPromoteWithChangelog(false);
    setRevisionChangelogNotes("");
    setReleaseDirty(false);
  }

  async function createApiRevision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedApi || !newRevisionId.trim()) return;
    if (revisionDirty) {
      setStatusMessage("Save or discard the selected revision edits before creating another revision.");
      return;
    }
    if (revisions.some((revision) => revision.id === newRevisionId.trim())) {
      setStatusMessage(`Revision ${newRevisionId.trim()} already exists. Choose a new identifier.`);
      return;
    }
    setRevisionLoading(true);
    try {
      await apiFetch<ApiRevisionRecord>(
        `/apim/management/apis/${encodeURIComponent(selectedApi.id)}/revisions/${encodeURIComponent(newRevisionId.trim())}`,
        { method: "PUT", body: JSON.stringify({ description: newRevisionDescription || null }) },
      );
      setNewRevisionId("");
      setNewRevisionDescription("");
      setNewRevisionDirty(false);
      setStatusMessage(`Created revision ${newRevisionId.trim()} from the current API.`);
      await loadApiRevisions(selectedApi.id, newRevisionId.trim());
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to create API revision.");
    } finally {
      setRevisionLoading(false);
    }
  }

  async function saveApiRevision(makeCurrent = false) {
    if (!selectedApi || !selectedRevisionId) return;
    setRevisionLoading(true);
    try {
      await apiFetch<ApiRevisionRecord>(
        `/apim/management/apis/${encodeURIComponent(selectedApi.id)}/revisions/${encodeURIComponent(selectedRevisionId)}`,
        {
          method: "PUT",
          body: JSON.stringify({
            description: revisionDescription || null,
            is_online: revisionOnline,
            is_current: makeCurrent || undefined,
            definition: revisionDefinition,
          }),
        },
      );
      setRevisionDirty(false);
      setStatusMessage(
        makeCurrent ? `Revision ${selectedRevisionId} is now current.` : `Saved revision ${selectedRevisionId}.`,
      );
      if (makeCurrent || selectedRevisionIsCurrent) {
        await refreshDashboard(selectedScopeId, selectedApi.id, true, selectedOperationId);
      }
      await loadApiRevisions(selectedApi.id, selectedRevisionId);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to save API revision.");
    } finally {
      setRevisionLoading(false);
    }
  }

  async function promoteRevision() {
    if (!selectedApi || !selectedRevisionId) return;
    if (!promoteWithChangelog) {
      await saveApiRevision(true);
      return;
    }
    setRevisionLoading(true);
    try {
      await apiFetch(
        `/apim/management/apis/${encodeURIComponent(selectedApi.id)}/revisions/${encodeURIComponent(selectedRevisionId)}`,
        {
          method: "PUT",
          body: JSON.stringify({
            description: revisionDescription || null,
            is_online: revisionOnline,
            definition: revisionDefinition,
          }),
        },
      );
      const baseReleaseId = `release-${selectedRevisionId}`;
      let generatedReleaseId = baseReleaseId;
      let suffix = 2;
      while (releases.some((release) => release.id === generatedReleaseId)) {
        generatedReleaseId = `${baseReleaseId}-${suffix}`;
        suffix += 1;
      }
      await apiFetch(
        `/apim/management/apis/${encodeURIComponent(selectedApi.id)}/releases/${encodeURIComponent(generatedReleaseId)}`,
        {
          method: "PUT",
          body: JSON.stringify({ revision: selectedRevisionId, notes: revisionChangelogNotes || null }),
        },
      );
      setRevisionDirty(false);
      setPromoteWithChangelog(false);
      setRevisionChangelogNotes("");
      await refreshDashboard(selectedScopeId, selectedApi.id, true, selectedOperationId);
      await loadApiRevisions(selectedApi.id, selectedRevisionId);
      await loadApiReleases(selectedApi.id);
      setStatusMessage(
        `Promoted revision ${selectedRevisionId} and added release ${generatedReleaseId} to the changelog.`,
      );
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to promote API revision.");
    } finally {
      setRevisionLoading(false);
    }
  }

  function updateRevisionOperation(field: string, value: string) {
    if (!selectedRevisionOperation || !revisionOperationId) return;
    setRevisionDefinition({
      ...revisionDefinition,
      operations: {
        ...revisionOperationMap,
        [revisionOperationId]: { ...selectedRevisionOperation, [field]: value },
      },
    });
    if (field === "url_template" && selectedApi) {
      setRevisionTestPath(
        apiRevisionPath(String(revisionDefinition.path ?? selectedApi.path), selectedRevisionId, value),
      );
    }
    setRevisionDirty(true);
  }

  async function runRevisionTest() {
    if (!selectedApi || !selectedRevisionOperation || !revisionTestPath.trim()) return;
    setBusy(true);
    setStatusMessage(`Sending ${String(selectedRevisionOperation.method ?? "GET")} ${revisionTestPath}.`);
    try {
      const result = await apiFetch<ReplayResult>("/apim/management/replay", {
        method: "POST",
        body: JSON.stringify({
          method: String(selectedRevisionOperation.method ?? "GET"),
          path: revisionTestPath,
          headers: JSON.parse(manualHeaders || "{}"),
          body_text: manualBody || undefined,
        }),
      });
      acceptReplayResult(result);
      setStatusMessage(`Revision test completed: ${result.response.status_code} ${revisionTestPath}.`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Revision test failed.");
    } finally {
      setBusy(false);
    }
  }

  async function loadApiReleases(apiId: string, preferredRevisionId?: string) {
    const requestId = ++releaseRequest.current;
    setReleaseLoading(true);
    try {
      const items = await apiFetch<ApiReleaseRecord[]>(`/apim/management/apis/${encodeURIComponent(apiId)}/releases`);
      if (requestId !== releaseRequest.current || selectedApiId !== apiId) return;
      setReleases(items);
      setReleaseRevisionId((current) =>
        releaseDirty
          ? current
          : current || preferredRevisionId || revisions.find((revision) => revision.is_current)?.id || "",
      );
    } catch (error) {
      if (requestId !== releaseRequest.current || selectedApiId !== apiId) return;
      setStatusMessage(error instanceof Error ? error.message : "Unable to load API releases.");
    } finally {
      if (requestId === releaseRequest.current) setReleaseLoading(false);
    }
  }

  async function createApiRelease(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedApi || !releaseId.trim() || !releaseRevisionId) return;
    if (revisionDirty && selectedRevisionId !== releaseRevisionId) {
      setStatusMessage("Save or discard the open revision edits before publishing a different revision.");
      return;
    }
    setBusy(true);
    try {
      if (revisionDirty && selectedRevisionId === releaseRevisionId) {
        await apiFetch(
          `/apim/management/apis/${encodeURIComponent(selectedApi.id)}/revisions/${encodeURIComponent(selectedRevisionId)}`,
          {
            method: "PUT",
            body: JSON.stringify({
              description: revisionDescription || null,
              is_online: revisionOnline,
              definition: revisionDefinition,
            }),
          },
        );
        setRevisionDirty(false);
      }
      await apiFetch<ApiReleaseRecord>(
        `/apim/management/apis/${encodeURIComponent(selectedApi.id)}/releases/${encodeURIComponent(releaseId.trim())}`,
        {
          method: "PUT",
          body: JSON.stringify({ revision: releaseRevisionId, notes: releaseNotes || null }),
        },
      );
      setReleaseDirty(false);
      setReleaseId("");
      setReleaseNotes("");
      await refreshDashboard(selectedScopeId, selectedApi.id, true, selectedOperationId);
      await Promise.all([loadApiReleases(selectedApi.id), loadApiRevisions(selectedApi.id, releaseRevisionId)]);
      setStatusMessage(`Created release ${releaseId.trim()} from revision ${releaseRevisionId}.`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to create API release.");
    } finally {
      setBusy(false);
    }
  }

  async function saveOperation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if ((apiDirty || policyDirty || importDirty) && !discardUnsaved()) return;
    if (!selectedApi || !operationDraft.id.trim())
      return setStatusMessage("Select an API and provide an operation ID.");
    setBusy(true);
    try {
      const existing = selectedOperation
        ? await apiFetch<Record<string, unknown>>(
            `/apim/management/apis/${encodeURIComponent(selectedApi.id)}/operations/${encodeURIComponent(selectedOperation.id)}`,
          )
        : {};
      const policy = selectedOperation
        ? await apiFetch<{ xml: string }>(
            `/apim/management/policies/operation/${encodeURIComponent(`${selectedApi.id}:${selectedOperation.id}`)}`,
          )
        : { xml: "" };
      await apiFetch(
        `/apim/management/apis/${encodeURIComponent(selectedApi.id)}/operations/${encodeURIComponent(operationDraft.id)}`,
        {
          method: "PUT",
          body: JSON.stringify({
            ...existing,
            name: operationDraft.name || operationDraft.id,
            method: operationDraft.method,
            url_template: operationDraft.url_template,
            description: operationDraft.description || null,
            policies_xml: policy.xml,
          }),
        },
      );
      setOperationDirty(false);
      setSelectedOperationId(operationDraft.id);
      setStatusMessage(`Saved operation ${operationDraft.id}.`);
      await refreshDashboard(
        `operation:${selectedApi.id}:${operationDraft.id}`,
        selectedApi.id,
        true,
        operationDraft.id,
      );
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to save operation.");
    } finally {
      setBusy(false);
    }
  }

  async function deleteOperation() {
    if (!selectedApi || !selectedOperation || !window.confirm(`Delete operation ${selectedOperation.id}?`)) return;
    setBusy(true);
    try {
      await apiFetch(
        `/apim/management/apis/${encodeURIComponent(selectedApi.id)}/operations/${encodeURIComponent(selectedOperation.id)}`,
        { method: "DELETE" },
      );
      setSelectedOperationId("");
      setOperationDirty(false);
      setStatusMessage(`Deleted operation ${selectedOperation.id}.`);
      await refreshDashboard(`api:${selectedApi.id}`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to delete operation.");
    } finally {
      setBusy(false);
    }
  }

  function selectApi(api: ApiSummary, keepTab = false) {
    if (!discardUnsaved()) return;
    revisionRequest.current += 1;
    releaseRequest.current += 1;
    setRevisionLoading(false);
    setReleaseLoading(false);
    setSelectedApiId(api.id);
    setApiForm(api);
    setSelectedOperationId("");
    setOperationForm(null, api.id);
    setRevisions([]);
    setSelectedRevisionId("");
    setRevisionDefinition({});
    setRevisionDirty(false);
    setNewRevisionId("");
    setReleases([]);
    setReleaseRevisionId("");
    setReleaseDirty(false);
    void loadPolicy(api.policy_scope);
    if (!keepTab) setActiveTab("policy");
  }

  function selectOperation(api: ApiSummary, operation: OperationSummary) {
    if (!discardUnsaved()) return;
    revisionRequest.current += 1;
    releaseRequest.current += 1;
    setRevisionLoading(false);
    setReleaseLoading(false);
    setSelectedApiId(api.id);
    setApiForm(api);
    setSelectedOperationId(operation.id);
    setOperationForm(operation, api.id);
    setRevisions([]);
    setSelectedRevisionId("");
    setRevisionDefinition({});
    setRevisionDirty(false);
    setNewRevisionId("");
    setReleases([]);
    setReleaseRevisionId("");
    setReleaseDirty(false);
    void loadPolicy(operation.policy_scope);
    setActiveTab("details");
  }

  function addApi() {
    if (!discardUnsaved()) return;
    revisionRequest.current += 1;
    releaseRequest.current += 1;
    setIsNewApi(true);
    setApiCreationMode("choose");
    setSelectedApiId("");
    setSelectedOperationId("");
    setApiDraft({
      id: "",
      name: "",
      path: "",
      upstream_base_url: "",
      upstream_path_prefix: "",
      translate_required_query_parameters: "template",
    });
    setOperationDraft({ id: "", name: "", method: "GET", url_template: "/", description: "" });
    setApiDirty(false);
    setOperationDirty(false);
    setShowImport(false);
    setImportValue("");
    setImportFormat("openapi+json");
    setImportUpstream("");
    setImportDirty(false);
    setOpenApiUrl("");
    setActiveTab("details");
  }

  function cancelApiCreation() {
    if (!discardUnsaved()) return;
    setIsNewApi(false);
    setApiDirty(false);
    setOperationDirty(false);
    setImportDirty(false);
  }

  function addOperation() {
    if (!selectedApi || !discardUnsaved()) return;
    setSelectedOperationId("");
    setOperationForm(null, selectedApi.id);
    setOperationDraft({
      id: "new-operation",
      name: "New operation",
      method: "GET",
      url_template: "/",
      description: "",
    });
    setOperationDirty(true);
    setActiveTab("details");
  }

  async function rotateKey(subscriptionId: string, key: "primary" | "secondary") {
    setBusy(true);
    try {
      await apiFetch(`/apim/management/subscriptions/${encodeURIComponent(subscriptionId)}/rotate?key=${key}`, {
        method: "POST",
      });
      setStatusMessage(`Rotated ${key} key for ${subscriptionId}.`);
      await refreshDashboard(selectedScopeId);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to rotate key.");
    } finally {
      setBusy(false);
    }
  }

  async function setSubscriptionState(subscriptionId: string, state: "active" | "rejected") {
    setBusy(true);
    try {
      await apiFetch(`/apim/management/subscriptions/${encodeURIComponent(subscriptionId)}`, {
        method: "PATCH",
        body: JSON.stringify({ state }),
      });
      setStatusMessage(`${state === "active" ? "Approved" : "Rejected"} subscription ${subscriptionId}.`);
      await refreshDashboard(selectedScopeId);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to update subscription state.");
    } finally {
      setBusy(false);
    }
  }

  function acceptReplayResult(result: ReplayResult) {
    setReplayResult(result);
    if (result.trace) {
      setSelectedTraceId(result.trace.trace_id);
      setTraces((current) => [
        result.trace as TraceItem,
        ...current.filter((trace) => trace.trace_id !== result.trace?.trace_id),
      ]);
    }
  }

  async function runApiTest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedApi || !selectedOperation) return;
    let path = testPath.replace(/\{([^{}]+)\}/g, (_, name: string) =>
      encodeURIComponent(testPathParams[name] ?? "value"),
    );
    const headers = { ...testHeaders };
    const query = { ...testQuery };
    if (testSubscriptionKey) {
      const headerNames = selectedOperation.subscription_header_names?.length
        ? selectedOperation.subscription_header_names
        : selectedApi.subscription_header_names?.length
          ? selectedApi.subscription_header_names
          : (summary?.subscription_key_names?.header_names ?? ["Ocp-Apim-Subscription-Key"]);
      const queryNames = selectedOperation.subscription_query_param_names?.length
        ? selectedOperation.subscription_query_param_names
        : selectedApi.subscription_query_param_names?.length
          ? selectedApi.subscription_query_param_names
          : (summary?.subscription_key_names?.query_param_names ?? []);
      for (const name of headerNames) headers[name] = testSubscriptionKey;
      for (const name of queryNames) query[name] = testSubscriptionKey;
    }
    const versionSetId = selectedOperation.api_version_set ?? selectedApi.api_version_set;
    const versionSet = summary?.api_version_sets?.find((item) => item.id === versionSetId);
    const version = selectedOperation.api_version ?? selectedApi.api_version;
    if (versionSet && version) {
      if (versionSet.versioning_scheme === "Header" && versionSet.version_header_name)
        headers[versionSet.version_header_name] = version;
      if (versionSet.versioning_scheme === "Query" && versionSet.version_query_name)
        query[versionSet.version_query_name] = version;
      if (versionSet.versioning_scheme === "Segment") {
        const apiPrefix = `/${selectedApi.path.replace(/^\/+|\/+$/g, "")}`;
        if (path === apiPrefix || path.startsWith(`${apiPrefix}/`)) {
          path = `${apiPrefix}/${encodeURIComponent(version)}${path.slice(apiPrefix.length)}`;
        }
      }
    }
    const contentType = selectedOperation.request?.representations?.[0]?.content_type;
    if (testBody && contentType && !Object.keys(headers).some((name) => name.toLowerCase() === "content-type"))
      headers["Content-Type"] = contentType;
    setBusy(true);
    setStatusMessage(`Sending ${selectedOperation.method} ${path}.`);
    try {
      const result = await apiFetch<ReplayResult>("/apim/management/replay", {
        method: "POST",
        body: JSON.stringify({
          method: selectedOperation.method,
          path,
          query,
          headers,
          body_text: testBody || undefined,
        }),
      });
      acceptReplayResult(result);
      setStatusMessage(`Test completed: ${result.response.status_code} ${selectedOperation.method} ${path}.`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "API test failed.");
    } finally {
      setBusy(false);
    }
  }

  async function runManualReplay(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setStatusMessage("Executing manual replay through the simulator.");
    try {
      const result = await apiFetch<ReplayResult>("/apim/management/replay", {
        method: "POST",
        body: JSON.stringify({
          method: manualMethod,
          path: manualPath,
          headers: JSON.parse(manualHeaders || "{}"),
          body_text: manualBody || undefined,
        }),
      });
      acceptReplayResult(result);
      setStatusMessage(`Replay completed: ${result.response.status_code}.`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Replay failed.");
    } finally {
      setBusy(false);
    }
  }

  const selectedScope = scopes.find((scope) => scopeId(scope) === selectedScopeId) ?? null;
  const stagePolicyXml = effectiveMode ? effectiveXml : policyXml;
  const policyStages = [
    { key: "inbound", label: "Inbound processing" },
    { key: "backend", label: "Backend" },
    { key: "outbound", label: "Outbound processing" },
  ];
  const pathParamNames = selectedOperation
    ? (selectedOperation.template_parameters ?? []).filter((item) =>
        selectedOperation.url_template.split("?", 1)[0]?.includes(`{${item.name}}`),
      )
    : [];
  const queryParamNames = selectedOperation
    ? Array.from(
        new Map([
          ...(selectedOperation.request?.query_parameters ?? []).map((item) => [item.name, item] as const),
          ...(selectedOperation.template_parameters ?? [])
            .filter((item) => !pathParamNames.some((pathItem) => pathItem.name === item.name))
            .map((item) => [item.name, item] as const),
        ]).values(),
      )
    : [];

  return (
    <div className="console-shell">
      <div className="ambient ambient-left" />
      <div className="ambient ambient-right" />
      <header className="masthead">
        <div className="brand-lockup">
          <span className="brand-mark">A</span>
          <h1>APIM Simulator</h1>
          <span className="brand-context">Local simulator</span>
        </div>
        <nav className="service-nav" aria-label="Management areas">
          {(["apis", "subscriptions", "traces"] as const).map((area) => (
            <button
              key={area}
              type="button"
              className={activeArea === area ? "active" : ""}
              aria-current={activeArea === area ? "page" : undefined}
              onClick={() => setActiveArea(area)}
            >
              {area === "apis" ? "APIs" : area === "subscriptions" ? "Subscriptions" : "Request traces"}
            </button>
          ))}
        </nav>
      </header>

      <details className="connection-control" ref={connectionControl}>
        <summary>
          <span className={`connection-indicator${summary ? " is-connected" : ""}`} aria-hidden="true" />
          <strong>{summary ? "Connected" : "Connection"}</strong>
          <span className="connection-endpoint">{baseUrl}</span>
          <span className="connection-manage">{summary ? "Manage" : "Configure"}</span>
        </summary>
        <form
          className="connection-panel"
          onSubmit={(event) => {
            event.preventDefault();
            void refreshDashboard();
          }}
        >
          <div className="connection-url">
            <label>
              <span className="field-label">Gateway base URL</span>
              <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} disabled={busy} />
            </label>
            <label>
              <span className="field-label">API ID for scoped access (optional)</span>
              <input value={scopedApiId} onChange={(event) => setScopedApiId(event.target.value)} disabled={busy} />
            </label>
          </div>
          <div className="connection-key">
            <label>
              <span className="field-label">Management authentication</span>
              <select
                value={authenticationMode}
                disabled={busy}
                onChange={(event) => {
                  setAuthenticationMode(event.target.value as AuthenticationMode);
                  setSummary(null);
                  setTraces([]);
                  setReplayResult(null);
                  setStatusMessage("Enter your management credential and connect.");
                }}
              >
                <option value="operator-token">Signed operator token</option>
                <option value="local-tenant">Local tenant key</option>
              </select>
            </label>
            <label>
              <span className="field-label">
                {authenticationMode === "operator-token" ? "Operator JWT" : "Tenant key"}
              </span>
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={authenticationMode === "operator-token" ? operatorToken : tenantKey}
                onChange={(event) =>
                  authenticationMode === "operator-token"
                    ? setOperatorToken(event.target.value)
                    : setTenantKey(event.target.value)
                }
                disabled={busy}
              />
            </label>
          </div>
          <div className="connection-actions">
            <button type="button" className="secondary-button" onClick={loadLocalDemo} disabled={busy}>
              Load Local Demo
            </button>
            <button type="submit" disabled={busy}>
              {busy ? "Working…" : "Connect"}
            </button>
          </div>
          <p className="connection-hint">
            The demo preset targets the management-enabled stack on <code>localhost:8000</code>. Credentials stay in
            memory; only the gateway URL is saved.
          </p>
        </form>
      </details>

      <section className="status-bar">
        <span>{statusMessage}</span>
        {summary ? (
          <button type="button" onClick={() => void refreshDashboard(selectedScopeId)} disabled={busy}>
            Refresh
          </button>
        ) : null}
      </section>

      <main className={`workspace-grid app-page-${activeArea}`} hidden={!summary}>
        <aside className="panel explorer-panel" hidden={activeArea !== "apis"}>
          <div className="panel-head">
            <div>
              <p className="eyebrow">Catalog</p>
              <h2>API Explorer</h2>
            </div>
            <button type="button" className="small-button" onClick={addApi} disabled={busy}>
              Add API
            </button>
          </div>
          <label className="search-field">
            <span className="field-label">Search APIs and operations</span>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Name, path, method…"
            />
          </label>
          <div className="explorer-tree api-tree">
            {filteredApis.map((api) => (
              <section key={api.id} className="tree-api">
                <button
                  type="button"
                  disabled={busy}
                  className={selectedApiId === api.id ? "tree-item selected" : "tree-item"}
                  onClick={() => selectApi(api)}
                >
                  <span>
                    <strong>{api.name}</strong>
                    <small>/{api.path}</small>
                  </span>
                  <span className="tree-count">{api.operations.length}</span>
                </button>
              </section>
            ))}
            {filteredApis.length === 0 ? <p className="empty">No APIs match this search.</p> : null}
          </div>
          <div className="explorer-foot">
            <span>{summary?.routes.length ?? 0} routes</span>
            <span>{summary?.products.length ?? 0} products</span>
            <span>{summary?.backends.length ?? 0} backends</span>
          </div>
        </aside>

        <aside className="panel operation-browser" hidden={activeArea !== "apis"}>
          <h2>Operations</h2>
          {selectedApi ? (
            <button
              type="button"
              className={!selectedOperationId ? "tree-item selected" : "tree-item"}
              disabled={busy || revisionLoading || releaseLoading}
              onClick={() => selectApi(selectedApi, true)}
            >
              All operations
            </button>
          ) : null}
          {selectedApi?.operations
            .filter(
              (operation) =>
                !search.trim() ||
                `${selectedApi.id} ${selectedApi.name} ${operation.id} ${operation.name} ${operation.method} ${operation.url_template}`
                  .toLowerCase()
                  .includes(search.trim().toLowerCase()),
            )
            .map((operation) => (
              <button
                key={operation.id}
                type="button"
                disabled={busy}
                className={
                  selectedOperationId === operation.id
                    ? "tree-item tree-operation selected"
                    : "tree-item tree-operation"
                }
                onClick={() => selectOperation(selectedApi, operation)}
              >
                <span className="method-pill">{operation.method}</span>
                <span className="tree-operation-name">
                  <strong>{operation.name}</strong>
                  <small>{operation.url_template}</small>
                </span>
              </button>
            ))}
          {!selectedApi ? <p className="empty">Select an API to view its operations.</p> : null}
        </aside>

        <section className="panel workspace-panel" hidden={activeArea !== "apis"}>
          <div className="resource-heading">
            <div>
              <p className="eyebrow">
                {selectedOperation ? `${selectedOperation.method} operation` : selectedApi ? "API" : "Authoring"}
              </p>
              <h2>{selectedOperation?.name ?? selectedApi?.name ?? (isNewApi ? "Create API" : "Select an API")}</h2>
              <p>
                {selectedOperation?.url_template ??
                  (selectedApi
                    ? `/${selectedApi.path} · ${selectedApi.upstream_base_url || "No upstream URL"}`
                    : isNewApi
                      ? "Choose a source and configure the API."
                      : "Select an API or add one to begin.")}
              </p>
            </div>
            {selectedApi ? (
              <button
                type="button"
                className="danger-button"
                onClick={() => void deleteApi()}
                disabled={busy || hasUnsaved}
              >
                Delete API
              </button>
            ) : null}
          </div>
          <nav className="workflow-tabs" aria-label="API workflow">
            {(["policy", "details", "test", "revisions", "changelog"] as const).map((tab) => (
              <button
                key={tab}
                type="button"
                className={activeTab === tab ? "active" : ""}
                disabled={
                  (["revisions", "changelog"] as const).includes(tab as "revisions" | "changelog") && !selectedApi
                }
                onClick={() => {
                  if (!discardUnsaved()) return;
                  setActiveTab(tab);
                  if (!selectedApi) return;
                  if (tab === "revisions") {
                    void Promise.all([loadApiRevisions(selectedApi.id), loadApiReleases(selectedApi.id)]);
                  }
                  if (tab === "changelog") {
                    void loadApiRevisions(selectedApi.id).then((items) => {
                      const currentRevision = items.find((revision) => revision.is_current)?.id ?? items[0]?.id ?? "";
                      setReleaseRevisionId(currentRevision);
                      return loadApiReleases(selectedApi.id, currentRevision);
                    });
                  }
                }}
              >
                {tab === "details"
                  ? "Settings"
                  : tab === "policy"
                    ? "Design"
                    : tab === "test"
                      ? "Test"
                      : tab === "revisions"
                        ? "Revisions"
                        : "Changelog"}
              </button>
            ))}
          </nav>

          {activeTab === "details" ? (
            isNewApi && apiCreationMode === "choose" ? (
              <section className="api-create-chooser">
                <div className="form-title">
                  <div>
                    <h3>Add API</h3>
                    <p>Choose how to define the API in your local simulator.</p>
                  </div>
                  <button type="button" className="secondary-button" onClick={cancelApiCreation}>
                    Cancel
                  </button>
                </div>
                <div className="api-create-options">
                  <button type="button" className="api-create-option" onClick={() => setApiCreationMode("http")}>
                    <strong>HTTP</strong>
                    <span>Set an API suffix and web service URL, then add operations.</span>
                  </button>
                  <button type="button" className="api-create-option" onClick={() => setApiCreationMode("openapi")}>
                    <strong>OpenAPI</strong>
                    <span>Import supported OpenAPI or Swagger content from a file or URL.</span>
                  </button>
                </div>
              </section>
            ) : isNewApi && apiCreationMode === "openapi" ? (
              <form className="api-create-form" onSubmit={(event) => void createApiFromOpenApi(event)}>
                <div className="form-title">
                  <div>
                    <h3>Create from OpenAPI</h3>
                    <p>The API is saved first, then the specification creates its operations.</p>
                  </div>
                  <div className="button-row">
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => setApiCreationMode("choose")}
                      disabled={busy}
                    >
                      Back
                    </button>
                    <button type="button" className="secondary-button" onClick={cancelApiCreation} disabled={busy}>
                      Cancel
                    </button>
                  </div>
                </div>
                <fieldset className="authoring-fields" disabled={busy}>
                  <div className="form-grid">
                    <label>
                      <span className="field-label">Display name</span>
                      <input
                        value={apiDraft.name}
                        onChange={(event) => {
                          setApiDraft({ ...apiDraft, name: event.target.value });
                          setApiDirty(true);
                        }}
                      />
                    </label>
                    <label>
                      <span className="field-label">Name (resource ID)</span>
                      <input
                        value={apiDraft.id}
                        onChange={(event) => {
                          setApiDraft({ ...apiDraft, id: event.target.value });
                          setApiDirty(true);
                        }}
                      />
                    </label>
                    <label>
                      <span className="field-label">API URL suffix</span>
                      <input
                        value={apiDraft.path}
                        onChange={(event) => {
                          setApiDraft({ ...apiDraft, path: event.target.value });
                          setApiDirty(true);
                        }}
                      />
                    </label>
                    <label>
                      <span className="field-label">Web service URL</span>
                      <input
                        value={apiDraft.upstream_base_url}
                        onChange={(event) => {
                          setApiDraft({ ...apiDraft, upstream_base_url: event.target.value });
                          setApiDirty(true);
                        }}
                        placeholder="https://backend.example"
                      />
                    </label>
                    <label className="wide-field">
                      <span className="field-label">Specification file</span>
                      <input
                        type="file"
                        accept=".json,.yaml,.yml,application/json,text/yaml,application/yaml"
                        onChange={(event) => {
                          const file = event.currentTarget.files?.[0];
                          if (!file) return;
                          setOpenApiUrl("");
                          setImportValue("");
                          setImportFormat(file.name.toLowerCase().endsWith(".json") ? "openapi+json" : "openapi");
                          void file
                            .text()
                            .then((content) => {
                              setImportValue(content);
                              setImportDirty(true);
                            })
                            .catch((error: unknown) => {
                              setStatusMessage(
                                error instanceof Error ? error.message : "Unable to read specification file.",
                              );
                            });
                        }}
                      />
                    </label>
                    <label className="wide-field">
                      <span className="field-label">Or specification URL</span>
                      <input
                        type="url"
                        value={openApiUrl}
                        onChange={(event) => {
                          const url = event.target.value;
                          setOpenApiUrl(url);
                          setImportValue(url);
                          setImportFormat("openapi-link");
                          setImportDirty(true);
                        }}
                        placeholder="https://example.com/openapi.yaml"
                      />
                    </label>
                  </div>
                  <label className="checkbox-field">
                    <input
                      type="checkbox"
                      checked={apiDraft.translate_required_query_parameters === "template"}
                      onChange={(event) => {
                        setApiDraft({
                          ...apiDraft,
                          translate_required_query_parameters: event.target.checked ? "template" : "query",
                        });
                        setApiDirty(true);
                      }}
                    />
                    <span>Include required query parameters in operation templates</span>
                  </label>
                </fieldset>
                <div className="form-actions">
                  <button
                    type="submit"
                    disabled={
                      busy || !apiDraft.id.trim() || !apiDraft.path.trim() || !(openApiUrl.trim() || importValue.trim())
                    }
                  >
                    {busy ? "Creating API…" : "Create API"}
                  </button>
                  <span>Supported formats follow the simulator’s OpenAPI import endpoint.</span>
                </div>
              </form>
            ) : (
              <div className="details-layout">
                <section className="compact-form api-form">
                  <form onSubmit={(event) => void saveApi(event)}>
                    <div className="form-title">
                      <div>
                        <h3>API settings</h3>
                        <p>Local API metadata and routing defaults.</p>
                      </div>
                      <div className="button-row">
                        {isNewApi ? (
                          <>
                            <button
                              type="button"
                              className="secondary-button"
                              onClick={() => setApiCreationMode("choose")}
                              disabled={busy}
                            >
                              Change API type
                            </button>
                            <button
                              type="button"
                              className="secondary-button"
                              onClick={cancelApiCreation}
                              disabled={busy}
                            >
                              Cancel
                            </button>
                          </>
                        ) : null}
                        <button type="submit" disabled={busy || !apiDirty}>
                          {isNewApi ? "Create API" : "Save API"}
                        </button>
                      </div>
                    </div>
                    <fieldset className="authoring-fields" disabled={busy}>
                      <div className="form-grid">
                        <label>
                          <span className="field-label">Name (resource ID)</span>
                          <input
                            value={apiDraft.id}
                            onChange={(event) => {
                              setApiDraft({ ...apiDraft, id: event.target.value });
                              setApiDirty(true);
                            }}
                            disabled={!isNewApi}
                          />
                        </label>
                        <label>
                          <span className="field-label">Display name</span>
                          <input
                            value={apiDraft.name}
                            onChange={(event) => {
                              setApiDraft({ ...apiDraft, name: event.target.value });
                              setApiDirty(true);
                            }}
                          />
                        </label>
                        <label>
                          <span className="field-label">API URL suffix</span>
                          <input
                            value={apiDraft.path}
                            onChange={(event) => {
                              setApiDraft({ ...apiDraft, path: event.target.value });
                              setApiDirty(true);
                            }}
                          />
                        </label>
                        <label>
                          <span className="field-label">Web service URL</span>
                          <input
                            value={apiDraft.upstream_base_url}
                            onChange={(event) => {
                              setApiDraft({ ...apiDraft, upstream_base_url: event.target.value });
                              setApiDirty(true);
                            }}
                            placeholder="https://backend.example"
                          />
                        </label>
                        <label>
                          <span className="field-label">Upstream path prefix</span>
                          <input
                            value={apiDraft.upstream_path_prefix}
                            onChange={(event) => {
                              setApiDraft({ ...apiDraft, upstream_path_prefix: event.target.value });
                              setApiDirty(true);
                            }}
                          />
                        </label>
                        <label className="checkbox-field">
                          <input
                            type="checkbox"
                            checked={apiDraft.translate_required_query_parameters === "template"}
                            onChange={(event) => {
                              setApiDraft({
                                ...apiDraft,
                                translate_required_query_parameters: event.target.checked ? "template" : "query",
                              });
                              setApiDirty(true);
                            }}
                          />
                          <span>Include required query parameters in operation templates</span>
                        </label>
                      </div>
                    </fieldset>
                  </form>
                  <div className="form-title import-title">
                    <div>
                      <h3>OpenAPI import</h3>
                      <p>Import OpenAPI 2 JSON or OpenAPI 3.0.x into this API.</p>
                    </div>
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => {
                        if (showImport && !discardUnsaved()) return;
                        setShowImport((open) => !open);
                        setImportDirty(false);
                      }}
                      disabled={!selectedApi}
                    >
                      {" "}
                      {showImport ? "Close import" : "Import document"}
                    </button>
                  </div>
                  {showImport ? (
                    <form className="import-form" onSubmit={(event) => void importOpenApi(event)}>
                      <fieldset className="authoring-fields" disabled={busy}>
                        <div className="form-grid">
                          <label>
                            <span className="field-label">Document format</span>
                            <select
                              value={importFormat}
                              onChange={(event) => {
                                setImportFormat(event.target.value);
                                setImportDirty(true);
                              }}
                            >
                              <option value="openapi+json">OpenAPI JSON</option>
                              <option value="openapi">OpenAPI YAML or JSON</option>
                              <option value="swagger-json">Swagger 2 JSON</option>
                              <option value="openapi-link">OpenAPI link</option>
                              <option value="openapi+json-link">OpenAPI JSON link</option>
                              <option value="swagger-link-json">Swagger JSON link</option>
                            </select>
                          </label>
                          <label>
                            <span className="field-label">Upstream override</span>
                            <input
                              value={importUpstream}
                              onChange={(event) => {
                                setImportUpstream(event.target.value);
                                setImportDirty(true);
                              }}
                              placeholder="Optional local backend URL"
                            />
                          </label>
                        </div>
                        <label>
                          <span className="field-label">Specification or URL</span>
                          <textarea
                            value={importValue}
                            onChange={(event) => {
                              setImportValue(event.target.value);
                              setImportDirty(true);
                            }}
                            rows={8}
                            placeholder="Paste the document, or enter a link for a link format."
                          />
                        </label>
                      </fieldset>
                      <div className="form-actions">
                        <button type="submit" disabled={busy || !importValue.trim()}>
                          {busy ? "Importing…" : "Import and update API"}
                        </button>
                        <span>Import refreshes operations and schemas. Matching operation policies are retained.</span>
                      </div>
                    </form>
                  ) : null}
                </section>

                {selectedApi ? (
                  <form className="compact-form operation-form" onSubmit={(event) => void saveOperation(event)}>
                    <div className="form-title">
                      <div>
                        <h3>Operation</h3>
                        <p>
                          {selectedOperation ? `Editing ${selectedOperation.id}` : "Create or edit a route operation."}
                        </p>
                      </div>
                      <div className="button-row">
                        <button type="button" className="secondary-button" onClick={addOperation} disabled={busy}>
                          Add operation
                        </button>
                        {selectedOperation ? (
                          <button
                            type="button"
                            className="danger-button"
                            onClick={() => void deleteOperation()}
                            disabled={busy || operationDirty}
                          >
                            Delete
                          </button>
                        ) : null}
                        <button type="submit" disabled={busy || !operationDirty}>
                          {selectedOperation ? "Save" : "Create"}
                        </button>
                      </div>
                    </div>
                    <fieldset className="authoring-fields" disabled={busy}>
                      <div className="form-grid">
                        <label>
                          <span className="field-label">Operation ID</span>
                          <input
                            value={operationDraft.id}
                            onChange={(event) => {
                              setOperationDraft({ ...operationDraft, id: event.target.value });
                              setOperationDirty(true);
                            }}
                            disabled={Boolean(selectedOperation)}
                          />
                        </label>
                        <label>
                          <span className="field-label">Display name</span>
                          <input
                            value={operationDraft.name}
                            onChange={(event) => {
                              setOperationDraft({ ...operationDraft, name: event.target.value });
                              setOperationDirty(true);
                            }}
                          />
                        </label>
                        <label>
                          <span className="field-label">Method</span>
                          <select
                            value={operationDraft.method}
                            onChange={(event) => {
                              setOperationDraft({ ...operationDraft, method: event.target.value });
                              setOperationDirty(true);
                            }}
                          >
                            {["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"].map((method) => (
                              <option key={method}>{method}</option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span className="field-label">URL template</span>
                          <input
                            value={operationDraft.url_template}
                            onChange={(event) => {
                              setOperationDraft({ ...operationDraft, url_template: event.target.value });
                              setOperationDirty(true);
                            }}
                          />
                        </label>
                        <label className="wide-field">
                          <span className="field-label">Description</span>
                          <input
                            value={operationDraft.description}
                            onChange={(event) => {
                              setOperationDraft({ ...operationDraft, description: event.target.value });
                              setOperationDirty(true);
                            }}
                          />
                        </label>
                      </div>
                    </fieldset>
                    {selectedOperation ? (
                      <div className="metadata-strip">
                        <span>{selectedOperation.template_parameters?.length ?? 0} template parameters</span>
                        <span>{selectedOperation.request?.representations?.length ?? 0} request representations</span>
                        <span>{selectedOperation.responses?.length ?? 0} responses</span>
                        <span>
                          {selectedOperation.request?.headers?.length ?? 0} headers ·{" "}
                          {selectedOperation.request?.query_parameters?.length ?? 0} query
                        </span>
                      </div>
                    ) : null}
                  </form>
                ) : (
                  <div className="empty-card">
                    <strong>Save this API first</strong>
                    <span>Then add operations directly or import an OpenAPI document.</span>
                  </div>
                )}
              </div>
            )
          ) : null}

          {activeTab === "revisions" ? (
            <div className="revision-workspace">
              <form className="revision-create-form" onSubmit={(event) => void createApiRevision(event)}>
                <div>
                  <h3>Create revision</h3>
                  <p>A new revision starts as a snapshot of the current API. Edit or promote it after creation.</p>
                </div>
                <label>
                  <span className="field-label">Revision identifier</span>
                  <input
                    value={newRevisionId}
                    onChange={(event) => {
                      setNewRevisionId(event.target.value);
                      setNewRevisionDirty(true);
                    }}
                    disabled={revisionLoading || busy || releaseLoading}
                  />
                </label>
                <label>
                  <span className="field-label">Description</span>
                  <input
                    value={newRevisionDescription}
                    onChange={(event) => {
                      setNewRevisionDescription(event.target.value);
                      setNewRevisionDirty(true);
                    }}
                    disabled={revisionLoading || busy || releaseLoading}
                  />
                </label>
                <button
                  type="submit"
                  disabled={
                    revisionLoading ||
                    busy ||
                    releaseLoading ||
                    !newRevisionId.trim() ||
                    revisions.some((revision) => revision.id === newRevisionId.trim())
                  }
                >
                  {revisionLoading ? "Working…" : "Create revision"}
                </button>
              </form>
              <div className="revision-layout">
                <aside className="revision-list">
                  <div className="form-title">
                    <h3>API revisions</h3>
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => {
                        if (!selectedApi || !discardUnsaved()) return;
                        void loadApiRevisions(selectedApi.id);
                      }}
                      disabled={!selectedApi || revisionLoading || releaseLoading || busy}
                    >
                      Refresh
                    </button>
                  </div>
                  {revisions.map((revision) => (
                    <button
                      type="button"
                      key={revision.id}
                      className={
                        selectedRevisionId === revision.id ? "revision-list-item selected" : "revision-list-item"
                      }
                      onClick={async () => {
                        if (!discardUnsaved() || !selectedApi) return;
                        const requestId = ++revisionRequest.current;
                        setRevisionLoading(true);
                        try {
                          await loadApiRevision(selectedApi.id, revision.id, requestId);
                        } catch (error) {
                          if (requestId === revisionRequest.current) {
                            setStatusMessage(error instanceof Error ? error.message : "Unable to load revision.");
                          }
                        } finally {
                          if (requestId === revisionRequest.current) setRevisionLoading(false);
                        }
                      }}
                      disabled={revisionLoading || releaseLoading || busy}
                    >
                      <strong>{revision.id}</strong>
                      <span>{revision.description || "No description"}</span>
                      <small>
                        {revision.is_current ? "Current" : "Revision"} ·{" "}
                        {revision.is_online === false
                          ? "Offline"
                          : revision.is_online === true
                            ? "Online"
                            : "Online status unset"}
                      </small>
                    </button>
                  ))}
                  {revisions.length === 0 ? <p className="empty">No revisions are available for this API.</p> : null}
                </aside>
                {selectedRevisionId ? (
                  <form
                    className="revision-editor"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void saveApiRevision();
                    }}
                  >
                    <div className="form-title">
                      <div>
                        <h3>Revision {selectedRevisionId}</h3>
                        <p>Edit this revision snapshot independently from the current API.</p>
                      </div>
                      <div className="button-row">
                        <button type="submit" disabled={revisionsBusy || !revisionDirty}>
                          Save revision
                        </button>
                        {!selectedRevisionIsCurrent ? (
                          <button type="button" onClick={() => void promoteRevision()} disabled={revisionsBusy}>
                            {promoteWithChangelog ? "Promote and publish" : "Make current"}
                          </button>
                        ) : null}
                      </div>
                    </div>
                    {!selectedRevisionIsCurrent ? (
                      <div className="revision-release-options">
                        <label className="checkbox-field">
                          <input
                            type="checkbox"
                            checked={promoteWithChangelog}
                            disabled={revisionsBusy}
                            onChange={(event) => {
                              setPromoteWithChangelog(event.target.checked);
                              setRevisionDirty(true);
                            }}
                          />
                          <span>Create changelog entry when promoting</span>
                        </label>
                        {promoteWithChangelog ? (
                          <label>
                            <span className="field-label">Change notes</span>
                            <textarea
                              rows={3}
                              value={revisionChangelogNotes}
                              disabled={revisionsBusy}
                              onChange={(event) => {
                                setRevisionChangelogNotes(event.target.value);
                                setRevisionDirty(true);
                              }}
                            />
                          </label>
                        ) : null}
                      </div>
                    ) : null}
                    <div className="form-grid">
                      <label>
                        <span className="field-label">Description</span>
                        <input
                          value={revisionDescription}
                          disabled={revisionsBusy}
                          onChange={(event) => {
                            setRevisionDescription(event.target.value);
                            setRevisionDirty(true);
                          }}
                        />
                      </label>
                      <label className="checkbox-field">
                        <input
                          type="checkbox"
                          checked={revisionOnline}
                          disabled={revisionsBusy}
                          onChange={(event) => {
                            setRevisionOnline(event.target.checked);
                            setRevisionDirty(true);
                          }}
                        />
                        <span>Online</span>
                      </label>
                      <label>
                        <span className="field-label">Display name</span>
                        <input
                          value={String(revisionDefinition.name ?? "")}
                          disabled={!selectedRevisionIsCurrent || revisionsBusy}
                          onChange={(event) => {
                            setRevisionDefinition({ ...revisionDefinition, name: event.target.value });
                            setRevisionDirty(true);
                          }}
                        />
                      </label>
                      <label>
                        <span className="field-label">API URL suffix</span>
                        <input
                          value={String(revisionDefinition.path ?? "")}
                          disabled={!selectedRevisionIsCurrent || revisionsBusy}
                          onChange={(event) => {
                            setRevisionDefinition({ ...revisionDefinition, path: event.target.value });
                            setRevisionDirty(true);
                          }}
                        />
                      </label>
                      <label>
                        <span className="field-label">Web service URL</span>
                        <input
                          value={String(revisionDefinition.upstream_base_url ?? "")}
                          disabled={revisionsBusy}
                          onChange={(event) => {
                            setRevisionDefinition({ ...revisionDefinition, upstream_base_url: event.target.value });
                            setRevisionDirty(true);
                          }}
                        />
                      </label>
                      <label>
                        <span className="field-label">Upstream path prefix</span>
                        <input
                          value={String(revisionDefinition.upstream_path_prefix ?? "")}
                          disabled={revisionsBusy}
                          onChange={(event) => {
                            setRevisionDefinition({ ...revisionDefinition, upstream_path_prefix: event.target.value });
                            setRevisionDirty(true);
                          }}
                        />
                      </label>
                      <label className="wide-field">
                        <span className="field-label">API policy XML</span>
                        <textarea
                          rows={14}
                          value={String(revisionDefinition.policies_xml ?? "")}
                          disabled={revisionsBusy}
                          onChange={(event) => {
                            setRevisionDefinition({ ...revisionDefinition, policies_xml: event.target.value });
                            setRevisionDirty(true);
                          }}
                        />
                      </label>
                    </div>
                    <section className="revision-operation-editor">
                      <div className="form-title">
                        <div>
                          <h3>Operations in this revision</h3>
                          <p>Operation changes are saved with this revision snapshot.</p>
                        </div>
                        <label>
                          <span className="field-label">Operation</span>
                          <select
                            value={revisionOperationId}
                            disabled={revisionsBusy}
                            onChange={(event) => {
                              const operationId = event.target.value;
                              setRevisionOperationId(operationId);
                              setReplayResult(null);
                              const operation = revisionOperationMap[operationId];
                              if (selectedApi && operation) {
                                setRevisionTestPath(
                                  apiRevisionPath(
                                    String(revisionDefinition.path ?? selectedApi.path),
                                    selectedRevisionId,
                                    String(operation.url_template ?? "/"),
                                  ),
                                );
                              }
                            }}
                          >
                            {Object.keys(revisionOperationMap).map((operationId) => (
                              <option key={operationId} value={operationId}>
                                {operationId}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                      {selectedRevisionOperation ? (
                        <div className="form-grid">
                          <label>
                            <span className="field-label">Display name</span>
                            <input
                              value={String(selectedRevisionOperation.name ?? "")}
                              disabled={revisionsBusy}
                              onChange={(event) => updateRevisionOperation("name", event.target.value)}
                            />
                          </label>
                          <label>
                            <span className="field-label">Method</span>
                            <select
                              value={String(selectedRevisionOperation.method ?? "GET")}
                              disabled={revisionsBusy}
                              onChange={(event) => updateRevisionOperation("method", event.target.value)}
                            >
                              {["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"].map((method) => (
                                <option key={method}>{method}</option>
                              ))}
                            </select>
                          </label>
                          <label>
                            <span className="field-label">URL template</span>
                            <input
                              value={String(selectedRevisionOperation.url_template ?? "/")}
                              disabled={revisionsBusy}
                              onChange={(event) => updateRevisionOperation("url_template", event.target.value)}
                            />
                          </label>
                          <label>
                            <span className="field-label">Description</span>
                            <input
                              value={String(selectedRevisionOperation.description ?? "")}
                              disabled={revisionsBusy}
                              onChange={(event) => updateRevisionOperation("description", event.target.value)}
                            />
                          </label>
                        </div>
                      ) : (
                        <p className="empty">This revision has no operations.</p>
                      )}
                    </section>
                    {selectedRevisionOperation ? (
                      <section className="revision-test-panel">
                        <div className="form-title">
                          <div>
                            <h3>Test revision {selectedRevisionId}</h3>
                            <p>The request URL includes the selected API revision.</p>
                          </div>
                          <button type="button" onClick={() => void runRevisionTest()} disabled={revisionsBusy}>
                            {busy ? "Sending…" : "Send request"}
                          </button>
                        </div>
                        <label>
                          <span className="field-label">Request URL path</span>
                          <input
                            value={revisionTestPath}
                            disabled={revisionsBusy}
                            onChange={(event) => setRevisionTestPath(event.target.value)}
                          />
                        </label>
                        <div className="form-grid">
                          <label>
                            <span className="field-label">Headers (JSON)</span>
                            <textarea
                              rows={4}
                              value={manualHeaders}
                              disabled={revisionsBusy}
                              onChange={(event) => setManualHeaders(event.target.value)}
                            />
                          </label>
                          <label>
                            <span className="field-label">Request body</span>
                            <textarea
                              rows={4}
                              value={manualBody}
                              disabled={revisionsBusy}
                              onChange={(event) => setManualBody(event.target.value)}
                            />
                          </label>
                        </div>
                        <ReplayOutput result={replayResult} onTrace={(traceId) => setSelectedTraceId(traceId)} />
                      </section>
                    ) : null}
                    <p className="revision-contents">
                      Snapshot includes{" "}
                      {Object.keys((revisionDefinition.operations as Record<string, unknown> | undefined) ?? {}).length}{" "}
                      operations and{" "}
                      {Object.keys((revisionDefinition.schemas as Record<string, unknown> | undefined) ?? {}).length}{" "}
                      schemas. Operation definitions are editable above; other operation and schema metadata remains in
                      Settings.
                    </p>
                  </form>
                ) : (
                  <div className="empty-card">
                    <strong>Select a revision</strong>
                    <span>Choose a revision to inspect its API settings and policy snapshot.</span>
                  </div>
                )}
              </div>
            </div>
          ) : null}

          {activeTab === "changelog" ? (
            <div className="changelog-workspace">
              <form className="release-create-form" onSubmit={(event) => void createApiRelease(event)}>
                <div>
                  <h3>Create release</h3>
                  <p>Publishing a release promotes its selected revision to current and records these notes.</p>
                </div>
                <div className="form-grid">
                  <label>
                    <span className="field-label">Release identifier</span>
                    <input
                      value={releaseId}
                      onChange={(event) => {
                        setReleaseId(event.target.value);
                        setReleaseDirty(true);
                      }}
                      disabled={revisionsBusy}
                    />
                  </label>
                  <label>
                    <span className="field-label">Revision</span>
                    <select
                      value={releaseRevisionId}
                      onChange={(event) => {
                        setReleaseRevisionId(event.target.value);
                        setReleaseDirty(true);
                      }}
                      disabled={revisionsBusy || revisions.length === 0}
                    >
                      <option value="">Choose a revision</option>
                      {revisions.map((revision) => (
                        <option key={revision.id} value={revision.id}>
                          {revision.id}
                          {revision.is_current ? " · current" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="wide-field">
                    <span className="field-label">Change notes</span>
                    <textarea
                      rows={4}
                      value={releaseNotes}
                      onChange={(event) => {
                        setReleaseNotes(event.target.value);
                        setReleaseDirty(true);
                      }}
                      disabled={revisionsBusy}
                    />
                  </label>
                </div>
                <button type="submit" disabled={revisionsBusy || !releaseId.trim() || !releaseRevisionId}>
                  {busy ? "Publishing…" : "Create release"}
                </button>
              </form>
              <section className="release-list">
                <div className="form-title">
                  <div>
                    <h3>Change log</h3>
                    <p>Published releases for {selectedApi?.name ?? "this API"}.</p>
                  </div>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => selectedApi && void loadApiReleases(selectedApi.id)}
                    disabled={revisionsBusy || !selectedApi}
                  >
                    Refresh
                  </button>
                </div>
                {releases.map((release) => (
                  <article className="release-entry" key={release.id}>
                    <div>
                      <strong>{release.name || release.id}</strong>
                      <span>Revision {release.revision}</span>
                    </div>
                    <p>{release.notes || "No change notes provided."}</p>
                  </article>
                ))}
                {releases.length === 0 ? <p className="empty">No releases have been published for this API.</p> : null}
              </section>
            </div>
          ) : null}

          {activeTab === "policy" ? (
            <div className="policy-workspace">
              <form className="policy-form" onSubmit={(event) => void savePolicy(event)}>
                <fieldset className="policy-design-grid" aria-label="Policy pipeline stages">
                  <button
                    className="policy-stage-card policy-stage-frontend"
                    type="button"
                    onClick={() => {
                      if (discardUnsaved()) setActiveTab("details");
                    }}
                    disabled={!selectedApi || busy}
                  >
                    <span>Frontend</span>
                    <strong>
                      {selectedOperation
                        ? `${selectedOperation.method} ${selectedOperation.url_template}`
                        : selectedApi
                          ? `/${selectedApi.path}`
                          : "API operation"}
                    </strong>
                    <small>{selectedOperation ? "Operation definition" : "API settings"}</small>
                  </button>
                  {policyStages.map(({ key, label }) => {
                    const names = policyStageNames(stagePolicyXml, key);
                    return (
                      <a className="policy-stage-card" href="#policy-xml-editor" key={key}>
                        <span>{label}</span>
                        <strong>
                          {names.length ? names.map(policyDisplayName).join(" · ") : "No policies defined"}
                        </strong>
                        <small>{effectiveMode ? "Effective policy stage" : "Authored policy stage"}</small>
                      </a>
                    );
                  })}
                </fieldset>
                <div className="policy-toolbar">
                  <label className="scope-select">
                    <span className="field-label">Policy scope</span>
                    <select
                      value={selectedScopeId}
                      disabled={busy || policyLoading}
                      onChange={(event) => {
                        const scope = scopes.find((item) => scopeId(item) === event.target.value);
                        if (scope && discardUnsaved()) {
                          setEffectiveMode(false);
                          void loadPolicy(scope);
                        }
                      }}
                    >
                      {scopes.map((scope) => (
                        <option key={scopeId(scope)} value={scopeId(scope)}>
                          {scope.scope_type} / {scope.scope_name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="product-select">
                    <span className="field-label">Product context</span>
                    <select
                      value={effectiveProductId}
                      disabled={busy || policyLoading}
                      onChange={(event) => {
                        const productId = event.target.value;
                        setEffectiveProductId(productId);
                        if (effectiveMode && selectedScope) void loadEffectivePolicy(selectedScope, productId);
                      }}
                    >
                      <option value="">No product context</option>
                      {summary?.products.map((product) => (
                        <option key={product.id} value={product.id}>
                          {product.name} ({product.id})
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    className={effectiveMode ? "secondary-button active-mode" : "secondary-button"}
                    onClick={() => {
                      if (!selectedScope) return;
                      setEffectiveMode((current) => !current);
                      if (!effectiveMode) void loadEffectivePolicy(selectedScope);
                    }}
                    disabled={busy || policyLoading || !selectedScope}
                  >
                    {" "}
                    {effectiveMode ? "Edit scope policy" : "Show effective policy"}
                  </button>
                </div>
                <label className="policy-editor" id="policy-xml-editor">
                  <span className="field-label">{effectiveMode ? "Effective XML · read only" : "Editable XML"}</span>
                  <textarea
                    value={effectiveMode ? effectiveXml : policyXml}
                    onChange={(event) => {
                      setPolicyXml(event.target.value);
                      setPolicyDirty(true);
                    }}
                    readOnly={effectiveMode || busy || policyLoading}
                    rows={12}
                  />
                </label>
                <div className="form-actions">
                  <button
                    type="submit"
                    disabled={busy || policyLoading || effectiveMode || !policyDirty || !selectedScopeId}
                  >
                    Save policy
                  </button>
                  <span>{policyMessage}</span>
                </div>
              </form>
              <div className="scope-note">
                <strong>Supported scopes</strong>
                <span>
                  Global, product, API, operation, and legacy route policies. Effective XML shows inherited policy order
                  for the selected scope and product context.
                </span>
              </div>
            </div>
          ) : null}

          {activeTab === "test" ? (
            <div className="test-workspace">
              {selectedApi && selectedOperation ? (
                <form className="test-form" onSubmit={(event) => void runApiTest(event)}>
                  <div className="form-title">
                    <div>
                      <h3>Try this operation</h3>
                      <p>Values come from the operation template and imported request metadata.</p>
                    </div>
                    <button type="submit" disabled={busy}>
                      {busy ? "Sending…" : "Send request"}
                    </button>
                  </div>
                  <div className="test-target">
                    <span className="method-pill">{selectedOperation.method}</span>
                    <code>{testPath || apiPathFor(selectedApi, selectedOperation)}</code>
                  </div>
                  {pathParamNames.map((parameter) => (
                    <label key={`path-${parameter.name}`}>
                      <span className="field-label">
                        Path · {parameter.name}
                        {parameter.required ? " *" : ""}
                      </span>
                      <input
                        value={testPathParams[parameter.name] ?? ""}
                        onChange={(event) =>
                          setTestPathParams({ ...testPathParams, [parameter.name]: event.target.value })
                        }
                      />
                    </label>
                  ))}
                  {queryParamNames.map((parameter) => (
                    <label key={`query-${parameter.name}`}>
                      <span className="field-label">
                        Query · {parameter.name}
                        {parameter.required ? " *" : ""}
                      </span>
                      <input
                        value={testQuery[parameter.name] ?? ""}
                        onChange={(event) => setTestQuery({ ...testQuery, [parameter.name]: event.target.value })}
                        placeholder={parameter.values?.join(" · ")}
                      />
                    </label>
                  ))}
                  {(selectedOperation.request?.headers ?? []).map((parameter) => (
                    <label key={`header-${parameter.name}`}>
                      <span className="field-label">
                        Header · {parameter.name}
                        {parameter.required ? " *" : ""}
                      </span>
                      <input
                        value={testHeaders[parameter.name] ?? ""}
                        onChange={(event) => setTestHeaders({ ...testHeaders, [parameter.name]: event.target.value })}
                      />
                    </label>
                  ))}
                  <label>
                    <span className="field-label">Subscription key · optional</span>
                    <div className="subscription-key-row">
                      <input
                        value={testSubscriptionKey}
                        onChange={(event) => setTestSubscriptionKey(event.target.value)}
                        placeholder="Paste a subscription key"
                      />
                      <select
                        value=""
                        onChange={(event) => {
                          const subscription = summary?.subscriptions.find((item) => item.id === event.target.value);
                          if (subscription) setTestSubscriptionKey(subscription.keys.primary);
                        }}
                      >
                        <option value="">Choose a saved key</option>
                        {summary?.subscriptions.map((subscription) => (
                          <option key={subscription.id} value={subscription.id}>
                            {subscription.name} · {subscription.state}
                          </option>
                        ))}
                      </select>
                    </div>
                  </label>
                  {selectedOperation.request?.representations?.length ? (
                    <label>
                      <span className="field-label">
                        Request body · {selectedOperation.request.representations[0].content_type}
                      </span>
                      <textarea value={testBody} onChange={(event) => setTestBody(event.target.value)} rows={9} />
                    </label>
                  ) : null}
                  <details className="request-metadata">
                    <summary>Imported request metadata</summary>
                    <pre>
                      {prettyJson({
                        parameters: selectedOperation.template_parameters ?? [],
                        request: selectedOperation.request ?? null,
                        responses: selectedOperation.responses ?? [],
                      })}
                    </pre>
                  </details>
                </form>
              ) : (
                <div className="empty-card">
                  <strong>Select an operation</strong>
                  <span>Choose one in the API explorer to build a request from its metadata.</span>
                </div>
              )}

              <div className="manual-replay">
                <details>
                  <summary>Manual replay</summary>
                  <form className="replay-form" onSubmit={(event) => void runManualReplay(event)}>
                    <div className="form-grid">
                      <label>
                        <span className="field-label">Method</span>
                        <select value={manualMethod} onChange={(event) => setManualMethod(event.target.value)}>
                          {["GET", "POST", "PUT", "PATCH", "DELETE"].map((method) => (
                            <option key={method}>{method}</option>
                          ))}
                        </select>
                      </label>
                      <label>
                        <span className="field-label">Path</span>
                        <input value={manualPath} onChange={(event) => setManualPath(event.target.value)} />
                      </label>
                    </div>
                    <label>
                      <span className="field-label">Headers (JSON)</span>
                      <textarea
                        value={manualHeaders}
                        onChange={(event) => setManualHeaders(event.target.value)}
                        rows={4}
                      />
                    </label>
                    <label>
                      <span className="field-label">Body</span>
                      <textarea value={manualBody} onChange={(event) => setManualBody(event.target.value)} rows={5} />
                    </label>
                    <button type="submit" disabled={busy}>
                      Run manual replay
                    </button>
                  </form>
                </details>
              </div>
              <ReplayOutput result={replayResult} onTrace={(traceId) => setSelectedTraceId(traceId)} />
            </div>
          ) : null}
        </section>

        <section className="panel trace-panel" hidden={activeArea !== "traces"}>
          <div className="panel-head">
            <div>
              <p className="eyebrow">Observability</p>
              <h2>Trace Ledger</h2>
            </div>
            <span className="count-chip">{traces.length} recent</span>
          </div>
          <div className="trace-layout">
            <ul className="trace-list">
              {traces.map((trace) => (
                <li key={trace.trace_id}>
                  <button
                    type="button"
                    className={selectedTrace?.trace_id === trace.trace_id ? "trace-chip active" : "trace-chip"}
                    onClick={() => setSelectedTraceId(trace.trace_id)}
                  >
                    <strong>{trace.route}</strong>
                    <span>{trace.status}</span>
                    <small>
                      {trace.created_at} · {trace.forwarded_proto || "direct"}
                    </small>
                  </button>
                </li>
              ))}
              {traces.length === 0 ? <li className="empty">No traces captured yet.</li> : null}
            </ul>
            <div className="trace-detail">
              {selectedTrace ? (
                <TraceDetails trace={selectedTrace} />
              ) : (
                "Select a trace to inspect request, routing, and policy details."
              )}
            </div>
          </div>
        </section>

        <section className="panel subscription-panel" hidden={activeArea !== "subscriptions"}>
          <div className="panel-head">
            <div>
              <p className="eyebrow">Access</p>
              <h2>Subscriptions</h2>
            </div>
            <p>Inspect saved keys, approve or reject pending requests, and rotate keys.</p>
          </div>
          <div className="subscription-grid">
            {summary?.subscriptions.map((subscription) => (
              <article key={subscription.id} className="subscription-card">
                <header>
                  <div>
                    <h3>{subscription.name}</h3>
                    <p>{subscription.id}</p>
                  </div>
                  <span className={subscription.state === "submitted" ? "state-pill state-pill-pending" : "state-pill"}>
                    {subscription.state}
                  </span>
                </header>
                <dl>
                  <div>
                    <dt>Primary</dt>
                    <dd>{subscription.keys.primary}</dd>
                  </div>
                  <div>
                    <dt>Secondary</dt>
                    <dd>{subscription.keys.secondary}</dd>
                  </div>
                  <div>
                    <dt>Products</dt>
                    <dd>{subscription.products.join(", ") || "None"}</dd>
                  </div>
                </dl>
                <div className="subscription-actions">
                  {subscription.state === "submitted" ? (
                    <>
                      <button
                        type="button"
                        onClick={() => void setSubscriptionState(subscription.id, "active")}
                        disabled={busy}
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={() => void setSubscriptionState(subscription.id, "rejected")}
                        disabled={busy}
                      >
                        Reject
                      </button>
                    </>
                  ) : null}
                  <button type="button" onClick={() => void rotateKey(subscription.id, "primary")} disabled={busy}>
                    Rotate primary
                  </button>
                  <button type="button" onClick={() => void rotateKey(subscription.id, "secondary")} disabled={busy}>
                    Rotate secondary
                  </button>
                </div>
              </article>
            )) ?? <p className="empty">No subscriptions loaded.</p>}
          </div>
        </section>
      </main>
      {!summary ? (
        <section className="welcome-panel" aria-labelledby="welcome-title">
          <span className="welcome-kicker">Local APIM control plane</span>
          <h2 id="welcome-title">Connect to your simulator</h2>
          <p>
            Browse APIs, edit policies, replay requests, and inspect traces from one workspace. Connect to a
            management-enabled local gateway to get started.
          </p>
          <div className="welcome-actions">
            <button
              type="button"
              onClick={() => {
                if (connectionControl.current) connectionControl.current.open = true;
              }}
            >
              Configure connection
            </button>
            <button type="button" className="secondary-button" onClick={loadLocalDemo} disabled={busy}>
              Use local demo settings
            </button>
          </div>
          <small>
            For the default demo, start the UI stack with <code>make up-ui</code>.
          </small>
        </section>
      ) : null}
    </div>
  );
}

function ReplayOutput({ result, onTrace }: { result: ReplayResult | null; onTrace: (traceId: string) => void }) {
  if (!result)
    return (
      <section className="replay-output">
        <h3>Response and trace</h3>
        <pre>Send an API test or manual replay to inspect the response.</pre>
      </section>
    );
  return (
    <section className="replay-output">
      <div className="response-heading">
        <h3>Response</h3>
        <span className={`status-code ${result.response.status_code < 400 ? "success" : "error"}`}>
          {result.response.status_code}
        </span>
        {result.trace_id ? (
          <button type="button" className="text-button" onClick={() => onTrace(result.trace_id as string)}>
            Open trace {result.trace_id}
          </button>
        ) : null}
      </div>
      <div className="response-split">
        <div>
          <h4>Body</h4>
          <pre>
            {result.response.body_text ??
              (result.response.body_base64 ? `Base64 body: ${result.response.body_base64}` : "(empty)")}
          </pre>
        </div>
        <div>
          <h4>Headers</h4>
          <pre>{prettyJson(result.response.headers)}</pre>
        </div>
      </div>
    </section>
  );
}

function traceText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  return typeof value === "object" ? prettyJson(value) : String(value);
}

function TraceDetails({ trace }: { trace: TraceItem }) {
  const steps = trace.policy_steps ?? [];
  return (
    <div className="trace-overview">
      <div className="trace-summary-heading">
        <div>
          <span className="trace-kicker">{trace.route}</span>
          <strong>{trace.status ? `HTTP ${trace.status}` : "Request trace"}</strong>
        </div>
        <span className="trace-duration">
          {trace.elapsed_ms != null ? `${trace.elapsed_ms} ms` : "Duration unavailable"}
        </span>
      </div>
      <dl className="trace-facts">
        <div>
          <dt>Upstream</dt>
          <dd>{traceText(trace.upstream_url)}</dd>
        </div>
        <div>
          <dt>Correlation ID</dt>
          <dd>{traceText(trace.correlation_id)}</dd>
        </div>
        <div>
          <dt>Incoming host</dt>
          <dd>{traceText(trace.incoming_host)}</dd>
        </div>
        <div>
          <dt>Forwarded host</dt>
          <dd>{traceText(trace.forwarded_host)}</dd>
        </div>
      </dl>
      <section className="policy-steps">
        <h3>
          Policy steps <span>{steps.length}</span>
        </h3>
        {steps.length ? (
          <ol>
            {steps.map((step) => {
              const { step: name, ...details } = step;
              const label = typeof name === "string" ? name : "Policy step";
              return (
                <li key={JSON.stringify(step)}>
                  <strong>{label}</strong>
                  {Object.keys(details).length ? <pre>{prettyJson(details)}</pre> : null}
                </li>
              );
            })}
          </ol>
        ) : (
          <p>No policy steps were recorded for this request.</p>
        )}
      </section>
      <details className="raw-trace">
        <summary>Raw trace JSON</summary>
        <pre>{prettyJson(trace)}</pre>
      </details>
    </div>
  );
}

export default App;
