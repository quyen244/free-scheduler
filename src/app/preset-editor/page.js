"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  FiChevronDown,
  FiChevronUp,
  FiDownload,
  FiEye,
  FiEyeOff,
  FiImage,
  FiLayers,
  FiPlus,
  FiSave,
  FiScissors,
  FiTrash2,
  FiType,
  FiUpload,
  FiVideo,
} from "react-icons/fi";
import { PALETTE } from "@/lib/preset-editor";

// Canvas pixels per aspect. A preset only ever stores 0–1 coordinates; these
// are needed to convert a font size or a contain-fit into preview pixels the
// same way the renderer converts them into real ones.
const CANVAS = {
  vertical: { w: 1080, h: 1920 },
  landscape: { w: 1920, h: 1080 },
};

const COLORS = Object.keys(PALETTE);
const SOURCES = [
  ["static", "Chữ cố định"],
  ["title", "Tiêu đề video"],
  ["part", "Phần (số chunk)"],
];

const Z = { main: 10, host: 20, logo: 30, watermark: 40, text: 50, subtitle: 60 };

const layerRect = (x, y, w, h, z) => ({ x, y, w, h, z, visible: true });

const freshLayout = (canvas) => {
  const vertical = canvas === "vertical_9_16";
  return {
    canvas,
    background_asset: null,
    main_video: vertical ? layerRect(0, 0.28, 1, 0.44, Z.main) : layerRect(0, 0, 1, 1, Z.main),
    host: vertical ? layerRect(0.06, 0.43, 0.38, 0.46, Z.host) : layerRect(0.67, 0.28, 0.26, 0.62, Z.host),
    logo: layerRect(0.04, 0.04, 0.16, 0.1, Z.logo),
    watermark: layerRect(0.79, 0.04, 0.16, 0.1, Z.watermark),
    blur_regions: [],
    text_layers: [],
    subtitle: {
      y: vertical ? 0.755 : 0.88,
      size: vertical ? 46 : 42,
      color: "white",
      outline_color: "black",
      outline: 3,
      align: "center",
      margin_ratio: 0.06,
      max_chars_per_line: vertical ? 38 : 58,
      max_lines: 2,
      z: Z.subtitle,
      visible: true,
    },
    mask_corrections: [],
    intro_enabled: true,
    intro_duration_s: 8,
  };
};

const newDraft = (presetId = "an-so") => ({
  schema_version: 2,
  preset_id: presetId,
  name: "Ẩn Số",
  status: "draft",
  host_asset: null,
  host_alpha_revision: null,
  mock_main_asset: null,
  vertical: freshLayout("vertical_9_16"),
  landscape: freshLayout("landscape_16_9"),
});

const assetUrl = (presetId, asset) =>
  asset
    ? `/api/preset-editor/assets?presetId=${encodeURIComponent(presetId)}&asset=${encodeURIComponent(asset)}`
    : "";
const brandUrl = (brandId, slot) =>
  brandId ? `/api/preset-editor/brands?brandId=${encodeURIComponent(brandId)}&slot=${slot}` : "";
const isVideo = (asset) => /\.(mp4|mov|webm)$/i.test(asset || "");
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

async function api(action, payload = {}, method = "POST") {
  const url =
    method === "GET"
      ? `/api/preset-editor?${new URLSearchParams({ action, ...payload })}`
      : "/api/preset-editor";
  const response = await fetch(
    url,
    method === "GET"
      ? { cache: "no-store" }
      : {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action, ...payload }),
        },
  );
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Editor request failed");
  return body;
}

/**
 * Where the source frame actually lands inside its slot, in canvas fractions.
 *
 * The renderer fits the source inside the slot preserving aspect ratio instead
 * of stretching it. Blur regions are stored against the source frame, so if
 * this fit disagrees with preset.resolve, a blur drawn over a face in the
 * editor lands somewhere else in the render.
 */
function fittedVideo(slot, canvasSize, source) {
  if (!source?.w || !source?.h) return { x: slot.x, y: slot.y, w: slot.w, h: slot.h };
  const scale = Math.min((slot.w * canvasSize.w) / source.w, (slot.h * canvasSize.h) / source.h);
  const w = (source.w * scale) / canvasSize.w;
  const h = (source.h * scale) / canvasSize.h;
  return { x: slot.x + (slot.w - w) / 2, y: slot.y + (slot.h - h) / 2, w, h };
}

function NumberField({ label, value, min, max, step, onChange }) {
  return (
    <label className="text-[10px] text-zinc-500">
      {label}
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-xs text-zinc-100 outline-none focus:border-violet-500"
      />
    </label>
  );
}

function RectInputs({ label, value, onChange }) {
  return (
    <section className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
      <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-400">{label}</p>
      <div className="grid grid-cols-4 gap-2">
        {["x", "y", "w", "h"].map((field) => (
          <NumberField
            key={field}
            label={field.toUpperCase()}
            value={Number(value[field]).toFixed(3)}
            min={0}
            max={1}
            step={0.005}
            onChange={(next) => onChange({ ...value, [field]: next })}
          />
        ))}
      </div>
    </section>
  );
}

function ColorPicker({ value, onChange }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {COLORS.map((name) => (
        <button
          key={name}
          title={name}
          onClick={() => onChange(name)}
          style={{ backgroundColor: PALETTE[name] }}
          className={`h-6 w-6 rounded border-2 ${value === name ? "border-violet-400" : "border-zinc-700"}`}
        />
      ))}
    </div>
  );
}

