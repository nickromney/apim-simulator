import { type FormEvent, useState } from "react";
import type { ManagementRequest } from "./management";
import "./products.css";

type Product = {
  id: string;
  name: string;
  description?: string | null;
  state?: string;
  require_subscription: boolean;
  approval_required?: boolean;
};
type Api = { id: string; name: string; products: string[] };
type Props = {
  request: ManagementRequest;
  products: Product[];
  apis: Api[];
  disabled: boolean;
  onChanged: () => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
};
type Details = Product & { terms?: string | null; subscriptions_limit?: number | null };
type Draft = {
  id: string;
  name: string;
  description: string;
  state: string;
  require_subscription: boolean;
  approval_required: boolean;
  terms: string;
  limit: string;
};
const empty: Draft = {
  id: "",
  name: "",
  description: "",
  state: "not_published",
  require_subscription: true,
  approval_required: false,
  terms: "",
  limit: "",
};

export default function ProductsPanel({
  request,
  products,
  apis,
  disabled,
  onChanged,
  onDirtyChange,
  onBusyChange,
}: Props) {
  const [draft, setDraft] = useState<Draft>(empty);
  const [baseline, setBaseline] = useState<Draft>(empty);
  const [isNew, setIsNew] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [apiId, setApiId] = useState("");
  const change = (next: Draft) => {
    setDraft(next);
    setDirty(true);
    onDirtyChange(true);
  };
  const clean = () => {
    setDirty(false);
    onDirtyChange(false);
  };
  async function choose(id: string) {
    if (dirty && !window.confirm("Discard unsaved product changes?")) return;
    if (!id) {
      setDraft(empty);
      setBaseline(empty);
      setIsNew(true);
      clean();
      setMessage("");
      return;
    }
    setBusy(true);
    onBusyChange(true);
    try {
      const p = await request<Details>(`/apim/management/products/${encodeURIComponent(id)}`);
      const loaded = {
        id: p.id,
        name: p.name,
        description: p.description ?? "",
        state: p.state ?? "not_published",
        require_subscription: p.require_subscription,
        approval_required: p.approval_required ?? false,
        terms: p.terms ?? "",
        limit: p.subscriptions_limit?.toString() ?? "",
      };
      setDraft(loaded);
      setBaseline(loaded);
      setIsNew(false);
      clean();
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to load product.");
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const id = draft.id.trim();
    if (!id || !draft.name.trim()) {
      setMessage("Product ID and name are required.");
      return;
    }
    if (draft.approval_required && !draft.require_subscription) {
      setMessage("Approval requires a subscription.");
      return;
    }
    if (draft.limit && (!/^[1-9]\d*$/.test(draft.limit) || !Number.isSafeInteger(Number(draft.limit)))) {
      setMessage("Subscription limit must be a positive whole number.");
      return;
    }
    setBusy(true);
    onBusyChange(true);
    try {
      const path = `/apim/management/products/${encodeURIComponent(id)}`;
      if (isNew) {
        const current = await request<Product[]>("/apim/management/products");
        if (current.some((product) => product.id === id)) {
          setMessage(`Product ${id} already exists. Select it to edit.`);
          return;
        }
      } else await request<Details>(path);
      const values = {
        name: draft.name.trim(),
        description: draft.description || null,
        state: draft.state,
        require_subscription: draft.require_subscription,
        approval_required: draft.approval_required,
        terms: draft.terms || null,
        subscriptions_limit: draft.limit ? Number(draft.limit) : null,
      };
      const previous: Record<string, string | number | boolean | null> = {
        name: baseline.name.trim(),
        description: baseline.description || null,
        state: baseline.state,
        require_subscription: baseline.require_subscription,
        approval_required: baseline.approval_required,
        terms: baseline.terms || null,
        subscriptions_limit: baseline.limit ? Number(baseline.limit) : null,
      };
      const updates = isNew
        ? values
        : Object.fromEntries(Object.entries(values).filter(([key, value]) => previous[key] !== value));
      await request(path, { method: "PUT", body: JSON.stringify(updates) });
      const savedDraft = { ...draft, id };
      setDraft(savedDraft);
      setBaseline(savedDraft);
      setIsNew(false);
      clean();
      setMessage(`Saved product ${id}.`);
      try {
        await onChanged();
      } catch (error) {
        setMessage(
          `Saved product ${id}, but refresh failed: ${error instanceof Error ? error.message : "Unknown error"}`,
        );
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to save product.");
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  }
  async function associate(add: boolean) {
    if (!apiId || isNew) return;
    setBusy(true);
    onBusyChange(true);
    try {
      const path = `/apim/management/products/${encodeURIComponent(draft.id)}/apis/${encodeURIComponent(apiId)}`;
      await request(path, { method: add ? "PUT" : "DELETE" });
      setMessage(`${add ? "Added" : "Removed"} API ${apiId} ${add ? "to" : "from"} product ${draft.id}.`);
      try {
        await onChanged();
      } catch (error) {
        setMessage(
          `API association saved, but refresh failed: ${error instanceof Error ? error.message : "Unknown error"}`,
        );
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to update API association.");
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  }
  const linked = apis.find((api) => api.id === apiId)?.products.includes(draft.id) ?? false;
  return (
    <section className="products-panel">
      <h2>Products</h2>
      <p>
        Create and publish products, then associate APIs. Published subscription products without group restrictions are
        visible to portal users; open products are visible only to administrators.
      </p>
      <label>
        Product
        <select
          aria-label="Product"
          value={isNew ? "" : draft.id}
          disabled={disabled || busy}
          onChange={(e) => void choose(e.target.value)}
        >
          <option value="">New product</option>
          {products.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.id})
            </option>
          ))}
        </select>
      </label>
      <form onSubmit={(e) => void save(e)}>
        <fieldset disabled={disabled || busy}>
          <label>
            Product ID
            <input value={draft.id} disabled={!isNew} onChange={(e) => change({ ...draft, id: e.target.value })} />
          </label>
          <label>
            Name
            <input value={draft.name} onChange={(e) => change({ ...draft, name: e.target.value })} />
          </label>
          <label>
            Description
            <textarea value={draft.description} onChange={(e) => change({ ...draft, description: e.target.value })} />
          </label>
          <label>
            Publication
            <select
              aria-label="Publication"
              value={draft.state}
              onChange={(e) => change({ ...draft, state: e.target.value })}
            >
              <option value="not_published">Unpublished</option>
              <option value="published">Published</option>
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.require_subscription}
              onChange={(e) =>
                change({
                  ...draft,
                  require_subscription: e.target.checked,
                  approval_required: e.target.checked && draft.approval_required,
                })
              }
            />{" "}
            Require subscription
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.approval_required}
              disabled={!draft.require_subscription}
              onChange={(e) => change({ ...draft, approval_required: e.target.checked })}
            />{" "}
            Require approval
          </label>
          <label>
            Subscription limit
            <input
              inputMode="numeric"
              value={draft.limit}
              onChange={(e) => change({ ...draft, limit: e.target.value })}
            />
          </label>
          <label>
            Terms
            <textarea value={draft.terms} onChange={(e) => change({ ...draft, terms: e.target.value })} />
          </label>
          <button type="submit" disabled={!dirty}>
            Save product
          </button>
        </fieldset>
      </form>
      {!isNew && (
        <div>
          <h3>API association</h3>
          <label>
            API
            <select
              aria-label="API"
              value={apiId}
              disabled={disabled || busy}
              onChange={(e) => setApiId(e.target.value)}
            >
              <option value="">Select API</option>
              {apis.map((api) => (
                <option key={api.id} value={api.id}>
                  {api.name} ({api.id})
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={disabled || busy || dirty || !apiId || linked}
            onClick={() => void associate(true)}
          >
            Add API
          </button>
          <button
            type="button"
            disabled={disabled || busy || dirty || !apiId || !linked}
            onClick={() => void associate(false)}
          >
            Remove API
          </button>
        </div>
      )}
      <p role="status" aria-live="polite">
        {message}
      </p>
    </section>
  );
}
