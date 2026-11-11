import { type FormEvent, useEffect, useRef, useState } from "react";
import type { ManagementRequest } from "./management";

type VersionSet = {
  id: string;
  versioning_scheme: "Header" | "Query" | "Segment";
  version_header_name?: string | null;
  version_query_name?: string | null;
};
type Props = {
  request: ManagementRequest;
  api: { id: string; name: string; api_version_set?: string | null; api_version?: string | null };
  versionSets: VersionSet[];
  disabled: boolean;
  onCreated: (apiId: string) => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
};
type Scheme = "Path" | "Header" | "Query";

export function ApiVersionsPanel({
  request,
  api,
  versionSets,
  disabled,
  onCreated,
  onDirtyChange,
  onBusyChange,
}: Props) {
  const [versionId, setVersionId] = useState("");
  const [label, setLabel] = useState("");
  const [setChoice, setSetChoice] = useState("");
  const [newSetId, setNewSetId] = useState("");
  const [scheme, setScheme] = useState<Scheme>("Path");
  const [selector, setSelector] = useState("");
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(false);
  const [message, setMessage] = useState("");
  const inFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const selectedSetId = api.api_version_set ?? setChoice;
  const selectedSet = versionSets.find((item) => item.id === selectedSetId);
  const creatingSet = !selectedSetId;
  const missingSet = !creatingSet && !selectedSet;
  const locked = disabled || busy || created;
  function markDirty() {
    onDirtyChange(true);
    setMessage("");
  }
  async function createVersion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked || inFlight.current || missingSet) return;
    const versionSetId = creatingSet ? newSetId.trim() : selectedSetId;
    if (!versionId.trim() || !label.trim() || !versionSetId || (creatingSet && scheme !== "Path" && !selector.trim())) {
      setMessage("Complete all required fields.");
      return;
    }
    if (creatingSet && versionSets.some((item) => item.id === versionSetId)) {
      setMessage("That version set already exists. Select it from the list to reuse it.");
      return;
    }
    const body = {
      version_id: versionId.trim(),
      api_version: label.trim(),
      version_set_id: versionSetId,
      ...(creatingSet ? { versioning_scheme: scheme } : {}),
      ...(creatingSet && scheme === "Header" ? { version_header_name: selector.trim() } : {}),
      ...(creatingSet && scheme === "Query" ? { version_query_name: selector.trim() } : {}),
    };
    const newApiId = versionId.trim();
    inFlight.current = true;
    setBusy(true);
    onBusyChange(true);
    setMessage("");
    try {
      await request(`/apim/management/apis/${encodeURIComponent(api.id)}/versions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!mounted.current) return;
      setCreated(true);
      setVersionId("");
      setLabel("");
      setNewSetId("");
      setSelector("");
      onDirtyChange(false);
      setMessage(`Version ${newApiId} was created.`);
      try {
        await onCreated(newApiId);
      } catch (error) {
        if (mounted.current)
          setMessage(
            `Version ${newApiId} was created, but the console could not refresh: ${error instanceof Error ? error.message : String(error)}. Refresh before creating another version.`,
          );
      }
    } catch (error) {
      if (mounted.current)
        setMessage(`Could not create version: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      inFlight.current = false;
      onBusyChange(false);
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <form className="compact-form" onSubmit={(event) => void createVersion(event)}>
      <div className="form-title">
        <div>
          <h3>Create version</h3>
          <p>
            Clone {api.name} with its operations, policies, and product associations. The new version can be edited
            independently.
          </p>
          {!api.api_version_set ? (
            <p>The source API remains available as Original when its first version is created.</p>
          ) : null}
        </div>
      </div>
      <div className="form-grid">
        <label>
          <span className="field-label">Version API ID</span>
          <input
            value={versionId}
            onChange={(event) => {
              setVersionId(event.target.value);
              markDirty();
            }}
            disabled={locked}
            required
          />
        </label>
        <label>
          <span className="field-label">Version label</span>
          <input
            value={label}
            onChange={(event) => {
              setLabel(event.target.value);
              markDirty();
            }}
            disabled={locked}
            placeholder="v2"
            required
          />
        </label>
        <label>
          <span className="field-label">Version set</span>
          <select
            value={selectedSetId}
            onChange={(event) => {
              setSetChoice(event.target.value);
              markDirty();
            }}
            disabled={locked || Boolean(api.api_version_set)}
          >
            {!api.api_version_set ? <option value="">Create a version set</option> : null}
            {missingSet ? <option value={selectedSetId}>{selectedSetId} (unavailable)</option> : null}
            {versionSets.map((item) => (
              <option key={item.id} value={item.id}>
                {item.id}
              </option>
            ))}
          </select>
        </label>
        {creatingSet ? (
          <>
            <label>
              <span className="field-label">Version set ID</span>
              <input
                value={newSetId}
                onChange={(event) => {
                  setNewSetId(event.target.value);
                  markDirty();
                }}
                disabled={locked}
                required
              />
            </label>
            <label>
              <span className="field-label">Versioning scheme</span>
              <select
                value={scheme}
                onChange={(event) => {
                  const value = event.target.value;
                  if (value === "Path" || value === "Header" || value === "Query") setScheme(value);
                  markDirty();
                }}
                disabled={locked}
              >
                <option value="Path">Path</option>
                <option value="Header">Header</option>
                <option value="Query">Query</option>
              </select>
            </label>
            {scheme !== "Path" ? (
              <label>
                <span className="field-label">
                  {scheme === "Header" ? "Version header name" : "Version query parameter name"}
                </span>
                <input
                  value={selector}
                  onChange={(event) => {
                    setSelector(event.target.value);
                    markDirty();
                  }}
                  disabled={locked}
                  required
                />
              </label>
            ) : null}
          </>
        ) : selectedSet ? (
          <p>
            Scheme: {selectedSet.versioning_scheme === "Segment" ? "Path" : selectedSet.versioning_scheme}
            {selectedSet.version_header_name ? ` (${selectedSet.version_header_name})` : ""}
            {selectedSet.version_query_name ? ` (${selectedSet.version_query_name})` : ""}
          </p>
        ) : (
          <p>Version set details are unavailable. Refresh before creating a version.</p>
        )}
      </div>
      <div className="form-actions">
        <button type="submit" disabled={locked || missingSet}>
          {busy ? "Creating…" : "Create version"}
        </button>
        {message ? <span role="status">{message}</span> : null}
      </div>
    </form>
  );
}
