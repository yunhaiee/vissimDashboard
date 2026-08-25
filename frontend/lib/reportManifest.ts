// ---------------------------------------------------------------------------
// The typesetter's MANIFEST, read out of the artifact bundle it ships in.
//
// R-5a (backend/services/report/typeset.py) returns two things per compose:
// the .docx from `POST /api/report/compose`, and from
// `POST /api/report/compose/artifacts` a zip carrying every graphic figure's
// SVG ORIGINAL plus `manifest.json`. The manifest is where the document's own
// honesty record lives -- which sections were EXCLUDED BY SELECTION (a state
// distinct from "no evidence"), which style fields fell back to the Prism
// default and why, and which limitations the typesetter names for this
// document (`svg_not_embedded_in_docx` above all).
//
// WHY THIS FILE EXISTS AT ALL, MEASURED. `POST /compose`'s response body IS
// the .docx; the manifest is not in it. That route publishes only two headline
// COUNTS, as response headers (`X-Prism-Report-Sections`,
// `X-Prism-Report-Limitations`) -- counts, never the sentences. The only place
// the typesetter publishes its limitation SENTENCES over HTTP is
// `manifest.json` inside this zip. So the /report screen either reads the zip
// or writes its own version of those sentences, and writing them is exactly
// what this product does not do: an absence or a limitation is stated in the
// server's own words or not at all (lib/serverText.ts states the rule).
//
// THE ZIP READER IS DELIBERATELY MINIMAL. It walks the central directory,
// finds ONE entry by exact name, and inflates it. It supports the two storage
// methods a Python `zipfile.ZipFile(..., ZIP_DEFLATED)` archive can produce
// (stored=0, deflate=8) and refuses anything else BY NAME rather than
// returning a half-decoded blob. Every failure is a named reason, never a
// thrown surprise and never `null` standing in for four different problems --
// the caller renders the reason.
// ---------------------------------------------------------------------------

/** A sentence the report backend wrote, in the three languages it ships. */
export type ReportManifestText = { ko: string; en: string; ja: string };

/** `services.report.typeset` — `LIMITATION_*`'s shape. `code` is stable; the
 * reason is the server's own prose and is never rewritten on this side. */
export type TypesetLimitation = {
  code: string;
  reason: ReportManifestText;
};

/** `services.report.style_apply.AppliedStyle.fallbacks` — one per style field
 * that the chosen profile did not supply. `used_default` is the Prism default
 * that stood in; `profile_reason` (when present) is R-4's own recorded words
 * for why the profile had no value there. */
export type TypesetStyleFallback = {
  field: string;
  cause: string;
  used_default: unknown;
  reason: ReportManifestText;
  profile_reason?: string;
};

/** `services.report.typeset.SECTION_STATE_*`. `excluded_by_selection` is the
 * THIRD state and is not an evidence gap: the user left the section out. */
export const SECTION_STATE_INCLUDED = "included";
export const SECTION_STATE_EXCLUDED = "excluded_by_selection";

export type TypesetManifestSection = {
  id: string;
  title: string;
  state: string;
  heading_number: string | null;
  figure_ids: string[];
  named_absences: { figure_id: string | null; reason: string }[];
};

export type TypesetManifestArtifact = {
  name: string;
  content_type: string;
  bytes: number;
};

export type TypesetManifest = {
  kind: string;
  generated_at: string;
  language: string;
  title: string;
  style: {
    source: string;
    profile_id: string | null;
    profile_name: string | null;
    fallbacks: TypesetStyleFallback[];
    resolved: Record<string, unknown>;
  };
  sections: TypesetManifestSection[];
  figures: {
    figure_id: string;
    section_id: string;
    kind: string;
    absent: boolean;
    caption: string | null;
    artifact: string | null;
  }[];
  artifacts: TypesetManifestArtifact[];
  limitations: TypesetLimitation[];
};

/** The manifest entry's name inside the bundle —
 * `services.report.typeset.ARTIFACT_MANIFEST_NAME`. */
export const REPORT_MANIFEST_ENTRY = "manifest.json";

/** Why a manifest could not be read. Each is a DIFFERENT sentence on screen:
 * "this browser cannot inflate" is not "the bundle carried no manifest". */
export type ManifestUnreadableReason =
  | "no_decompression_stream"
  | "not_a_zip"
  | "entry_missing"
  | "unsupported_compression"
  | "not_json";

