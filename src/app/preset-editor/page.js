"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { FiDownload, FiImage, FiLoader, FiMove, FiPlus, FiSave, FiScissors, FiUpload, FiVideo } from "react-icons/fi";

const initialRect = (x, y, w, h) => ({ x, y, w, h });
const freshLayout = (canvas) => ({
  canvas,
  background_asset: null,
  host_asset: null,
  host: initialRect(canvas === "vertical_9_16" ? 0.06 : 0.67, canvas === "vertical_9_16" ? 0.43 : 0.28, canvas === "vertical_9_16" ? 0.38 : 0.26, canvas === "vertical_9_16" ? 0.46 : 0.62),
  logo_asset: null,
  logo: initialRect(0.04, 0.04, 0.16, 0.1),
  watermark_asset: null,
  watermark: initialRect(0.8, 0.04, 0.16, 0.1),
  blur_regions: [],
  caption: { text: "", y: 0.82, size: 42, color: "#FFFFFF" },
  mask_corrections: [],
  intro_enabled: true,
  intro_duration_s: 8,
});

const newDraft = (presetId = "an-so") => ({
  schema_version: 1,
  preset_id: presetId,
  name: "Ẩn Số",
  status: "draft",
  vertical: freshLayout("vertical_9_16"),
  landscape: freshLayout("landscape_16_9"),
});

const assetUrl = (presetId, asset) => asset ? `/api/preset-editor/assets?presetId=${encodeURIComponent(presetId)}&asset=${encodeURIComponent(asset)}` : "";
const isVideo = (asset) => /\.(mp4|mov|webm)$/i.test(asset || "");
const clamp = (number, min, max) => Math.max(min, Math.min(max, number));

async function api(action, payload = {}, method = "POST") {
  const url = method === "GET" ? `/api/preset-editor?${new URLSearchParams({ action, ...payload })}` : "/api/preset-editor";
  const response = await fetch(url, method === "GET" ? { cache: "no-store" } : {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...payload }),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Editor request failed");
  return body;
}

