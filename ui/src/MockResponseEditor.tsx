import { useEffect, useRef, useState } from "react";
import type { ManagementRequest } from "./management";

type Example = { name: string; value?: unknown; external_value?: string | null; [key: string]: unknown };
type Representation = { content_type: string; examples?: Example[]; [key: string]: unknown };
type Response = { status_code: number | "default"; representations?: Representation[]; [key: string]: unknown };
type Operation = { responses?: Response[]; [key: string]: unknown };
type Choice = { status: number; contentType: string; name: string; value: unknown };
type Props = {
  request: ManagementRequest;
  apiId: string;
  operationId: string;
  disabled: boolean;
  onChanged: () => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
};

function choices(operation: Operation): Choice[] {
  return (operation.responses ?? []).flatMap((response) =>
    typeof response.status_code !== "number"
      ? []
      : (response.representations ?? []).flatMap((representation) =>
          (representation.examples ?? [])
            .filter((example) => example.value != null)
            .slice(0, 1)
            .map((example) => ({
              status: Number(response.status_code),
              contentType: representation.content_type,
              name: example.name,
              value: example.value,
            })),
        ),
  );
}
function formatBody(item: Choice): string {
  return typeof item.value === "string" && !item.contentType.toLowerCase().includes("json")
    ? item.value
    : JSON.stringify(item.value ?? null, null, 2);
}
function policyState(xml: string) {
  const document = new DOMParser().parseFromString(
    xml || "<policies><inbound/><backend/><outbound/><on-error/></policies>",
    "application/xml",
  );
  const inbound = Array.from(document.documentElement.children).find((element) => element.tagName === "inbound");
  if (document.querySelector("parsererror") || document.documentElement.tagName !== "policies" || !inbound)
    throw new Error("The operation policy cannot be safely edited. Check its XML in Design.");
  const mocks = Array.from(document.getElementsByTagName("mock-response"));
  const direct = mocks.filter((mock) => mock.parentElement === inbound);
  return {
    document,
    inbound,
    mock: direct.length === 1 ? direct[0] : null,
    error:
      mocks.length !== direct.length || direct.length > 1
        ? "Mocking is configured conditionally or in multiple places. Edit the policy in Design."
        : null,
  };
}
export default function MockResponseEditor({
  request,
  apiId,
  operationId,
  disabled,
  onChanged,
  onDirtyChange,
  onBusyChange,
}: Props) {
  const [items, setItems] = useState<Choice[]>([]);
  const [selection, setSelection] = useState("new");
  const [status, setStatus] = useState("200");
  const [contentType, setContentType] = useState("application/json");
  const [body, setBody] = useState("{}");
  const [active, setActive] = useState(false);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [message, setMessage] = useState("Loading response examples…");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const path = `/apim/management/apis/${encodeURIComponent(apiId)}/operations/${encodeURIComponent(operationId)}`;
  const policyPath = `/apim/management/policies/operation/${encodeURIComponent(`${apiId}:${operationId}`)}`;
  const requestRef = useRef(request);
  requestRef.current = request;
  useEffect(() => {
    let current = true;
    Promise.all([requestRef.current<Operation>(path), requestRef.current<{ xml: string }>(policyPath)])
      .then(([operation, policy]) => {
        if (!current) return;
        const state = policyState(policy.xml);
        const available = choices(operation);
        const mockStatus = state.mock?.getAttribute("status-code");
        const mockType = state.mock?.getAttribute("content-type");
        const matching = available.findIndex(
          (item) => String(item.status) === mockStatus && item.contentType.toLowerCase() === mockType?.toLowerCase(),
        );
        const index = matching >= 0 ? matching : available.length ? 0 : -1;
        setItems(available);
        if (index >= 0) {
          const item = available[index];
          setSelection(String(index));
          setStatus(String(item.status));
          setContentType(item.contentType);
          setBody(formatBody(item));
        } else if (state.mock) {
          if (mockStatus) setStatus(mockStatus);
          if (mockType) setContentType(mockType);
        }
        setLoaded(true);
        setActive(Boolean(state.mock));
        setPolicyError(state.error);
        setMessage("");
      })
      .catch((error: unknown) => {
        if (current) setMessage(error instanceof Error ? error.message : "Unable to load operation.");
      });
    return () => {
      current = false;
    };
  }, [path, policyPath]);
  function choose(value: string) {
    if (dirty && !window.confirm("Discard unsaved response example edits?")) return;
    setDirty(false);
    setSelection(value);
    const item = items[Number(value)];
    if (value === "new" || !item) {
      setStatus("200");
      setContentType("application/json");
      setBody("{}");
    } else {
      setStatus(String(item.status));
      setContentType(item.contentType);
      setBody(formatBody(item));
    }
    onDirtyChange(false);
  }
  async function save(action: "response" | "enable" | "disable") {
    let value: unknown = body;
    const code = Number(status);
    if (action !== "disable") {
      if (!Number.isInteger(code) || code < 100 || code > 599 || !contentType.trim()) {
        setMessage("Enter a status from 100 to 599 and a content type.");
        return;
      }
      if (action === "response" && contentType.toLowerCase().includes("json")) {
        try {
          value = JSON.parse(body);
        } catch {
          setMessage("Enter a valid JSON response example.");
          return;
        }
        if (value === null) {
          setMessage("The mock policy skips null examples. Enter a non-null JSON value.");
          return;
        }
      }
    }
    if (action !== "response" && dirty) {
      setMessage("Save the response example before changing mocking.");
      return;
    }
    setBusy(true);
    onBusyChange(true);
    let saved = false;
    try {
      const selected = selection === "new" ? null : items[Number(selection)];
      if (
        action === "response" &&
        selected &&
        (selected.status !== code || selected.contentType.toLowerCase() !== contentType.trim().toLowerCase())
      ) {
        throw new Error("To change status or content type, create a new response example instead.");
      }
      const operation = await request<Operation>(
        `${path}/${action === "response" ? "response-example" : "mock-response"}`,
        {
          method: "PUT",
          body: JSON.stringify(
            action === "response"
              ? { status_code: code, content_type: contentType.trim(), example_name: selected?.name ?? null, value }
              : { enabled: action === "enable", status_code: code, content_type: contentType.trim() },
          ),
        },
      );
      saved = true;
      const updatedItems = choices(operation);
      setItems(updatedItems);
      if (action === "response") {
        const selectedIndex = updatedItems.findIndex(
          (item) => item.status === code && item.contentType.toLowerCase() === contentType.trim().toLowerCase(),
        );
        setSelection(selectedIndex >= 0 ? String(selectedIndex) : "new");
      }
      if (action !== "response") setActive(action === "enable");
      setDirty(false);
      onDirtyChange(false);
      setMessage(
        action === "response"
          ? "Response example saved."
          : action === "enable"
            ? "Mocking enabled."
            : "Mocking disabled.",
      );
      await onChanged();
    } catch (error: unknown) {
      setMessage(
        saved
          ? "Changes saved, but the console could not refresh. Reload to see the latest state."
          : error instanceof Error
            ? error.message
            : "Unable to save operation.",
      );
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  }
  return (
    <section className="mock-response-editor">
      <h3>Response example and mocking</h3>
      <p>{active ? "Mocking is enabled on this operation." : "Mocking is not enabled on this operation."}</p>
      <p>
        Mocking uses the first non-null example for the selected status and content type. Other examples are preserved.
      </p>
      <fieldset disabled={disabled || busy || !loaded}>
        <label>
          Example{" "}
          <select value={selection} onChange={(event) => choose(event.target.value)}>
            <option value="new">New example</option>
            {items.map((item, index) => (
              <option key={`${item.status}:${item.contentType}:${item.name}`} value={index}>
                {item.status} · {item.contentType} · {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status code{" "}
          <input
            type="number"
            min="100"
            max="599"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setDirty(true);
              onDirtyChange(true);
            }}
          />
        </label>
        <label>
          Content type{" "}
          <input
            value={contentType}
            onChange={(event) => {
              setContentType(event.target.value);
              setDirty(true);
              onDirtyChange(true);
            }}
          />
        </label>
        <label>
          Example body{" "}
          <textarea
            value={body}
            onChange={(event) => {
              setBody(event.target.value);
              setDirty(true);
              onDirtyChange(true);
            }}
          />
        </label>
        <button type="button" onClick={() => void save("response")}>
          Save response example
        </button>
        <button type="button" disabled={Boolean(policyError)} onClick={() => void save("enable")}>
          Enable mocking
        </button>
        <button type="button" disabled={!active || Boolean(policyError)} onClick={() => void save("disable")}>
          Disable mocking
        </button>
      </fieldset>
      {policyError && <p role="alert">{policyError}</p>}
      <p role="status">{message}</p>
    </section>
  );
}
