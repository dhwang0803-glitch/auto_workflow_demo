"use client";

// Minimal JSON-Schema → form mapper. Supports the 5 types we ship in the
// catalog (string, number/integer, boolean, enum, secret_ref). Anything else
// renders a JSON textarea so the user is never blocked.

interface PropertySchema {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  format?: string;
  items?: PropertySchema;
}

interface ObjectSchema {
  type?: string;
  properties?: Record<string, PropertySchema>;
  required?: string[];
}

export function PropertyForm({
  schema,
  value,
  onChange,
}: {
  schema: ObjectSchema;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const props = schema?.properties;
  if (!props || Object.keys(props).length === 0) {
    return <RawJsonEditor value={value} onChange={onChange} />;
  }
  const required = new Set(schema.required ?? []);
  const update = (key: string, v: unknown) => onChange({ ...value, [key]: v });

  return (
    <div className="space-y-3">
      {Object.entries(props).map(([key, propSchema]) => (
        <Field
          key={key}
          name={key}
          required={required.has(key)}
          schema={propSchema}
          value={value[key]}
          onChange={(v) => update(key, v)}
        />
      ))}
    </div>
  );
}

function Field({
  name,
  required,
  schema,
  value,
  onChange,
}: {
  name: string;
  required: boolean;
  schema: PropertySchema;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const label = (
    <label className="block text-xs font-medium mb-1">
      {schema.title ?? name}
      {required && <span className="text-red-500 ml-0.5">*</span>}
    </label>
  );

  if (schema.enum) {
    return (
      <div>
        {label}
        <select
          className="w-full border rounded px-2 py-1 text-sm"
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">—</option>
          {schema.enum.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </div>
    );
  }

  if (schema.type === "boolean") {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
        {schema.title ?? name}
      </label>
    );
  }

  if (schema.type === "number" || schema.type === "integer") {
    return (
      <div>
        {label}
        <input
          type="number"
          className="w-full border rounded px-2 py-1 text-sm"
          value={(value as number | string) ?? ""}
          onChange={(e) =>
            onChange(e.target.value === "" ? undefined : Number(e.target.value))
          }
        />
      </div>
    );
  }

  if (schema.format === "secret_ref") {
    return (
      <div>
        {label}
        <input
          type="text"
          placeholder="credential_id (PLAN_07 wires picker)"
          className="w-full border rounded px-2 py-1 text-sm font-mono"
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
        <p className="text-[10px] text-gray-500 mt-0.5">
          References a Credentials entry. UI picker lands in PLAN_07.
        </p>
      </div>
    );
  }

  // string + fallback
  return (
    <div>
      {label}
      <input
        type="text"
        className="w-full border rounded px-2 py-1 text-sm"
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value)}
      />
      {schema.description && (
        <p className="text-[10px] text-gray-500 mt-0.5">{schema.description}</p>
      )}
    </div>
  );
}

function RawJsonEditor({
  value,
  onChange,
}: {
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}) {
  return (
    <div>
      <p className="text-xs text-gray-500 mb-1">
        No schema — edit raw JSON. Save will fail if invalid.
      </p>
      <textarea
        className="w-full border rounded px-2 py-1 text-xs font-mono h-48"
        defaultValue={JSON.stringify(value, null, 2)}
        onBlur={(e) => {
          try {
            onChange(JSON.parse(e.target.value));
          } catch {
            // keep prior value silently — toolbar/save will surface issues
          }
        }}
      />
    </div>
  );
}