export default function PresetEditorPage() {
  const [draft, setDraft] = useState(newDraft());
  const [aspect, setAspect] = useState("vertical");
  const [message, setMessage] = useState("Draft local: chưa được workflow sử dụng.");
  const [busy, setBusy] = useState(false);
  const [published, setPublished] = useState(null);
  const [rvm, setRvm] = useState(null);
  const [matting, setMatting] = useState(null);
  const [brands, setBrands] = useState([]);
  const [brandId, setBrandId] = useState("");
  const [selected, setSelected] = useState({ kind: "main_video", index: -1 });
  const [drag, setDrag] = useState(null);
  const [brushMode, setBrushMode] = useState(null);
  const [mockSize, setMockSize] = useState(null);
  const [preview, setPreview] = useState({ w: 0, h: 0 });
  const fileInput = useRef(null);
  const uploadRole = useRef(null);
  const canvasRef = useRef(null);

  const layout = draft[aspect];
  const canvasSize = CANVAS[aspect];
  const ratioClass = aspect === "vertical" ? "aspect-[9/16] max-h-[680px]" : "aspect-video";
  const title = aspect === "vertical" ? "9:16 · Facebook / TikTok" : "16:9 · YouTube";
  const brand = useMemo(() => brands.find((item) => item.brand_id === brandId) || null, [brands, brandId]);
  const fitted = useMemo(
    () => fittedVideo(layout.main_video, canvasSize, mockSize),
    [layout.main_video, canvasSize, mockSize],
  );
  // Preview pixels per canvas pixel, so a size of 46 looks here the size it
  // will actually be in the render instead of a guessed number.
  const scale = preview.h ? preview.h / canvasSize.h : 0;

  useEffect(() => {
    api("rvm", {}, "GET")
      .then(setRvm)
      .catch((error) => setMessage(error.message));
    fetch("/api/preset-editor/brands", { cache: "no-store" })
      .then((response) => response.json())
      .then((body) => {
        setBrands(body.brands || []);
        if (body.brands?.length) setBrandId(body.brands[0].brand_id);
      })
      .catch(() => {});
    // The shipped local asset set is intentionally a draft. It gives the
    // operator a useful starting point, yet must still be explicitly saved and
    // published before n8n can select it.
    api("draft", { presetId: "an-so" }, "GET")
      .then((existing) => {
        setDraft({ ...newDraft(existing.preset_id), ...existing });
        setMessage("Đã mở draft an-so local. Chỉnh sửa rồi Save draft / Publish revision.");
      })
      .catch(() => {});
  }, []);

  useLayoutEffect(() => {
    const node = canvasRef.current;
    if (!node) return undefined;
    const observer = new ResizeObserver(([entry]) =>
      setPreview({ w: entry.contentRect.width, h: entry.contentRect.height }),
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [aspect]);

  const setLayout = useCallback(
    (updater) =>
      setDraft((current) => ({
        ...current,
        [aspect]: typeof updater === "function" ? updater(current[aspect]) : updater,
      })),
    [aspect],
  );

  const readRect = (state, kind, index) =>
    kind === "blur" ? state.blur_regions[index] : kind === "text" ? state.text_layers[index] : state[kind];

  const writeRect = (kind, index, rect) =>
    setLayout((state) => {
      if (kind === "blur") {
        return { ...state, blur_regions: state.blur_regions.map((item, at) => (at === index ? rect : item)) };
      }
      if (kind === "text") {
        return { ...state, text_layers: state.text_layers.map((item, at) => (at === index ? rect : item)) };
      }
      return { ...state, [kind]: rect };
    });

  const setAsset = (role, asset) => {
    if (role === "host") {
      // One host video dresses both aspects, and a new upload invalidates the
      // alpha that was matted from the previous one.
      setDraft((current) => ({ ...current, host_asset: asset, host_alpha_revision: null }));
      setMatting(null);
      return;
    }
    if (role === "mock") {
      setMockSize(null);
      setDraft((current) => ({ ...current, mock_main_asset: asset }));
      return;
    }
    setLayout((state) => ({ ...state, [`${role}_asset`]: asset }));
  };

  const triggerUpload = (role) => {
    uploadRole.current = role;
    fileInput.current?.click();
  };

  const upload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const role = uploadRole.current;
    setBusy(true);
    try {
      const form = new FormData();
      form.set("presetId", draft.preset_id);
      form.set("file", file);
      const response = await fetch("/api/preset-editor/assets", { method: "POST", body: form });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Upload failed");
      setAsset(role, body.asset);
      setMessage(`Đã tải ${body.asset}. Bấm Save draft để lưu cấu hình.`);
    } catch (error) {
      setMessage(error.message);
    } finally {
      event.target.value = "";
      setBusy(false);
    }
  };

  const saveDraft = async () => {
    setBusy(true);
    try {
      const saved = await api("save-draft", draft);
      setDraft(saved);
      setPublished(null);
      setMessage("Đã lưu draft. n8n vẫn không thể sử dụng draft này.");
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    setBusy(true);
    try {
      const result = await api("publish", draft);
      setPublished(result);
      setMessage(`Đã publish ${result.preset_id}@${result.revision}. Có thể export JSON.`);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };

  const exportPreset = () => {
    if (!published) return;
    const blob = new Blob([JSON.stringify(published, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${published.preset_id}-v${published.revision}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const pollMatting = async (jobId) => {
    try {
      const state = await api("matting", { jobId }, "GET");
      setMatting(state);
      if (state.state === "queued" || state.state === "running") {
        window.setTimeout(() => pollMatting(jobId), 1200);
      }
      if (state.state === "done") {
        // The matting job id IS the alpha revision the renderer looks up.
        setDraft((current) => ({ ...current, host_alpha_revision: state.job_id }));
        setMessage("RVM đã tạo alpha mask. Save draft để gắn revision này vào preset.");
      }
      if (state.state === "failed") setMessage(state.error || "RVM thất bại.");
    } catch (error) {
      setMessage(error.message);
    }
  };

  const startMatting = async () => {
    if (!draft.host_asset) {
      setMessage("Hãy upload host MP4/MOV trước.");
      return;
    }
    setBusy(true);
    try {
      const accepted = await api("matting", {
        preset_id: draft.preset_id,
        host_asset: draft.host_asset,
        corrections: layout.mask_corrections,
      });
      setMatting(accepted);
      setMessage("RVM đang tách host trên GPU…");
      pollMatting(accepted.job_id);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };

  const pointerDown = (event, kind, index, mode) => {
    if (brushMode) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setSelected({ kind, index });
    setDrag({
      kind,
      index,
      mode,
      startX: event.clientX,
      startY: event.clientY,
      rect: readRect(layout, kind, index),
    });
  };

  const pointerMove = (event) => {
    if (!drag || !canvasRef.current) return;
    const box = canvasRef.current.getBoundingClientRect();
    let dx = (event.clientX - drag.startX) / box.width;
    let dy = (event.clientY - drag.startY) / box.height;
    // A blur region is a child of the source frame, not of the canvas, so its
    // delta has to be expressed against the fitted video instead of the page.
    if (drag.kind === "blur") {
      dx /= fitted.w || 1;
      dy /= fitted.h || 1;
    }
    const rect = drag.rect;
    const hasHeight = drag.kind !== "text";
    if (drag.mode === "move") {
      writeRect(drag.kind, drag.index, {
        ...rect,
        x: clamp(rect.x + dx, 0, 1 - rect.w),
        y: clamp(rect.y + dy, 0, hasHeight ? 1 - rect.h : 1),
      });
      return;
    }
    const next = { ...rect, w: clamp(rect.w + dx, 0.02, 1 - rect.x) };
    if (hasHeight) next.h = clamp(rect.h + dy, 0.02, 1 - rect.y);
    writeRect(drag.kind, drag.index, next);
  };

  const paintCorrection = (event) => {
    if (!brushMode || !canvasRef.current) return;
    const box = canvasRef.current.getBoundingClientRect();
    const x = (event.clientX - box.left) / box.width;
    const y = (event.clientY - box.top) / box.height;
    const host = layout.host;
    if (x < host.x || x > host.x + host.w || y < host.y || y > host.y + host.h) {
      setMessage("Brush phải được đặt trên host.");
      return;
    }
    setLayout((state) => ({
      ...state,
      mask_corrections: [
        ...state.mask_corrections,
        { x: (x - host.x) / host.w, y: (y - host.y) / host.h, radius: 0.035, mode: brushMode },
      ],
    }));
    setMessage("Đã thêm correction. Chạy RVM lại để áp dụng vào alpha mask.");
  };

  // Every stackable thing in one list. Highest z first, because that is how an
  // operator reads a layers panel: what is on top is at the top.
  const stack = useMemo(() => {
    const entries = [
      { kind: "main_video", index: -1, label: "Main video", z: layout.main_video.z, visible: layout.main_video.visible },
      { kind: "host", index: -1, label: "Host (RVM)", z: layout.host.z, visible: layout.host.visible },
      { kind: "logo", index: -1, label: "Logo (brand)", z: layout.logo.z, visible: layout.logo.visible },
      {
        kind: "watermark",
        index: -1,
        label: "Watermark (brand)",
        z: layout.watermark.z,
        visible: layout.watermark.visible,
      },
      ...layout.text_layers.map((item, index) => ({
        kind: "text",
        index,
        label: `Text · ${item.id}`,
        z: item.z,
        visible: item.visible,
      })),
      { kind: "subtitle", index: -1, label: "Subtitle", z: layout.subtitle.z, visible: layout.subtitle.visible },
    ];
    return entries.sort((a, b) => b.z - a.z);
  }, [layout]);

  const setZ = (entry, direction) => {
    const ordered = [...stack].sort((a, b) => a.z - b.z);
    const at = ordered.findIndex((item) => item.kind === entry.kind && item.index === entry.index);
    const swap = ordered[at + direction];
    if (!swap) return;
    const assign = (state, item, z) =>
      item.kind === "text"
        ? { ...state, text_layers: state.text_layers.map((t, i) => (i === item.index ? { ...t, z } : t)) }
        : { ...state, [item.kind]: { ...state[item.kind], z } };
    setLayout((state) => assign(assign(state, entry, swap.z), swap, entry.z));
  };

  const toggleVisible = (entry) =>
    setLayout((state) =>
      entry.kind === "text"
        ? {
            ...state,
            text_layers: state.text_layers.map((t, i) => (i === entry.index ? { ...t, visible: !t.visible } : t)),
          }
        : { ...state, [entry.kind]: { ...state[entry.kind], visible: !state[entry.kind].visible } },
    );

  const addTextLayer = () =>
    setLayout((state) => ({
      ...state,
      text_layers: [
        ...state.text_layers,
        {
          id: `text-${state.text_layers.length + 1}`,
          source: "static",
          text: "Tiêu đề mẫu",
          x: 0.08,
          y: 0.06,
          w: 0.84,
          size: aspect === "vertical" ? 64 : 52,
          color: "white",
          align: "center",
          max_chars_per_line: 26,
          max_lines: 3,
          line_spacing: 1.15,
          z: Z.text + state.text_layers.length,
          visible: true,
        },
      ],
    }));

  const patchText = (index, patch) =>
    setLayout((state) => ({
      ...state,
      text_layers: state.text_layers.map((item, at) => (at === index ? { ...item, ...patch } : item)),
    }));

  const patchSubtitle = (patch) => setLayout((state) => ({ ...state, subtitle: { ...state.subtitle, ...patch } }));

  const isSelected = (kind, index) => selected.kind === kind && selected.index === index;
  const frameClass = (kind, index) =>
    `absolute ${isSelected(kind, index) ? "outline outline-2 outline-violet-400" : "outline outline-1 outline-transparent hover:outline-violet-400/60"}`;

  const handle = (kind, index) => (
    <span
      onPointerDown={(event) => pointerDown(event, kind, index, "resize")}
      className="absolute -bottom-1.5 -right-1.5 h-3 w-3 cursor-nwse-resize rounded-sm border border-white bg-violet-500"
    />
  );

  const rendered = [...stack].sort((a, b) => a.z - b.z);
  const selectedText = selected.kind === "text" ? layout.text_layers[selected.index] : null;
  const selectedBlur = selected.kind === "blur" ? layout.blur_regions[selected.index] : null;

  return (
    <main className="min-h-0 flex-1 overflow-auto bg-zinc-950 px-4 py-6 text-zinc-100 sm:px-6">
      <input
        ref={fileInput}
        onChange={upload}
        className="hidden"
        type="file"
        accept="video/mp4,video/quicktime,video/webm,image/png,image/jpeg,image/webp"
      />
      <div className="mx-auto max-w-[1600px]">
        <div className="mb-5 flex flex-col gap-3 border-b border-zinc-800 pb-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-violet-400">Local visual preset</p>
            <h1 className="mt-1 text-2xl font-black tracking-tight">Visual editor · {draft.name}</h1>
            <p className="mt-1 max-w-3xl text-xs leading-relaxed text-zinc-400">
              Draft → validate → published revision. Preset giữ vị trí; brand profile giữ file logo/watermark. Ảnh mock
              chỉ dùng để canh layout, không bao giờ được render.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              href="/"
              className="rounded-lg border border-zinc-700 px-3 py-2 text-xs font-bold text-zinc-300 hover:bg-zinc-900"
            >
              Workspace
            </Link>
            <button
              onClick={saveDraft}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-lg border border-violet-500/50 bg-violet-500/10 px-3 py-2 text-xs font-bold text-violet-200 hover:bg-violet-500/20 disabled:opacity-50"
            >
              <FiSave /> Save draft
            </button>
            <button
              onClick={publish}
              disabled={busy}
              className="rounded-lg bg-violet-600 px-3 py-2 text-xs font-bold text-white hover:bg-violet-500 disabled:opacity-50"
            >
              Publish revision
            </button>
            <button
              onClick={exportPreset}
              disabled={!published}
              className="inline-flex items-center gap-2 rounded-lg border border-zinc-700 px-3 py-2 text-xs font-bold text-zinc-200 hover:bg-zinc-900 disabled:opacity-40"
            >
              <FiDownload /> Export JSON
            </button>
          </div>
        </div>

        <div className="mb-5 rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-3 text-xs text-zinc-300">
          <span className="mr-2 font-bold text-violet-300">Status</span>
          {message}
          {rvm && (
            <span
              className={`ml-3 rounded px-2 py-1 text-[10px] font-bold ${rvm.ready ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"}`}
            >
              RVM {rvm.ready ? "ready" : "model missing"}
            </span>
          )}
          {draft.host_alpha_revision && (
            <span className="ml-2 rounded bg-emerald-500/15 px-2 py-1 font-mono text-[10px] text-emerald-300">
              alpha {draft.host_alpha_revision.slice(0, 8)}
            </span>
          )}
        </div>

        <div className="grid gap-5 xl:grid-cols-[300px_minmax(0,1fr)_340px]">
          <aside className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
            <div>
              <label className="text-[10px] font-bold uppercase tracking-wider text-zinc-500">Preset ID</label>
              <input
                value={draft.preset_id}
                onChange={(event) => setDraft({ ...draft, preset_id: event.target.value.toLowerCase() })}
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none focus:border-violet-500"
              />
              <label className="mt-3 block text-[10px] font-bold uppercase tracking-wider text-zinc-500">Name</label>
              <input
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none focus:border-violet-500"
              />
            </div>

            <div className="border-t border-zinc-800 pt-4">
              <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-500">Assets</p>
              {[
                ["mock", "Ảnh mock main video", FiImage],
                ["host", "Host video", FiVideo],
                ["background", "Background", FiImage],
              ].map(([role, label, Icon]) => (
                <button
                  key={role}
                  onClick={() => triggerUpload(role)}
                  disabled={busy}
                  className="mb-2 flex w-full items-center justify-between rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-left text-xs hover:border-violet-500/70 disabled:opacity-50"
                >
                  <span className="flex items-center gap-2 text-zinc-200">
                    <Icon className="text-violet-300" />
                    {label}
                  </span>
                  <FiUpload className="text-zinc-500" />
                </button>
              ))}
              <p className="mt-2 break-words text-[10px] leading-relaxed text-zinc-500">
                Mock: {draft.mock_main_asset || "chưa có"}
                <br />
                Host: {draft.host_asset || "chưa có"}
                <br />
                Background: {layout.background_asset || "chưa có"}
              </p>
            </div>

            <div className="border-t border-zinc-800 pt-4">
              <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-500">Brand preview</p>
              <select
                value={brandId}
                onChange={(event) => setBrandId(event.target.value)}
                className="w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-2 text-xs outline-none focus:border-violet-500"
              >
                <option value="">Không xem trước brand</option>
                {brands.map((item) => (
                  <option key={item.brand_id} value={item.brand_id}>
                    {item.display_name}
                  </option>
                ))}
              </select>
              <p className="mt-2 text-[10px] leading-relaxed text-zinc-500">
                Brand profile giữ file logo + watermark. Preset chỉ lưu vị trí, nên một layout dùng được cho mọi brand.
              </p>
            </div>

            <div className="border-t border-zinc-800 pt-4">
              <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-500">Aspect</p>
              <div className="grid grid-cols-2 gap-2">
                {[
                  ["vertical", "9:16"],
                  ["landscape", "16:9"],
                ].map(([value, label]) => (
                  <button
                    key={value}
                    onClick={() => setAspect(value)}
                    className={`rounded-lg px-2 py-2 text-xs font-bold ${aspect === value ? "bg-violet-600 text-white" : "border border-zinc-700 text-zinc-400 hover:bg-zinc-800"}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="mt-2 text-[10px] text-zinc-500">
                Hai layout độc lập. Đổi ratio không ghi đè vị trí ratio kia.
              </p>
            </div>

            <div className="border-t border-zinc-800 pt-4">
              <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-500">Host mask · RVM</p>
              <button
                onClick={startMatting}
                disabled={busy || !draft.host_asset}
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-40"
              >
                <FiScissors /> {matting?.state === "running" ? "Đang segment…" : "Segment host"}
              </button>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {[
                  ["keep", "+ Keep brush", "border-emerald-400 bg-emerald-500/20 text-emerald-200"],
                  ["remove", "− Remove brush", "border-rose-400 bg-rose-500/20 text-rose-200"],
                ].map(([mode, label, active]) => (
                  <button
                    key={mode}
                    onClick={() => setBrushMode(brushMode === mode ? null : mode)}
                    className={`rounded border px-2 py-1.5 text-[10px] font-bold ${brushMode === mode ? active : "border-zinc-700 text-zinc-400"}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <button
                onClick={() => setLayout((state) => ({ ...state, mask_corrections: [] }))}
                className="mt-2 text-[10px] text-zinc-500 underline hover:text-zinc-300"
              >
                Clear {layout.mask_corrections.length} correction(s)
              </button>
              <p className="mt-2 text-[10px] leading-relaxed text-zinc-500">
                Không có alpha revision thì render bị từ chối, vì host sẽ đè lên nền thành một khối chữ nhật đục.
              </p>
            </div>
          </aside>

          <section className="min-w-0 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-bold">Preview · {title}</h2>
                <p className="text-[10px] text-zinc-500">
                  Kéo để di chuyển, kéo góc dưới-phải để resize. Blur bám theo khung hình gốc.
                </p>
              </div>
              <span className="rounded bg-zinc-800 px-2 py-1 font-mono text-[10px] text-zinc-400">
                {canvasSize.w}×{canvasSize.h}
              </span>
            </div>
            <div className="flex min-h-[620px] items-center justify-center overflow-auto rounded-lg bg-[radial-gradient(#27272a_1px,transparent_1px)] [background-size:12px_12px] p-4">
              <div
                ref={canvasRef}
                onPointerMove={pointerMove}
                onPointerUp={() => setDrag(null)}
                onPointerCancel={() => setDrag(null)}
                onClick={paintCorrection}
                className={`relative w-full max-w-[760px] select-none overflow-hidden bg-zinc-950 shadow-2xl ${ratioClass} ${brushMode ? "cursor-crosshair" : ""}`}
                style={
                  layout.background_asset
                    ? {
                        backgroundImage: `url("${assetUrl(draft.preset_id, layout.background_asset)}")`,
                        backgroundSize: "cover",
                      }
                    : {}
                }
              >
                {!layout.background_asset && (
                  <div className="absolute inset-0 bg-gradient-to-br from-indigo-950 via-zinc-950 to-violet-950" />
                )}

                {rendered.map((entry) => {
                  if (!entry.visible) return null;

                  if (entry.kind === "main_video") {
                    const slot = layout.main_video;
                    return (
                      <div
                        key="main"
                        onPointerDown={(event) => pointerDown(event, "main_video", -1, "move")}
                        className={`${frameClass("main_video", -1)} cursor-move`}
                        style={{
                          left: `${slot.x * 100}%`,
                          top: `${slot.y * 100}%`,
                          width: `${slot.w * 100}%`,
                          height: `${slot.h * 100}%`,
                        }}
                      >
                        {draft.mock_main_asset ? (
                          <img
                            src={assetUrl(draft.preset_id, draft.mock_main_asset)}
                            alt="mock main video"
                            draggable="false"
                            onLoad={(event) =>
                              setMockSize({
                                w: event.currentTarget.naturalWidth,
                                h: event.currentTarget.naturalHeight,
                              })
                            }
                            className="h-full w-full object-contain"
                          />
                        ) : (
                          <div className="flex h-full w-full items-center justify-center border border-dashed border-zinc-600 bg-zinc-900/60 text-[10px] text-zinc-500">
                            main video slot
                          </div>
                        )}
                        {handle("main_video", -1)}
                      </div>
                    );
                  }

                  if (entry.kind === "host") {
                    const rect = layout.host;
                    return (
                      <div
                        key="host"
                        onPointerDown={(event) => pointerDown(event, "host", -1, "move")}
                        className={`${frameClass("host", -1)} cursor-move`}
                        style={{
                          left: `${rect.x * 100}%`,
                          top: `${rect.y * 100}%`,
                          width: `${rect.w * 100}%`,
                          height: `${rect.h * 100}%`,
                        }}
                      >
                        {draft.host_asset && isVideo(draft.host_asset) ? (
                          <video
                            src={assetUrl(draft.preset_id, draft.host_asset)}
                            autoPlay
                            muted
                            loop
                            playsInline
                            className="h-full w-full object-contain"
                          />
                        ) : (
                          <div className="flex h-full w-full items-center justify-center border border-dashed border-emerald-700/60 bg-emerald-950/30 text-[10px] text-emerald-500/70">
                            host
                          </div>
                        )}
                        {layout.mask_corrections.map((point, index) => (
                          <i
                            key={index}
                            className={`pointer-events-none absolute h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 ${point.mode === "keep" ? "border-emerald-300 bg-emerald-400/40" : "border-rose-300 bg-rose-400/40"}`}
                            style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }}
                          />
                        ))}
                        {handle("host", -1)}
                      </div>
                    );
                  }

                  if (entry.kind === "logo" || entry.kind === "watermark") {
                    const rect = layout[entry.kind];
                    const source = brand?.[entry.kind] ? brandUrl(brandId, entry.kind) : "";
                    return (
                      <div
                        key={entry.kind}
                        onPointerDown={(event) => pointerDown(event, entry.kind, -1, "move")}
                        className={`${frameClass(entry.kind, -1)} cursor-move`}
                        style={{
                          left: `${rect.x * 100}%`,
                          top: `${rect.y * 100}%`,
                          width: `${rect.w * 100}%`,
                          height: `${rect.h * 100}%`,
                          opacity: brand ? brand[`${entry.kind}_opacity`] : 1,
                        }}
                      >
                        {source ? (
                          <img
                            src={source}
                            alt={entry.kind}
                            draggable="false"
                            className="h-full w-full object-contain"
                          />
                        ) : (
                          <div className="flex h-full w-full items-center justify-center border border-dashed border-amber-700/60 bg-amber-950/20 text-[10px] text-amber-500/70">
                            {entry.kind}
                          </div>
                        )}
                        {handle(entry.kind, -1)}
                      </div>
                    );
                  }

                  if (entry.kind === "text") {
                    const item = layout.text_layers[entry.index];
                    const value = item.source === "static" ? item.text : item.text || `{${item.source}}`;
                    return (
                      <div
                        key={`text-${entry.index}`}
                        onPointerDown={(event) => pointerDown(event, "text", entry.index, "move")}
                        className={`${frameClass("text", entry.index)} cursor-move`}
                        style={{
                          left: `${item.x * 100}%`,
                          top: `${item.y * 100}%`,
                          width: `${item.w * 100}%`,
                          textAlign: item.align,
                          color: PALETTE[item.color],
                          fontSize: `${Math.max(item.size * scale, 6)}px`,
                          lineHeight: item.line_spacing,
                        }}
                      >
                        <span className="font-black drop-shadow-[0_2px_2px_rgba(0,0,0,1)]">{value}</span>
                        {handle("text", entry.index)}
                      </div>
                    );
                  }

                  const subtitle = layout.subtitle;
                  return (
                    <div
                      key="subtitle"
                      className="pointer-events-none absolute"
                      style={{
                        left: `${subtitle.margin_ratio * 100}%`,
                        right: `${subtitle.margin_ratio * 100}%`,
                        top: `${subtitle.y * 100}%`,
                        textAlign: subtitle.align,
                        color: PALETTE[subtitle.color],
                        fontSize: `${Math.max(subtitle.size * scale, 6)}px`,
                        WebkitTextStroke: `${Math.max(subtitle.outline * scale, 0.5)}px ${PALETTE[subtitle.outline_color]}`,
                        paintOrder: "stroke fill",
                      }}
                    >
                      <span className="font-bold">Phụ đề mẫu hiển thị ở đây</span>
                    </div>
                  );
                })}

                {/* Blur regions stay last in the DOM so they remain grabbable,
                    but they belong to the source frame and are drawn inside
                    its contain-fit rather than against the canvas. */}
                {layout.blur_regions.map((region, index) => (
                  <div
                    key={`blur-${index}`}
                    onPointerDown={(event) => pointerDown(event, "blur", index, "move")}
                    className={`${frameClass("blur", index)} cursor-move border border-sky-300/40 bg-sky-200/10 backdrop-blur-xl`}
                    style={{
                      left: `${(fitted.x + region.x * fitted.w) * 100}%`,
                      top: `${(fitted.y + region.y * fitted.h) * 100}%`,
                      width: `${region.w * fitted.w * 100}%`,
                      height: `${region.h * fitted.h * 100}%`,
                    }}
                  >
                    <span className="pointer-events-none absolute left-0 top-0 bg-sky-500/70 px-1 text-[9px] text-white">
                      blur {index + 1}
                    </span>
                    {handle("blur", index)}
                  </div>
                ))}
              </div>
            </div>

            {matting?.state === "done" && (
              <div className="mt-4 grid gap-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-xs text-emerald-100 sm:grid-cols-[140px_1fr]">
                <img
                  src={`/api/preset-editor/assets?presetId=${encodeURIComponent(draft.preset_id)}&mattingJobId=${encodeURIComponent(matting.job_id)}`}
                  alt="RVM alpha preview"
                  className="aspect-video w-full rounded bg-[linear-gradient(45deg,#27272a_25%,transparent_25%),linear-gradient(-45deg,#27272a_25%,transparent_25%),linear-gradient(45deg,transparent_75%,#27272a_75%),linear-gradient(-45deg,transparent_75%,#27272a_75%)] [background-position:0_0,0_6px,6px_-6px,-6px_0px] [background-size:12px_12px] object-contain"
                />
                <p>
                  <span className="font-bold">RVM result:</span> {matting.result.frames} frames ·{" "}
                  {matting.result.provider}
                  <br />
                  <span className="text-emerald-300/80">Trắng = giữ host, đen = bỏ nền.</span>
                </p>
              </div>
            )}
          </section>

          <aside className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
            <section className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
              <p className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-zinc-400">
                <FiLayers /> Layers · trên xuống dưới
              </p>
              {stack.map((entry) => (
                <div
                  key={`${entry.kind}-${entry.index}`}
                  onClick={() => setSelected({ kind: entry.kind, index: entry.index })}
                  className={`mb-1 flex items-center gap-1 rounded border px-2 py-1.5 text-[11px] ${isSelected(entry.kind, entry.index) ? "border-violet-500 bg-violet-500/10 text-violet-100" : "border-zinc-800 text-zinc-300 hover:border-zinc-600"}`}
                >
                  <span className="w-6 font-mono text-[9px] text-zinc-500">{entry.z}</span>
                  <span className={`flex-1 truncate ${entry.visible ? "" : "line-through opacity-50"}`}>
                    {entry.label}
                  </span>
                  <button
                    onClick={(event) => {
                      event.stopPropagation();
                      toggleVisible(entry);
                    }}
                    className="p-1 text-zinc-400 hover:text-white"
                  >
                    {entry.visible ? <FiEye /> : <FiEyeOff />}
                  </button>
                  <button
                    onClick={(event) => {
                      event.stopPropagation();
                      setZ(entry, 1);
                    }}
                    className="p-1 text-zinc-400 hover:text-white"
                  >
                    <FiChevronUp />
                  </button>
                  <button
                    onClick={(event) => {
                      event.stopPropagation();
                      setZ(entry, -1);
                    }}
                    className="p-1 text-zinc-400 hover:text-white"
                  >
                    <FiChevronDown />
                  </button>
                </div>
              ))}
            </section>

            {selectedText ? (
              <section className="rounded-lg border border-violet-800/50 bg-zinc-950/70 p-3">
                <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-violet-300">
                  Text · {selectedText.id}
                </p>
                <label className="text-[10px] text-zinc-500">
                  Nguồn chữ
                  <select
                    value={selectedText.source}
                    onChange={(event) => patchText(selected.index, { source: event.target.value })}
                    className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-100 outline-none focus:border-violet-500"
                  >
                    {SOURCES.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <textarea
                  value={selectedText.text}
                  onChange={(event) => patchText(selected.index, { text: event.target.value })}
                  placeholder={selectedText.source === "static" ? "Chữ hiển thị" : "Chữ mẫu để canh khung"}
                  className="mt-2 h-14 w-full resize-none rounded border border-zinc-700 bg-zinc-900 p-2 text-xs outline-none focus:border-violet-500"
                />
                <div className="mt-2 grid grid-cols-3 gap-2">
                  <NumberField label="X" value={selectedText.x} min={0} max={1} step={0.005} onChange={(v) => patchText(selected.index, { x: v })} />
                  <NumberField label="Y" value={selectedText.y} min={0} max={1} step={0.005} onChange={(v) => patchText(selected.index, { y: v })} />
                  <NumberField label="W" value={selectedText.w} min={0.02} max={1} step={0.005} onChange={(v) => patchText(selected.index, { w: v })} />
                  <NumberField label="Size" value={selectedText.size} min={12} max={220} step={1} onChange={(v) => patchText(selected.index, { size: v })} />
                  <NumberField label="Chars/dòng" value={selectedText.max_chars_per_line} min={6} max={120} step={1} onChange={(v) => patchText(selected.index, { max_chars_per_line: v })} />
                  <NumberField label="Số dòng" value={selectedText.max_lines} min={1} max={6} step={1} onChange={(v) => patchText(selected.index, { max_lines: v })} />
                </div>
                <div className="mt-2 flex items-center justify-between gap-2">
                  <select
                    value={selectedText.align}
                    onChange={(event) => patchText(selected.index, { align: event.target.value })}
                    className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-[11px] text-zinc-100"
                  >
                    {["left", "center", "right"].map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                  <ColorPicker value={selectedText.color} onChange={(color) => patchText(selected.index, { color })} />
                </div>
                <button
                  onClick={() => {
                    setLayout((state) => ({
                      ...state,
                      text_layers: state.text_layers.filter((_, at) => at !== selected.index),
                    }));
                    setSelected({ kind: "main_video", index: -1 });
                  }}
                  className="mt-2 inline-flex items-center gap-1 text-[10px] text-rose-300 hover:text-rose-200"
                >
                  <FiTrash2 /> Xoá text layer
                </button>
              </section>
            ) : selectedBlur ? (
              <div>
                <RectInputs
                  label={`Blur ${selected.index + 1} · toạ độ theo khung hình gốc`}
                  value={selectedBlur}
                  onChange={(value) => writeRect("blur", selected.index, value)}
                />
                <button
                  onClick={() => {
                    setLayout((state) => ({
                      ...state,
                      blur_regions: state.blur_regions.filter((_, at) => at !== selected.index),
                    }));
                    setSelected({ kind: "main_video", index: -1 });
                  }}
                  className="mt-1 inline-flex items-center gap-1 text-[10px] text-rose-300 hover:text-rose-200"
                >
                  <FiTrash2 /> Xoá blur
                </button>
              </div>
            ) : selected.kind !== "subtitle" ? (
              <RectInputs
                label={selected.kind.replace("_", " ")}
                value={layout[selected.kind]}
                onChange={(value) => writeRect(selected.kind, -1, value)}
              />
            ) : null}

            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={addTextLayer}
                className="flex items-center justify-center gap-1 rounded-lg border border-zinc-700 py-2 text-[11px] font-bold text-zinc-300 hover:bg-zinc-800"
              >
                <FiType /> Text layer
              </button>
              <button
                onClick={() =>
                  setLayout((state) => ({
                    ...state,
                    blur_regions: [...state.blur_regions, { x: 0.05, y: 0.75, w: 0.9, h: 0.15 }],
                  }))
                }
                className="flex items-center justify-center gap-1 rounded-lg border border-zinc-700 py-2 text-[11px] font-bold text-zinc-300 hover:bg-zinc-800"
              >
                <FiPlus /> Blur band
              </button>
            </div>

            <section className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
              <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-400">Subtitle</p>
              <div className="grid grid-cols-3 gap-2">
                <NumberField label="Y" value={layout.subtitle.y} min={0} max={1} step={0.005} onChange={(v) => patchSubtitle({ y: v })} />
                <NumberField label="Size" value={layout.subtitle.size} min={12} max={160} step={1} onChange={(v) => patchSubtitle({ size: v })} />
                <NumberField label="Outline" value={layout.subtitle.outline} min={0} max={12} step={1} onChange={(v) => patchSubtitle({ outline: v })} />
                <NumberField label="Chars/dòng" value={layout.subtitle.max_chars_per_line} min={10} max={120} step={1} onChange={(v) => patchSubtitle({ max_chars_per_line: v })} />
                <NumberField label="Số dòng" value={layout.subtitle.max_lines} min={1} max={5} step={1} onChange={(v) => patchSubtitle({ max_lines: v })} />
                <NumberField label="Lề" value={layout.subtitle.margin_ratio} min={0} max={0.4} step={0.01} onChange={(v) => patchSubtitle({ margin_ratio: v })} />
              </div>
              <p className="mt-2 text-[10px] text-zinc-500">Màu chữ</p>
              <ColorPicker value={layout.subtitle.color} onChange={(color) => patchSubtitle({ color })} />
              <p className="mt-2 text-[10px] text-zinc-500">Màu viền</p>
              <ColorPicker
                value={layout.subtitle.outline_color}
                onChange={(color) => patchSubtitle({ outline_color: color })}
              />
            </section>

            <section className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
              <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-400">Intro</p>
              <label className="flex items-center gap-2 text-xs text-zinc-300">
                <input
                  type="checkbox"
                  checked={layout.intro_enabled}
                  onChange={(event) => setLayout((state) => ({ ...state, intro_enabled: event.target.checked }))}
                />
                Enable intro
              </label>
              <label className="mt-2 block text-[10px] text-zinc-500">
                Duration (s)
                <input
                  type="number"
                  min="0"
                  max="60"
                  value={layout.intro_duration_s}
                  onChange={(event) =>
                    setLayout((state) => ({ ...state, intro_duration_s: Number(event.target.value) }))
                  }
                  className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-white"
                />
              </label>
            </section>
          </aside>
        </div>
      </div>
    </main>
  );
}