export type ManifestRead =
  | { ok: true; manifest: TypesetManifest }
  | { ok: false; reason: ManifestUnreadableReason };

const EOCD_SIGNATURE = 0x06054b50;
const CENTRAL_SIGNATURE = 0x02014b50;
const LOCAL_SIGNATURE = 0x04034b50;
const EOCD_MIN_BYTES = 22;
const METHOD_STORED = 0;
const METHOD_DEFLATE = 8;

/**
 * `manifest.json` out of the artifact bundle, parsed.
 *
 * Reads ONE named entry. It never enumerates the archive for the caller and
 * never writes anything to disk: the SVGs in the same bundle are the user's
 * download, not this screen's to unpack.
 */
export async function readReportManifest(bundle: Blob): Promise<ManifestRead> {
  const bytes = new Uint8Array(await bundle.arrayBuffer());
  const found = findEntry(bytes, REPORT_MANIFEST_ENTRY);
  if (!found.ok) return found;

  let raw: Uint8Array;
  if (found.method === METHOD_STORED) {
    raw = found.payload;
  } else if (found.method === METHOD_DEFLATE) {
    if (typeof DecompressionStream === "undefined") return { ok: false, reason: "no_decompression_stream" };
    raw = await inflateRaw(found.payload);
  } else {
    return { ok: false, reason: "unsupported_compression" };
  }

  try {
    return { ok: true, manifest: JSON.parse(new TextDecoder().decode(raw)) as TypesetManifest };
  } catch {
    return { ok: false, reason: "not_json" };
  }
}

type EntryFound = { ok: true; method: number; payload: Uint8Array };

function findEntry(bytes: Uint8Array, name: string): EntryFound | { ok: false; reason: ManifestUnreadableReason } {
  if (bytes.length < EOCD_MIN_BYTES) return { ok: false, reason: "not_a_zip" };
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

  // The end-of-central-directory record is last, but a trailing comment can
  // push it back by up to 0xffff bytes, so it is searched for rather than
  // assumed to sit at a fixed offset.
  let eocd = -1;
  const floor = Math.max(0, bytes.length - (EOCD_MIN_BYTES + 0xffff));
  for (let at = bytes.length - EOCD_MIN_BYTES; at >= floor; at -= 1) {
    if (view.getUint32(at, true) === EOCD_SIGNATURE) {
      eocd = at;
      break;
    }
  }
  if (eocd < 0) return { ok: false, reason: "not_a_zip" };

  const count = view.getUint16(eocd + 10, true);
  let at = view.getUint32(eocd + 16, true);
  const decoder = new TextDecoder();

  for (let index = 0; index < count; index += 1) {
    if (at + 46 > bytes.length || view.getUint32(at, true) !== CENTRAL_SIGNATURE) {
      return { ok: false, reason: "not_a_zip" };
    }
    const method = view.getUint16(at + 10, true);
    const compressedSize = view.getUint32(at + 20, true);
    const nameLength = view.getUint16(at + 28, true);
    const extraLength = view.getUint16(at + 30, true);
    const commentLength = view.getUint16(at + 32, true);
    const localAt = view.getUint32(at + 42, true);
    const entryName = decoder.decode(bytes.subarray(at + 46, at + 46 + nameLength));

    if (entryName === name) {
      if (localAt + 30 > bytes.length || view.getUint32(localAt, true) !== LOCAL_SIGNATURE) {
        return { ok: false, reason: "not_a_zip" };
      }
      // The LOCAL header's own name/extra lengths, not the central one's: the
      // two legitimately differ (extra fields are rewritten per header), and
      // using the central directory's here lands mid-payload.
      const localNameLength = view.getUint16(localAt + 26, true);
      const localExtraLength = view.getUint16(localAt + 28, true);
      const start = localAt + 30 + localNameLength + localExtraLength;
      if (start + compressedSize > bytes.length) return { ok: false, reason: "not_a_zip" };
      return { ok: true, method, payload: bytes.subarray(start, start + compressedSize) };
    }

    at += 46 + nameLength + extraLength + commentLength;
  }

  return { ok: false, reason: "entry_missing" };
}

async function inflateRaw(payload: Uint8Array): Promise<Uint8Array> {
  // `new Uint8Array(payload)` COPIES: `payload` is a subarray view over the
  // whole archive, and a Blob built on the view's backing buffer would carry
  // the entire zip rather than this one entry's bytes.
  const stream = new Blob([new Uint8Array(payload)]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}
