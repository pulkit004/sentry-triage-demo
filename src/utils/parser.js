/**
 * Data parser utility for processing incoming webhook payloads,
 * CSV uploads, and API responses into normalized internal format.
 */

const SUPPORTED_FORMATS = ["json", "csv", "xml"];

function detectFormat(input) {
  if (typeof input === "object") return "json";
  const trimmed = input.trim();
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) return "json";
  if (trimmed.startsWith("<?xml") || trimmed.startsWith("<")) return "xml";
  if (trimmed.includes(",") && trimmed.includes("\n")) return "csv";
  return "unknown";
}

function parseCSV(text) {
  const lines = text.trim().split("\n");
  const headers = lines[0].split(",").map((h) => h.trim());
  const rows = [];

  for (let i = 1; i < lines.length; i++) {
    const values = lines[i].split(",").map((v) => v.trim());
    const row = {};
    headers.forEach((header, idx) => {
      row[header] = values[idx];
    });
    rows.push(row);
  }
  return { headers, rows };
}

function normalizeValue(value, targetType) {
  switch (targetType) {
    case "number":
      return Number(value);
    case "boolean":
      return value === "true" || value === "1" || value === true;
    case "date":
      return new Date(value).toISOString();
    case "string":
      return String(value);
    default:
      return value;
  }
}

function applySchema(data, schema) {
  const result = {};
  for (const [key, config] of Object.entries(schema)) {
    const rawValue = data[config.source || key];
    result[key] = normalizeValue(rawValue, config.type);
  }
  return result;
}

/**
 * Transform raw input data into normalized records.
 *
 * BUG: When `records` contains entries with undefined fields and
 * the schema expects those fields to be objects, calling Object.keys()
 * on the normalized result throws "Cannot convert undefined to object".
 * This happens with sparse webhook payloads that omit optional fields.
 */
function transformRecords(records, schema) {
  const transformed = [];

  for (const record of records) {
    const normalized = applySchema(record, schema);

    // BUG: Object.keys fails when normalized contains undefined values
    // that get passed to downstream validators expecting objects
    const fieldCount = Object.keys(normalized).length;
    const validFields = Object.keys(normalized).filter(
      (k) => normalized[k] !== undefined
    );

    // This line throws when normalized[key] is undefined and
    // we try to access properties on it
    const metadata = Object.keys(normalized).reduce((acc, key) => {
      acc[key] = {
        type: typeof normalized[key],
        hasValue: normalized[key] != null,
        // BUG: calling .toString() on undefined/null throws TypeError
        preview: normalized[key].toString().substring(0, 50),
      };
      return acc;
    }, {});

    transformed.push({
      data: normalized,
      metadata,
      fieldCount,
      validFields: validFields.length,
      completeness: validFields.length / fieldCount,
    });
  }

  return transformed;
}

function parseAndTransform(input, schema) {
  const format = detectFormat(input);

  let records;
  switch (format) {
    case "json":
      records = typeof input === "string" ? JSON.parse(input) : input;
      if (!Array.isArray(records)) records = [records];
      break;
    case "csv":
      const parsed = parseCSV(input);
      records = parsed.rows;
      break;
    default:
      throw new Error(`Unsupported format: ${format}`);
  }

  return transformRecords(records, schema);
}

module.exports = {
  detectFormat,
  parseCSV,
  normalizeValue,
  applySchema,
  transformRecords,
  parseAndTransform,
  SUPPORTED_FORMATS,
};