function RectInputs({ label, value, onChange }) {
  const fields = ["x", "y", "w", "h"];
  return (
    <section className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
      <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-400">{label}</p>
      <div className="grid grid-cols-4 gap-2">
        {fields.map((field) => (
          <label key={field} className="text-[10px] text-zinc-500">
            {field.toUpperCase()}
            <input
              value={Number(value[field]).toFixed(2)}
              type="number" step="0.01" min="0" max="1"
              onChange={(event) => onChange({ ...value, [field]: Number(event.target.value) })}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-xs text-zinc-100 outline-none focus:border-violet-500"
            />
          </label>
        ))}
      </div>
    </section>
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
  const [drag, setDrag] = useState(null);
  const [brushMode, setBrushMode] = useState(null);
  const fileInput = useRef(null);
  const uploadRole = useRef(null);
  const canvas = useRef(null);

  const layout = draft[aspect];
  const ratioClass = aspect === "vertical" ? "aspect-[9/16] max-h-[680px]" : "aspect-video";
  const title = aspect === "vertical" ? "9:16 · Facebook / TikTok" : "16:9 · YouTube";

  useEffect(() => {
    api("rvm", {}, "GET").then(setRvm).catch((error) => setMessage(error.message));
    // The shipped local asset set is intentionally a draft. It gives the
    // operator a useful starting point, yet must still be explicitly saved and
    // published before n8n can select it.
    api("draft", { presetId: "an-so" }, "GET")
      .then((existing) => {
        setDraft(existing);
        setMessage("Đã mở draft an-so local. Chỉnh sửa rồi Save draft / Publish revision.");
      })
      .catch(() => {});
  }, []);

  const imageStyle = (asset) => asset && !isVideo(asset) ? { backgroundImage: `url("${assetUrl(draft.preset_id, asset)}")` } : {};
  const setLayout = (next) => setDraft((current) => ({ ...current, [aspect]: next }));
  const changeRect = (key, rect) => setLayout({ ...layout, [key]: rect });
  const setAsset = (role, asset) => {
    if (role === "host") {
      setDraft((current) => ({
        ...current,
        vertical: { ...current.vertical, host_asset: asset },
        landscape: { ...current.landscape, host_asset: asset },
      }));
      return;
    }
    setLayout({ ...layout, [`${role}_asset`]: asset });
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
      if (state.state === "queued" || state.state === "running") window.setTimeout(() => pollMatting(jobId), 1200);
      if (state.state === "done") setMessage("RVM đã tạo alpha mask. Kiểm tra preview trắng/đen bên dưới.");
      if (state.state === "failed") setMessage(state.error || "RVM thất bại.");
    } catch (error) {
      setMessage(error.message);
    }
  };

  const startMatting = async () => {
    if (!layout.host_asset) {
      setMessage("Hãy upload host MP4/MOV trước.");
      return;
    }
    setBusy(true);
    try {
      const accepted = await api("matting", {
        preset_id: draft.preset_id,
        host_asset: layout.host_asset,
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

  const pointerDown = (event, layer) => {
    if (brushMode || layer === "canvas") return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const rect = layout[layer];
    setDrag({ layer, startX: event.clientX, startY: event.clientY, rect });
  };

  const pointerMove = (event) => {
    if (!drag || !canvas.current) return;
    const box = canvas.current.getBoundingClientRect();
    const dx = (event.clientX - drag.startX) / box.width;
    const dy = (event.clientY - drag.startY) / box.height;
    const next = { ...drag.rect, x: clamp(drag.rect.x + dx, 0, 1 - drag.rect.w), y: clamp(drag.rect.y + dy, 0, 1 - drag.rect.h) };
    changeRect(drag.layer, next);
  };

  const paintCorrection = (event) => {
    if (!brushMode || !canvas.current) return;
    const box = canvas.current.getBoundingClientRect();
    const x = (event.clientX - box.left) / box.width;
    const y = (event.clientY - box.top) / box.height;
    const host = layout.host;
    if (x < host.x || x > host.x + host.w || y < host.y || y > host.y + host.h) {
      setMessage("Brush phải được đặt trên host.");
      return;
    }
    const correction = { x: (x - host.x) / host.w, y: (y - host.y) / host.h, radius: 0.035, mode: brushMode };
    setLayout({ ...layout, mask_corrections: [...layout.mask_corrections, correction] });
    setMessage("Đã thêm correction. Chạy RVM lại để áp dụng vào alpha mask.");
  };

  const visualLayers = useMemo(() => [
    ["host", layout.host_asset, layout.host],
    ["logo", layout.logo_asset, layout.logo],
    ["watermark", layout.watermark_asset, layout.watermark],
  ], [layout]);

  return (
    <main className="min-h-0 flex-1 overflow-auto bg-zinc-950 px-4 py-6 text-zinc-100 sm:px-6">
      <input ref={fileInput} onChange={upload} className="hidden" type="file" accept="video/mp4,video/quicktime,video/webm,image/png,image/jpeg,image/webp" />
      <div className="mx-auto max-w-[1500px]">
        <div className="mb-5 flex flex-col gap-3 border-b border-zinc-800 pb-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-violet-400">Local visual preset</p>
            <h1 className="mt-1 text-2xl font-black tracking-tight">Visual editor · Ẩn Số</h1>
            <p className="mt-1 max-w-2xl text-xs leading-relaxed text-zinc-400">Draft → validate → published revision. FFmpeg vẫn là renderer cuối; n8n chỉ nhận preset ID + revision đã publish.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href="/" className="rounded-lg border border-zinc-700 px-3 py-2 text-xs font-bold text-zinc-300 hover:bg-zinc-900">Workspace</Link>
            <button onClick={saveDraft} disabled={busy} className="inline-flex items-center gap-2 rounded-lg border border-violet-500/50 bg-violet-500/10 px-3 py-2 text-xs font-bold text-violet-200 hover:bg-violet-500/20 disabled:opacity-50"><FiSave /> Save draft</button>
            <button onClick={publish} disabled={busy} className="rounded-lg bg-violet-600 px-3 py-2 text-xs font-bold text-white hover:bg-violet-500 disabled:opacity-50">Publish revision</button>
            <button onClick={exportPreset} disabled={!published} className="inline-flex items-center gap-2 rounded-lg border border-zinc-700 px-3 py-2 text-xs font-bold text-zinc-200 hover:bg-zinc-900 disabled:opacity-40"><FiDownload /> Export JSON</button>
          </div>
        </div>

        <div className="mb-5 rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-3 text-xs text-zinc-300">
          <span className="mr-2 font-bold text-violet-300">Status</span>{message}
          {rvm && <span className={`ml-3 rounded px-2 py-1 text-[10px] font-bold ${rvm.ready ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"}`}>RVM {rvm.ready ? "ready" : "model missing"}</span>}
        </div>

        <div className="grid gap-5 xl:grid-cols-[310px_minmax(0,1fr)_320px]">
          <aside className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
            <div>
              <label className="text-[10px] font-bold uppercase tracking-wider text-zinc-500">Preset ID</label>
              <input value={draft.preset_id} onChange={(event) => setDraft({ ...draft, preset_id: event.target.value.toLowerCase() })} className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none focus:border-violet-500" />
              <label className="mt-3 block text-[10px] font-bold uppercase tracking-wider text-zinc-500">Name</label>
              <input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none focus:border-violet-500" />
            </div>
            <div className="border-t border-zinc-800 pt-4">
              <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-500">Assets</p>
              {[['host', 'Host video', FiVideo], ['background', 'Background', FiImage], ['logo', 'Logo', FiImage], ['watermark', 'Watermark', FiImage]].map(([role, label, Icon]) => (
                <button key={role} onClick={() => triggerUpload(role)} disabled={busy} className="mb-2 flex w-full items-center justify-between rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-left text-xs hover:border-violet-500/70 disabled:opacity-50">
                  <span className="flex items-center gap-2 text-zinc-200"><Icon className="text-violet-300" />{label}</span><FiUpload className="text-zinc-500" />
                </button>
              ))}
              <p className="mt-2 break-words text-[10px] leading-relaxed text-zinc-500">Host: {layout.host_asset || "chưa có"}<br />Background: {layout.background_asset || "chưa có"}<br />Logo: {layout.logo_asset || "chưa có"}<br />Watermark: {layout.watermark_asset || "chưa có"}</p>
            </div>
            <div className="border-t border-zinc-800 pt-4">
              <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-500">Aspect</p>
              <div className="grid grid-cols-2 gap-2">
                {[['vertical', '9:16'], ['landscape', '16:9']].map(([value, label]) => <button key={value} onClick={() => setAspect(value)} className={`rounded-lg px-2 py-2 text-xs font-bold ${aspect === value ? "bg-violet-600 text-white" : "border border-zinc-700 text-zinc-400 hover:bg-zinc-800"}`}>{label}</button>)}
              </div>
              <p className="mt-2 text-[10px] text-zinc-500">Hai layout độc lập. Đổi ratio không ghi đè vị trí ratio kia.</p>
            </div>
            <div className="border-t border-zinc-800 pt-4">
              <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-500">Host mask · RVM</p>
              <button onClick={startMatting} disabled={busy || !layout.host_asset} className="flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-40"><FiScissors /> {matting?.state === "running" ? "Đang segment…" : "Segment host"}</button>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <button onClick={() => setBrushMode(brushMode === "keep" ? null : "keep")} className={`rounded border px-2 py-1.5 text-[10px] font-bold ${brushMode === "keep" ? "border-emerald-400 bg-emerald-500/20 text-emerald-200" : "border-zinc-700 text-zinc-400"}`}>+ Keep brush</button>
                <button onClick={() => setBrushMode(brushMode === "remove" ? null : "remove")} className={`rounded border px-2 py-1.5 text-[10px] font-bold ${brushMode === "remove" ? "border-rose-400 bg-rose-500/20 text-rose-200" : "border-zinc-700 text-zinc-400"}`}>− Remove brush</button>
              </div>
              <button onClick={() => setLayout({ ...layout, mask_corrections: [] })} className="mt-2 text-[10px] text-zinc-500 underline hover:text-zinc-300">Clear {layout.mask_corrections.length} correction(s)</button>
              <p className="mt-2 text-[10px] leading-relaxed text-zinc-500">Brush áp cho mọi frame; dùng cho host đứng tương đối yên. Sau khi brush, bấm Segment host lại.</p>
            </div>
          </aside>

          <section className="min-w-0 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
            <div className="mb-3 flex items-center justify-between"><div><h2 className="text-sm font-bold">Preview · {title}</h2><p className="text-[10px] text-zinc-500">Kéo layer trực tiếp; toạ độ lưu dạng 0–1.</p></div><span className="rounded bg-zinc-800 px-2 py-1 text-[10px] text-zinc-400"><FiMove className="mr-1 inline" />drag</span></div>
            <div className="flex min-h-[620px] items-center justify-center overflow-auto rounded-lg bg-[radial-gradient(#27272a_1px,transparent_1px)] [background-size:12px_12px] p-4">
              <div ref={canvas} onPointerMove={pointerMove} onPointerUp={() => setDrag(null)} onClick={paintCorrection} className={`relative w-full max-w-[760px] overflow-hidden bg-zinc-950 shadow-2xl ${ratioClass} ${brushMode ? "cursor-crosshair" : ""}`} style={imageStyle(layout.background_asset)}>
                {!layout.background_asset && <div className="absolute inset-0 bg-gradient-to-br from-indigo-950 via-zinc-950 to-violet-950" />}
                {layout.blur_regions.map((region, index) => <div key={index} className="absolute border border-white/10 bg-white/10 backdrop-blur-xl" style={{ left: `${region.x * 100}%`, top: `${region.y * 100}%`, width: `${region.w * 100}%`, height: `${region.h * 100}%` }} />)}
                {visualLayers.map(([kind, asset, rect]) => asset && <div key={kind} onPointerDown={(event) => pointerDown(event, kind)} className={`absolute group ${kind === "host" ? "cursor-move" : "cursor-move"}`} style={{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%` }}>
                  {kind === "host" && isVideo(asset) ? <video src={assetUrl(draft.preset_id, asset)} autoPlay muted loop playsInline className="h-full w-full object-contain" /> : <img src={assetUrl(draft.preset_id, asset)} alt={kind} draggable="false" className="h-full w-full object-contain" />}
                  <span className="pointer-events-none absolute -top-5 left-0 hidden rounded bg-black/70 px-1 text-[9px] uppercase text-white group-hover:block">{kind}</span>
                  <div className="pointer-events-none absolute inset-0 border border-violet-400/0 group-hover:border-violet-400" />
                  {kind === "host" && layout.mask_corrections.map((point, index) => <i key={index} className={`pointer-events-none absolute h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 ${point.mode === "keep" ? "border-emerald-300 bg-emerald-400/40" : "border-rose-300 bg-rose-400/40"}`} style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }} />)}
                </div>)}
                {layout.caption.text && <div className="pointer-events-none absolute left-[8%] right-[8%] text-center font-black drop-shadow-[0_2px_2px_rgba(0,0,0,1)]" style={{ top: `${layout.caption.y * 100}%`, fontSize: `${Math.max(12, layout.caption.size / (aspect === "vertical" ? 2 : 1.5))}px`, color: layout.caption.color }}>{layout.caption.text}</div>}
              </div>
            </div>
            {matting?.state === "done" && <div className="mt-4 grid gap-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-xs text-emerald-100 sm:grid-cols-[140px_1fr]"><img src={`/api/preset-editor/assets?presetId=${encodeURIComponent(draft.preset_id)}&mattingJobId=${encodeURIComponent(matting.job_id)}`} alt="RVM alpha preview" className="aspect-video w-full rounded bg-[linear-gradient(45deg,#27272a_25%,transparent_25%),linear-gradient(-45deg,#27272a_25%,transparent_25%),linear-gradient(45deg,transparent_75%,#27272a_75%),linear-gradient(-45deg,transparent_75%,#27272a_75%)] [background-position:0_0,0_6px,6px_-6px,-6px_0px] [background-size:12px_12px] object-contain" /><p><span className="font-bold">RVM result:</span> {matting.result.frames} frames · {matting.result.provider} · alpha mask đã sẵn sàng để FFmpeg ghép với host.<br /><span className="text-emerald-300/80">Trắng = giữ host, đen = bỏ nền.</span></p></div>}
          </section>

          <aside className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
            <h2 className="text-sm font-bold">{title} controls</h2>
            <RectInputs label="Host" value={layout.host} onChange={(value) => changeRect("host", value)} />
            <RectInputs label="Logo" value={layout.logo} onChange={(value) => changeRect("logo", value)} />
            <RectInputs label="Watermark" value={layout.watermark} onChange={(value) => changeRect("watermark", value)} />
            <section className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
              <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-400">Caption</p>
              <textarea value={layout.caption.text} onChange={(event) => setLayout({ ...layout, caption: { ...layout.caption, text: event.target.value } })} placeholder="Title / caption preview" className="h-16 w-full resize-none rounded border border-zinc-700 bg-zinc-900 p-2 text-xs outline-none focus:border-violet-500" />
              <div className="mt-2 grid grid-cols-3 gap-2">
                {[['y', 'Y', 0, 1, 0.01], ['size', 'Size', 12, 160, 1]].map(([key, label, min, max, step]) => <label key={key} className="text-[10px] text-zinc-500">{label}<input type="number" min={min} max={max} step={step} value={layout.caption[key]} onChange={(event) => setLayout({ ...layout, caption: { ...layout.caption, [key]: Number(event.target.value) } })} className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-1 py-1 text-xs text-white" /></label>)}
                <label className="text-[10px] text-zinc-500">Color<input type="color" value={layout.caption.color} onChange={(event) => setLayout({ ...layout, caption: { ...layout.caption, color: event.target.value } })} className="mt-1 h-7 w-full rounded border border-zinc-700 bg-zinc-900" /></label>
              </div>
            </section>
            <section className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
              <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-zinc-400">Intro</p>
              <label className="flex items-center gap-2 text-xs text-zinc-300"><input type="checkbox" checked={layout.intro_enabled} onChange={(event) => setLayout({ ...layout, intro_enabled: event.target.checked })} /> Enable intro</label>
              <label className="mt-2 block text-[10px] text-zinc-500">Duration (s)<input type="number" min="0" max="60" value={layout.intro_duration_s} onChange={(event) => setLayout({ ...layout, intro_duration_s: Number(event.target.value) })} className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-white" /></label>
            </section>
            <button onClick={() => setLayout({ ...layout, blur_regions: [...layout.blur_regions, initialRect(0.05, 0.7, 0.9, 0.15)] })} className="flex w-full items-center justify-center gap-2 rounded-lg border border-zinc-700 py-2 text-xs font-bold text-zinc-300 hover:bg-zinc-800"><FiPlus /> Add blur band</button>
            {layout.blur_regions.map((region, index) => <div key={index}><RectInputs label={`Blur ${index + 1}`} value={region} onChange={(value) => setLayout({ ...layout, blur_regions: layout.blur_regions.map((item, itemIndex) => itemIndex === index ? value : item) })} /><button onClick={() => setLayout({ ...layout, blur_regions: layout.blur_regions.filter((_, itemIndex) => itemIndex !== index) })} className="mt-1 text-[10px] text-rose-300">Remove blur</button></div>)}
          </aside>
        </div>
      </div>
    </main>
  );
}
