"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  FiChevronDown,
  FiChevronUp,
  FiDroplet,
  FiEye,
  FiEyeOff,
  FiFilm,
  FiImage,
  FiLayers,
  FiPlus,
  FiSave,
  FiTrash2,
  FiType,
  FiUpload,
  FiUploadCloud,
  FiUser,
} from "react-icons/fi";
import { PALETTE } from "@/lib/preset-editor";

// Canvas pixels per aspect, mirroring CANVAS_SIZE in
// automation/render-service/brands.py. A brand stores 0-1 coordinates only;
// these turn a font size or a rectangle into preview pixels the same way the
// renderer turns them into real ones.
const CANVAS = {
  vertical: { w: 1080, h: 1920, key: "vertical_9_16" },
  landscape: { w: 1920, h: 1080, key: "landscape_16_9" },
};

const COLORS = Object.keys(PALETTE);

const ROLES = [
  ["background", "Nền"],
  ["logo", "Logo"],
  ["watermark", "Watermark"],
  ["host", "Người dẫn (tách nền)"],
  ["mock_main", "Ảnh giả lập video chính"],
  ["other", "Khác"],
];

const TEXT_SOURCES = [
  ["static", "Chữ cố định"],
  ["title", "Tiêu đề video"],
  ["part", "Phần (số chunk)"],
];

const KIND_LABEL = {
  image: "Ảnh",
  main_video: "Video chính",
  blur: "Làm mờ",
  host: "Người dẫn",
  text: "Chữ",
  subtitle: "Phụ đề",
};

const KIND_ICON = {
  image: FiImage,
  main_video: FiFilm,
  blur: FiDroplet,
  host: FiUser,
  text: FiType,
  subtitle: FiType,
};

const clamp01 = (value) => Math.min(Math.max(Number(value) || 0, 0), 1);
const round3 = (value) => Math.round(value * 1000) / 1000;

/** A rectangle inside the canvas, whatever the layer kind. */
const rectOf = (layer) => ({
  x: Number(layer.x || 0),
  y: Number(layer.y || 0),
  w: Number(layer.w ?? 1),
  h: Number(layer.h ?? 1),
});

// A blur has no z: it is applied to the source pixels before the footage is
// placed, so there is no position in the stack for it to occupy.
const stacked = (layer) => layer.kind !== "blur";

function freshId(layers, stem) {
  const used = new Set(layers.map((layer) => layer.id));
  if (!used.has(stem)) return stem;
  for (let index = 2; index < 100; index += 1) {
    if (!used.has(`${stem}-${index}`)) return `${stem}-${index}`;
  }
  return `${stem}-${Date.now() % 1000}`;
}

/** Renumber z from list order, so "on top" in the list is on top in the render. */
function renumber(layers) {
  let z = 0;
  return layers.map((layer) => (stacked(layer) ? { ...layer, z: z++ } : layer));
}

const drawOrder = (layers) =>
  layers
    .filter(stacked)
    .slice()
    .sort((a, b) => (a.z ?? 0) - (b.z ?? 0));

export default function BrandEditorPage() {
  const [brands, setBrands] = useState([]);
  const [brandId, setBrandId] = useState("");
  const [brand, setBrand] = useState(null);
  const [aspect, setAspect] = useState("vertical");
  const [selectedId, setSelectedId] = useState(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const canvas = CANVAS[aspect];
  const layout = brand ? brand[aspect] : null;
  const layers = useMemo(() => layout?.layers || [], [layout]);
  const selected = layers.find((layer) => layer.id === selectedId) || null;
  const assets = useMemo(() => brand?.assets || [], [brand]);
  const assetById = useMemo(
    () => Object.fromEntries(assets.map((asset) => [asset.id, asset])),
    [assets],
  );

  const call = useCallback(async (url, init) => {
    const response = await fetch(url, init);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }, []);

  // The loaders below only fetch. Their callers decide what to do with the
  // answer, which keeps every setState off the synchronous path of an effect.
  const fetchBrands = useCallback(
    async () => (await call("/api/brands?action=brands")).brands || [],
    [call],
  );

  const refreshBrands = useCallback(
    () => fetchBrands().then(setBrands).catch(() => {}),
    [fetchBrands],
  );

  useEffect(() => {
    let cancelled = false;
    fetchBrands()
      .then((list) => {
        if (cancelled) return;
        setBrands(list);
        // The picker owns the selection from here on, so only fill an empty one.
        if (list.length) setBrandId((current) => current || list[0].brand_id);
      })
      .catch((problem) => {
        if (!cancelled) setError(problem.message);
      });
    return () => {
      cancelled = true;
    };
  }, [fetchBrands]);

  useEffect(() => {
    if (!brandId) return undefined;
    let cancelled = false;
    call(`/api/brands?action=draft&brandId=${encodeURIComponent(brandId)}`)
      .then((draft) => {
        if (cancelled) return;
        setError("");
        setBrand(draft);
        setSelectedId(null);
        setStatus(`Đã mở bản nháp của ${draft.display_name}`);
      })
      .catch((problem) => {
        if (!cancelled) setError(problem.message);
      });
    return () => {
      cancelled = true;
    };
  }, [brandId, call]);

  // -------------------------------------------------------------------------
  // editing
  // -------------------------------------------------------------------------

  const setLayers = useCallback(
    (next) => {
      setBrand((current) =>
        current
          ? {
              ...current,
              [aspect]: {
                ...current[aspect],
                layers: typeof next === "function" ? next(current[aspect].layers) : next,
              },
            }
          : current,
      );
    },
    [aspect],
  );

  const patchLayer = useCallback(
    (id, patch) => {
      setLayers((current) =>
        current.map((layer) => (layer.id === id ? { ...layer, ...patch } : layer)),
      );
    },
    [setLayers],
  );

  // The id is settled before the updater runs: React may call an updater more
  // than once, and picking a name in there would make the name depend on how
  // many times it did.
  const addLayer = useCallback(
    (layer) => {
      const id = freshId(layers, layer.id);
      setLayers((current) => [
        // Appended on top, which is where an operator expects a thing they just
        // added to appear rather than hidden behind what is already there.
        ...renumber([...drawOrder(current), { ...layer, id, z: 99 }]),
        ...current.filter((item) => !stacked(item)),
      ]);
      setSelectedId(id);
    },
    [layers, setLayers],
  );

  const removeLayer = useCallback(
    (id) => {
      setLayers((current) => renumber(current.filter((layer) => layer.id !== id)));
      setSelectedId((current) => (current === id ? null : current));
    },
    [setLayers],
  );

  const move = useCallback(
    (id, direction) => {
      setLayers((current) => {
        const ordered = drawOrder(current);
        const index = ordered.findIndex((layer) => layer.id === id);
        const target = index + direction;
        if (index < 0 || target < 0 || target >= ordered.length) return current;
        [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
        return [...renumber(ordered), ...current.filter((layer) => !stacked(layer))];
      });
    },
    [setLayers],
  );

  const hasKind = (kind) => layers.some((layer) => layer.kind === kind);

  const addAssetLayer = (asset) => {
    if (asset.role === "mock_main") {
      setError("Ảnh giả lập chỉ dùng để xem trước, không đặt được vào bố cục.");
      return;
    }
    if (asset.role === "host") {
      if (hasKind("host")) return setError("Bố cục này đã có một lớp người dẫn.");
      if (!asset.alpha_file) return setError("Hãy tách nền cho video người dẫn trước.");
      return addLayer({
        kind: "host",
        id: "host",
        asset: asset.id,
        x: 0.06,
        y: 0.43,
        w: 0.38,
        h: 0.46,
        z: 99,
        visible: true,
      });
    }
    const full = asset.role === "background";
    return addLayer({
      kind: "image",
      id: asset.id,
      asset: asset.id,
      x: full ? 0 : 0.04,
      y: full ? 0 : 0.04,
      w: full ? 1 : 0.2,
      h: full ? 1 : 0.12,
      z: 99,
      visible: true,
      opacity: 1,
      fit: full ? "fill" : "contain",
    });
  };

  const addMainVideo = () => {
    if (hasKind("main_video")) return setError("Bố cục này đã có video chính.");
    return addLayer({
      kind: "main_video",
      id: "video",
      x: 0,
      y: aspect === "vertical" ? 0.28 : 0,
      w: 1,
      h: aspect === "vertical" ? 0.44 : 1,
      z: 99,
      visible: true,
      fit: "contain",
    });
  };

  const addBlur = () => {
    if (!hasKind("main_video")) {
      return setError("Thêm video chính trước: vùng mờ được tính trên khung hình gốc.");
    }
    const id = freshId(layers, "blur");
    setLayers((current) => [
      ...current,
      { kind: "blur", id, x: 0.1, y: 0.8, w: 0.4, h: 0.12, visible: true },
    ]);
    setSelectedId(id);
    return undefined;
  };

  const addText = () =>
    addLayer({
      kind: "text",
      id: "text",
      source: "static",
      text: "Chữ mẫu",
      x: 0.06,
      y: 0.04,
      w: 0.88,
      size: 56,
      color: "white",
      align: "center",
      max_chars_per_line: 24,
      max_lines: 2,
      line_spacing: 1.15,
      z: 99,
      visible: true,
    });

  const addSubtitle = () => {
    if (hasKind("subtitle")) return setError("Bố cục này đã có phụ đề.");
    return addLayer({
      kind: "subtitle",
      id: "subtitle",
      y: aspect === "vertical" ? 0.755 : 0.88,
      size: aspect === "vertical" ? 46 : 42,
      color: "white",
      outline_color: "black",
      outline: 3,
      align: "center",
      margin_ratio: 0.06,
      max_chars_per_line: 32,
      max_lines: 2,
      z: 99,
      visible: true,
    });
  };

  // -------------------------------------------------------------------------
  // server actions
  // -------------------------------------------------------------------------

  const post = useCallback(
    async (body, label) => {
      setBusy(label);
      setError("");
      try {
        const payload = await call("/api/brands", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        return payload;
      } catch (problem) {
        setError(problem.message);
        return null;
      } finally {
        setBusy("");
      }
    },
    [call],
  );

  const createBrand = async () => {
    const id = window.prompt("Mã brand (chữ thường, số, gạch ngang):", "");
    if (!id) return;
    const name = window.prompt("Tên hiển thị:", id) || id;
    const created = await post(
      { action: "create", brand_id: id.trim(), display_name: name.trim() },
      "create",
    );
    if (!created) return;
    await refreshBrands();
    setBrandId(created.brand_id);
    setStatus(`Đã tạo brand ${created.display_name} — nền đen, chưa có lớp nào.`);
  };

  const saveDraft = async () => {
    if (!brand) return;
    const saved = await post({ action: "save-draft", ...brand }, "save");
    if (saved) {
      setBrand(saved);
      setStatus("Đã lưu bản nháp.");
    }
  };

  const publish = async () => {
    if (!brand) return;
    const published = await post({ action: "publish", ...brand }, "publish");
    if (published) {
      await refreshBrands();
      setStatus(
        `Đã xuất bản bản ${published.revision} (sha ${published.content_sha256.slice(0, 12)}).`,
      );
    }
  };

  const upload = async (file, role, assetId) => {
    if (!brand || !file) return;
    setBusy("upload");
    setError("");
    try {
      const form = new FormData();
      form.append("brandId", brand.brand_id);
      form.append("assetId", assetId);
      // "music" is a choice in the picker, not a layer role: the service
      // recognises audio by its extension and answers with its own record.
      form.append("role", role === "music" ? "other" : role);
      form.append("file", file);
      const stored = await call("/api/brands/assets", { method: "POST", body: form });

      if (stored.kind === "audio") {
        setBrand((current) => ({
          ...current,
          signature_music: { file: stored.file, gain_db: -18, duck_db: -12 },
        }));
        setStatus(`Đã tải nhạc hiệu ${stored.file}.`);
        return;
      }

      let record = stored;
      if (role === "host") {
        setBusy("matting");
        setStatus("Đang tách nền người dẫn (RVM)…");
        const matted = await call("/api/brands", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: "matting",
            brand_id: brand.brand_id,
            file: stored.file,
          }),
        });
        record = {
          ...stored,
          alpha_file: matted.alpha_file,
          alpha_revision: matted.alpha_revision,
        };
      }

      setBrand((current) => ({
        ...current,
        assets: [...current.assets.filter((item) => item.id !== record.id), record],
        ...(role === "mock_main" ? { mock_main_asset: record.id } : {}),
      }));
      setStatus(`Đã tải ${record.file}. Bấm “Đặt vào bố cục” để dùng.`);
    } catch (problem) {
      setError(problem.message);
    } finally {
      setBusy("");
    }
  };

  const removeAsset = (asset) => {
    const used = ["vertical", "landscape"].some((key) =>
      (brand[key]?.layers || []).some((layer) => layer.asset === asset.id),
    );
    if (used) {
      setError(`Tài sản ${asset.id} đang được một lớp sử dụng; xoá lớp đó trước.`);
      return;
    }
    setBrand((current) => ({
      ...current,
      assets: current.assets.filter((item) => item.id !== asset.id),
      mock_main_asset: current.mock_main_asset === asset.id ? null : current.mock_main_asset,
    }));
  };

  // -------------------------------------------------------------------------
  // render
  // -------------------------------------------------------------------------

  return (
    <div className="min-h-screen bg-bg-page text-primary-text">
      <header className="flex flex-wrap items-center gap-3 border-b border-divider px-5 py-3">
        <FiLayers className="text-primary" size={20} />
        <h1 className="text-lg font-semibold">Brand &amp; bố cục</h1>
        <select
          value={brandId}
          onChange={(event) => setBrandId(event.target.value)}
          className="rounded border border-divider bg-bg-card px-2 py-1.5 text-sm"
        >
          {!brands.length && <option value="">— chưa có brand —</option>}
          {brands.map((item) => (
            <option key={item.brand_id} value={item.brand_id}>
              {item.display_name}
              {item.latest_revision ? ` (v${item.latest_revision})` : " (nháp)"}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={createBrand}
          className="flex items-center gap-1.5 rounded border border-divider px-2.5 py-1.5 text-sm hover:bg-bg-card-hover"
        >
          <FiPlus size={14} /> Brand mới
        </button>

        <div className="ml-auto flex items-center gap-2">
          <div className="flex rounded border border-divider">
            {Object.keys(CANVAS).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => {
                  setAspect(key);
                  setSelectedId(null);
                }}
                className={`px-3 py-1.5 text-sm ${
                  aspect === key ? "bg-primary text-primary-btn-text" : "hover:bg-bg-card-hover"
                }`}
              >
                {key === "vertical" ? "9:16" : "16:9"}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={saveDraft}
            disabled={!brand || !!busy}
            className="flex items-center gap-1.5 rounded border border-divider px-3 py-1.5 text-sm hover:bg-bg-card-hover disabled:opacity-40"
          >
            <FiSave size={14} /> {busy === "save" ? "Đang lưu…" : "Lưu nháp"}
          </button>
          <button
            type="button"
            onClick={publish}
            disabled={!brand || !!busy}
            className="rounded bg-primary px-3 py-1.5 text-sm font-medium text-primary-btn-text hover:bg-primary-hover disabled:opacity-40"
          >
            {busy === "publish" ? "Đang xuất bản…" : "Xuất bản"}
          </button>
          <Link href="/" className="text-sm text-secondary-text hover:text-primary-text">
            Trang chủ
          </Link>
        </div>
      </header>

      {(error || status) && (
        <div
          className={`px-5 py-2 text-sm ${
            error ? "bg-red-950/60 text-red-200" : "bg-bg-card text-secondary-text"
          }`}
        >
          {error || status}
        </div>
      )}

      {!brand ? (
        <p className="p-8 text-secondary-text">
          Chọn một brand ở trên, hoặc tạo brand mới để bắt đầu từ nền đen.
        </p>
      ) : (
        <div className="grid gap-4 p-4 xl:grid-cols-[300px_minmax(0,1fr)_340px]">
          <AssetPanel
            brand={brand}
            busy={busy}
            onUpload={upload}
            onPlace={addAssetLayer}
            onRemove={removeAsset}
            onAddMainVideo={addMainVideo}
            onAddBlur={addBlur}
            onAddText={addText}
            onAddSubtitle={addSubtitle}
          />

          <Stage
            brand={brand}
            canvas={canvas}
            layers={layers}
            assetById={assetById}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onPatch={patchLayer}
          />

          <div className="flex flex-col gap-4">
            <LayerList
              layers={layers}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onMove={move}
              onToggle={(id, visible) => patchLayer(id, { visible })}
              onRemove={removeLayer}
            />
            <Properties
              layer={selected}
              assets={assets}
              canvas={canvas}
              onPatch={(patch) => selected && patchLayer(selected.id, patch)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// left: assets and the things that are not assets
// ---------------------------------------------------------------------------

function AssetPanel({
  brand,
  busy,
  onUpload,
  onPlace,
  onRemove,
  onAddMainVideo,
  onAddBlur,
  onAddText,
  onAddSubtitle,
}) {
  const [role, setRole] = useState("background");
  const [assetId, setAssetId] = useState("background");
  const input = useRef(null);

  const pick = (nextRole) => {
    setRole(nextRole);
    // The role is the obvious default name, and a second logo simply gets
    // edited to "logo-2" rather than forcing a naming decision up front.
    setAssetId(nextRole === "mock_main" ? "mock" : nextRole);
  };

  return (
    <aside className="flex flex-col gap-4">
      <section className="rounded-lg border border-divider bg-bg-card p-3">
        <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
          <FiUploadCloud size={15} /> Tải tài sản
        </h2>
        <label className="mb-2 block text-xs text-secondary-text">
          Loại
          <select
            value={role}
            onChange={(event) => pick(event.target.value)}
            className="mt-1 w-full rounded border border-divider bg-bg-elevated px-2 py-1.5 text-sm text-primary-text"
          >
            {ROLES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
            <option value="music">Nhạc hiệu</option>
          </select>
        </label>
        <label className="mb-2 block text-xs text-secondary-text">
          Mã tài sản
          <input
            value={assetId}
            onChange={(event) => setAssetId(event.target.value)}
            className="mt-1 w-full rounded border border-divider bg-bg-elevated px-2 py-1.5 text-sm text-primary-text"
          />
        </label>
        <input
          ref={input}
          type="file"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) onUpload(file, role, assetId.trim());
          }}
        />
        <button
          type="button"
          disabled={!!busy}
          onClick={() => input.current?.click()}
          className="flex w-full items-center justify-center gap-2 rounded bg-primary px-3 py-2 text-sm font-medium text-primary-btn-text hover:bg-primary-hover disabled:opacity-40"
        >
          <FiUpload size={14} />
          {busy === "upload" ? "Đang tải…" : busy === "matting" ? "Đang tách nền…" : "Chọn tệp"}
        </button>
        {role === "host" && (
          <p className="mt-2 text-xs text-secondary-text">
            Video người dẫn sẽ được tách nền bằng RVM ngay khi tải lên. Bước này mất vài phút.
          </p>
        )}
      </section>

      <section className="rounded-lg border border-divider bg-bg-card p-3">
        <h2 className="mb-2 text-sm font-semibold">Tài sản của brand</h2>
        {!brand.assets.length && (
          <p className="text-xs text-secondary-text">Chưa có tài sản nào.</p>
        )}
        <ul className="flex flex-col gap-2">
          {brand.assets.map((asset) => (
            <li
              key={asset.id}
              className="rounded border border-divider bg-bg-elevated px-2 py-1.5 text-xs"
            >
              <div className="flex items-center gap-2">
                <span className="font-medium text-primary-text">{asset.id}</span>
                <span className="text-secondary-text">{asset.role}</span>
                <button
                  type="button"
                  onClick={() => onRemove(asset)}
                  className="ml-auto text-secondary-text hover:text-red-400"
                  title="Gỡ khỏi brand"
                >
                  <FiTrash2 size={13} />
                </button>
              </div>
              <div className="truncate text-secondary-text">{asset.file}</div>
              {asset.role === "host" && !asset.alpha_file && (
                <div className="text-amber-400">chưa có mặt nạ alpha</div>
              )}
              <button
                type="button"
                onClick={() => onPlace(asset)}
                disabled={asset.role === "mock_main"}
                className="mt-1.5 w-full rounded border border-divider px-2 py-1 hover:bg-bg-card-hover disabled:opacity-40"
              >
                Đặt vào bố cục
              </button>
            </li>
          ))}
        </ul>
        {brand.signature_music && (
          <p className="mt-2 text-xs text-secondary-text">
            Nhạc hiệu: {brand.signature_music.file}
          </p>
        )}
      </section>

      <section className="rounded-lg border border-divider bg-bg-card p-3">
        <h2 className="mb-2 text-sm font-semibold">Lớp không cần tệp</h2>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <PanelButton icon={FiFilm} label="Video chính" onClick={onAddMainVideo} />
          <PanelButton icon={FiDroplet} label="Vùng mờ" onClick={onAddBlur} />
          <PanelButton icon={FiType} label="Chữ" onClick={onAddText} />
          <PanelButton icon={FiType} label="Phụ đề" onClick={onAddSubtitle} />
        </div>
      </section>
    </aside>
  );
}

function PanelButton({ icon: Icon, label, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center justify-center gap-1.5 rounded border border-divider px-2 py-2 hover:bg-bg-card-hover"
    >
      <Icon size={13} /> {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// centre: the canvas
// ---------------------------------------------------------------------------

function Stage({ brand, canvas, layers, assetById, selectedId, onSelect, onPatch }) {
  const frame = useRef(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const element = frame.current;
    if (!element) return undefined;
    const observe = new ResizeObserver(() => {
      const box = element.getBoundingClientRect();
      setSize({ w: box.width, h: box.height });
    });
    observe.observe(element);
    return () => observe.disconnect();
  }, []);

  const scale = size.w ? size.w / canvas.w : 0;
  // Capping both ratios at one width would leave the 16:9 canvas a strip too
  // short to drag a layer inside, so cap them at the same height instead.
  const stageMaxWidth = (canvas.w / canvas.h) * 750;
  const mockAsset = brand.mock_main_asset ? assetById[brand.mock_main_asset] : null;
  const video = layers.find((layer) => layer.kind === "main_video");

  const drag = (layer, mode) => (event) => {
    event.preventDefault();
    event.stopPropagation();
    onSelect(layer.id);
    const box = frame.current.getBoundingClientRect();
    const startX = event.clientX;
    const startY = event.clientY;
    const start = rectOf(layer);
    // The blur is normalised against the source frame, which the preview draws
    // inside the footage rectangle, so its pointer maths uses that rectangle.
    const host = layer.kind === "blur" && video ? rectOf(video) : { x: 0, y: 0, w: 1, h: 1 };
    const spanX = box.width * host.w;
    const spanY = box.height * host.h;

    const onMove = (moveEvent) => {
      const dx = (moveEvent.clientX - startX) / spanX;
      const dy = (moveEvent.clientY - startY) / spanY;
      // Only the fields the layer's schema actually has: a subtitle is a
      // baseline with no x, a text layer has no height, and sending either one
      // a key it does not define is rejected at save time, not here.
      if (mode === "move" && layer.kind === "subtitle") {
        onPatch(layer.id, { y: round3(clamp01(Number(layer.y ?? 0.75) + dy)) });
      } else if (mode === "move") {
        onPatch(layer.id, {
          x: round3(clamp01(Math.min(start.x + dx, 1 - start.w))),
          y: round3(clamp01(Math.min(start.y + dy, 1 - start.h))),
        });
      } else if (layer.kind === "text") {
        onPatch(layer.id, { w: round3(Math.min(Math.max(start.w + dx, 0.05), 1 - start.x)) });
      } else {
        onPatch(layer.id, {
          w: round3(Math.min(Math.max(start.w + dx, 0.02), 1 - start.x)),
          h: round3(Math.min(Math.max(start.h + dy, 0.02), 1 - start.y)),
        });
      }
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  return (
    <div className="flex flex-col items-center gap-2">
      <div
        className="relative w-full overflow-hidden rounded-lg border border-divider bg-black"
        style={{ aspectRatio: `${canvas.w} / ${canvas.h}`, maxWidth: stageMaxWidth }}
        ref={frame}
        onPointerDown={() => onSelect(null)}
      >
        {drawOrder(layers).map((layer) =>
          layer.visible === false ? null : (
            <LayerBox
              key={layer.id}
              layer={layer}
              canvas={canvas}
              scale={scale}
              brandId={brand.brand_id}
              asset={layer.asset ? assetById[layer.asset] : null}
              mockAsset={mockAsset}
              selected={layer.id === selectedId}
              onDrag={drag}
            />
          ),
        )}

        {/* Blur boxes sit on top of everything in the preview: they are an
            editing aid, not a layer, and hiding them under the artwork would
            make them impossible to grab. */}
        {layers
          .filter((layer) => layer.kind === "blur" && layer.visible !== false)
          .map((layer) => {
            const host = video ? rectOf(video) : { x: 0, y: 0, w: 1, h: 1 };
            const rect = rectOf(layer);
            return (
              <div
                key={layer.id}
                onPointerDown={drag(layer, "move")}
                className={`absolute cursor-move border-2 border-dashed bg-sky-400/20 ${
                  layer.id === selectedId ? "border-sky-300" : "border-sky-500/70"
                }`}
                style={{
                  left: `${(host.x + rect.x * host.w) * 100}%`,
                  top: `${(host.y + rect.y * host.h) * 100}%`,
                  width: `${rect.w * host.w * 100}%`,
                  height: `${rect.h * host.h * 100}%`,
                }}
              >
                <span className="absolute left-0.5 top-0.5 text-[10px] text-sky-200">
                  {layer.id}
                </span>
                <span
                  onPointerDown={drag(layer, "resize")}
                  className="absolute -bottom-1 -right-1 h-3 w-3 cursor-se-resize bg-sky-300"
                />
              </div>
            );
          })}
      </div>
      <p className="text-xs text-secondary-text">
        {canvas.w}×{canvas.h} · kéo để di chuyển, kéo góc dưới phải để đổi kích thước
      </p>
    </div>
  );
}

function LayerBox({ layer, canvas, scale, brandId, asset, mockAsset, selected, onDrag }) {
  const rect = rectOf(layer);
  const src = (file) =>
    `/api/brands/assets?brandId=${encodeURIComponent(brandId)}&file=${encodeURIComponent(file)}`;

  // A subtitle has no rectangle of its own: it is a baseline and a margin, so
  // the preview draws the band the renderer will draw into.
  if (layer.kind === "subtitle") {
    const margin = Number(layer.margin_ratio ?? 0.06);
    return (
      <div
        onPointerDown={onDrag(layer, "move")}
        className={`absolute cursor-move ${selected ? "outline outline-1 outline-primary" : ""}`}
        style={{
          left: `${margin * 100}%`,
          top: `${Number(layer.y ?? 0.75) * 100}%`,
          width: `${(1 - margin * 2) * 100}%`,
        }}
      >
        <span
          className="block"
          style={{
            fontSize: `${Number(layer.size ?? 46) * scale}px`,
            color: PALETTE[layer.color] || "#FFFFFF",
            textAlign: layer.align || "center",
            WebkitTextStroke: `${Number(layer.outline ?? 3) * scale}px ${
              PALETTE[layer.outline_color] || "#000000"
            }`,
            paintOrder: "stroke fill",
            lineHeight: 1.15,
          }}
        >
          Phụ đề mẫu hiển thị ở đây
        </span>
      </div>
    );
  }

  if (layer.kind === "text") {
    return (
      <div
        onPointerDown={onDrag(layer, "move")}
        className={`absolute cursor-move ${selected ? "outline outline-1 outline-primary" : ""}`}
        style={{
          left: `${rect.x * 100}%`,
          top: `${rect.y * 100}%`,
          width: `${rect.w * 100}%`,
        }}
      >
        <span
          className="block whitespace-pre-wrap"
          style={{
            fontSize: `${Number(layer.size ?? 48) * scale}px`,
            color: PALETTE[layer.color] || "#FFFFFF",
            textAlign: layer.align || "center",
            lineHeight: Number(layer.line_spacing ?? 1.15),
            WebkitTextStroke: `${Math.max(Number(layer.size ?? 48) / 16, 1) * scale}px #000`,
            paintOrder: "stroke fill",
          }}
        >
          {layer.source === "static" ? layer.text || "" : `«${layer.source}»`}
        </span>
      </div>
    );
  }

  const frameStyle = {
    left: `${rect.x * 100}%`,
    top: `${rect.y * 100}%`,
    width: `${rect.w * 100}%`,
    height: `${rect.h * 100}%`,
  };

  let inner = null;
  if (layer.kind === "image" && asset) {
    inner = (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={src(asset.file)}
        alt={layer.id}
        draggable={false}
        className="h-full w-full"
        style={{
          objectFit: layer.fit === "fill" ? "fill" : "contain",
          opacity: Number(layer.opacity ?? 1),
        }}
      />
    );
  } else if (layer.kind === "host" && asset) {
    inner = (
      <video
        src={src(asset.file)}
        muted
        loop
        autoPlay
        playsInline
        className="h-full w-full object-contain"
      />
    );
  } else if (layer.kind === "main_video") {
    inner = mockAsset ? (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={src(mockAsset.file)}
        alt="giả lập video chính"
        draggable={false}
        className="h-full w-full"
        style={{ objectFit: layer.fit === "fill" ? "fill" : "contain" }}
      />
    ) : (
      <div className="flex h-full w-full items-center justify-center bg-zinc-700/70 text-xs text-zinc-300">
        VIDEO CHÍNH
      </div>
    );
  }

  return (
    <div
      onPointerDown={onDrag(layer, "move")}
      className={`absolute cursor-move ${
        selected ? "outline outline-2 outline-primary" : "outline outline-1 outline-white/15"
      }`}
      style={frameStyle}
    >
      {inner}
      {selected && (
        <span
          onPointerDown={onDrag(layer, "resize")}
          className="absolute -bottom-1.5 -right-1.5 h-3 w-3 cursor-se-resize bg-primary"
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// right: the stack and one layer's numbers
// ---------------------------------------------------------------------------

function LayerList({ layers, selectedId, onSelect, onMove, onToggle, onRemove }) {
  const ordered = drawOrder(layers).reverse();
  const blurs = layers.filter((layer) => layer.kind === "blur");

  return (
    <section className="rounded-lg border border-divider bg-bg-card p-3">
      <h2 className="mb-2 text-sm font-semibold">Lớp (trên xuống dưới)</h2>
      {!ordered.length && !blurs.length && (
        <p className="text-xs text-secondary-text">
          Nền đen, chưa có lớp nào. Tải một tài sản rồi bấm “Đặt vào bố cục”.
        </p>
      )}
      <ul className="flex flex-col gap-1">
        {[...ordered, ...blurs].map((layer) => {
          const Icon = KIND_ICON[layer.kind] || FiLayers;
          const isBlur = layer.kind === "blur";
          return (
            <li
              key={layer.id}
              onClick={() => onSelect(layer.id)}
              className={`flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs ${
                layer.id === selectedId ? "bg-primary/25" : "hover:bg-bg-card-hover"
              }`}
            >
              <Icon size={13} className="shrink-0 text-secondary-text" />
              <span className="truncate font-medium">{layer.id}</span>
              <span className="shrink-0 text-secondary-text">{KIND_LABEL[layer.kind]}</span>
              <span className="ml-auto flex shrink-0 items-center gap-0.5">
                {!isBlur && (
                  <>
                    <IconButton title="Lên trên" onClick={() => onMove(layer.id, 1)}>
                      <FiChevronUp size={13} />
                    </IconButton>
                    <IconButton title="Xuống dưới" onClick={() => onMove(layer.id, -1)}>
                      <FiChevronDown size={13} />
                    </IconButton>
                  </>
                )}
                <IconButton
                  title={layer.visible === false ? "Hiện" : "Ẩn"}
                  onClick={() => onToggle(layer.id, layer.visible === false)}
                >
                  {layer.visible === false ? <FiEyeOff size={13} /> : <FiEye size={13} />}
                </IconButton>
                <IconButton title="Xoá" onClick={() => onRemove(layer.id)}>
                  <FiTrash2 size={13} />
                </IconButton>
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function IconButton({ title, onClick, children }) {
  return (
    <button
      type="button"
      title={title}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      className="rounded p-1 text-secondary-text hover:bg-bg-elevated hover:text-primary-text"
    >
      {children}
    </button>
  );
}

function Field({ label, value, step = 0.01, min, max, onChange }) {
  return (
    <label className="block text-xs text-secondary-text">
      {label}
      <input
        type="number"
        step={step}
        min={min}
        max={max}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value === "" ? 0 : Number(event.target.value))}
        className="mt-1 w-full rounded border border-divider bg-bg-elevated px-2 py-1 text-sm text-primary-text"
      />
    </label>
  );
}

function Choice({ label, value, options, onChange }) {
  return (
    <label className="block text-xs text-secondary-text">
      {label}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded border border-divider bg-bg-elevated px-2 py-1 text-sm text-primary-text"
      >
        {options.map(([option, text]) => (
          <option key={option} value={option}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

function Properties({ layer, assets, canvas, onPatch }) {
  if (!layer) {
    return (
      <section className="rounded-lg border border-divider bg-bg-card p-3">
        <p className="text-xs text-secondary-text">Chọn một lớp để chỉnh.</p>
      </section>
    );
  }

  const rect = rectOf(layer);
  const hasRect = layer.kind !== "subtitle";
  const pixels = (value, axis) =>
    Math.round((axis === "x" ? canvas.w : canvas.h) * Number(value || 0));

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-divider bg-bg-card p-3">
      <h2 className="text-sm font-semibold">
        {KIND_LABEL[layer.kind]} · {layer.id}
      </h2>

      {hasRect && (
        <>
          <div className="grid grid-cols-2 gap-2">
            <Field label="X" value={rect.x} onChange={(value) => onPatch({ x: clamp01(value) })} />
            <Field label="Y" value={rect.y} onChange={(value) => onPatch({ y: clamp01(value) })} />
            <Field
              label="Rộng"
              value={rect.w}
              onChange={(value) => onPatch({ w: clamp01(value) })}
            />
            {layer.kind !== "text" && (
              <Field
                label="Cao"
                value={rect.h}
                onChange={(value) => onPatch({ h: clamp01(value) })}
              />
            )}
          </div>
          <p className="text-[11px] text-secondary-text">
            ≈ {pixels(rect.x, "x")}, {pixels(rect.y, "y")} px · {pixels(rect.w, "x")}×
            {pixels(rect.h, "y")} px
            {layer.kind === "blur" && " (tính trên khung hình gốc)"}
          </p>
        </>
      )}

      {layer.kind === "image" && (
        <>
          <Choice
            label="Tài sản"
            value={layer.asset || ""}
            options={assets
              .filter((asset) => asset.kind === "image" && asset.role !== "mock_main")
              .map((asset) => [asset.id, `${asset.id} — ${asset.file}`])}
            onChange={(value) => onPatch({ asset: value })}
          />
          <Choice
            label="Cách vừa khung"
            value={layer.fit || "contain"}
            options={[
              ["contain", "Giữ tỉ lệ (contain)"],
              ["fill", "Kéo đầy khung (fill)"],
            ]}
            onChange={(value) => onPatch({ fit: value })}
          />
          <Field
            label="Độ mờ (0–1)"
            value={layer.opacity ?? 1}
            min={0}
            max={1}
            onChange={(value) => onPatch({ opacity: Math.min(Math.max(value, 0), 1) })}
          />
        </>
      )}

      {layer.kind === "main_video" && (
        <Choice
          label="Cách vừa khung"
          value={layer.fit || "contain"}
          options={[
            ["contain", "Giữ tỉ lệ (contain)"],
            ["fill", "Kéo đầy khung (fill)"],
          ]}
          onChange={(value) => onPatch({ fit: value })}
        />
      )}

      {layer.kind === "text" && (
        <>
          <Choice
            label="Nguồn chữ"
            value={layer.source || "static"}
            options={TEXT_SOURCES}
            onChange={(value) => onPatch({ source: value })}
          />
          {layer.source === "static" && (
            <label className="block text-xs text-secondary-text">
              Nội dung
              <textarea
                rows={2}
                value={layer.text || ""}
                onChange={(event) => onPatch({ text: event.target.value })}
                className="mt-1 w-full rounded border border-divider bg-bg-elevated px-2 py-1 text-sm text-primary-text"
              />
            </label>
          )}
          <div className="grid grid-cols-2 gap-2">
            <Field
              label="Cỡ chữ (px)"
              step={1}
              value={layer.size ?? 48}
              onChange={(value) => onPatch({ size: Math.max(Math.round(value), 8) })}
            />
            <Field
              label="Số dòng tối đa"
              step={1}
              value={layer.max_lines ?? 2}
              onChange={(value) => onPatch({ max_lines: Math.max(Math.round(value), 1) })}
            />
            <Field
              label="Ký tự mỗi dòng"
              step={1}
              value={layer.max_chars_per_line ?? 24}
              onChange={(value) =>
                onPatch({ max_chars_per_line: Math.max(Math.round(value), 6) })
              }
            />
            <Field
              label="Giãn dòng"
              value={layer.line_spacing ?? 1.15}
              onChange={(value) => onPatch({ line_spacing: value })}
            />
          </div>
          <ColorChoice value={layer.color} onChange={(value) => onPatch({ color: value })} />
          <Choice
            label="Canh lề"
            value={layer.align || "center"}
            options={[
              ["left", "Trái"],
              ["center", "Giữa"],
              ["right", "Phải"],
            ]}
            onChange={(value) => onPatch({ align: value })}
          />
        </>
      )}

      {layer.kind === "subtitle" && (
        <>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Y" value={layer.y ?? 0.755} onChange={(value) => onPatch({ y: clamp01(value) })} />
            <Field
              label="Cỡ chữ (px)"
              step={1}
              value={layer.size ?? 46}
              onChange={(value) => onPatch({ size: Math.max(Math.round(value), 8) })}
            />
            <Field
              label="Viền (px)"
              step={1}
              value={layer.outline ?? 3}
              onChange={(value) => onPatch({ outline: Math.max(Math.round(value), 0) })}
            />
            <Field
              label="Lề hai bên"
              value={layer.margin_ratio ?? 0.06}
              onChange={(value) => onPatch({ margin_ratio: clamp01(value) })}
            />
          </div>
          <ColorChoice value={layer.color} onChange={(value) => onPatch({ color: value })} />
          <ColorChoice
            label="Màu viền"
            value={layer.outline_color}
            onChange={(value) => onPatch({ outline_color: value })}
          />
        </>
      )}

      {layer.kind === "host" && (
        <p className="text-xs text-secondary-text">
          Người dẫn dùng mặt nạ alpha đã tách sẵn của brand này.
        </p>
      )}
    </section>
  );
}

function ColorChoice({ label = "Màu chữ", value, onChange }) {
  return (
    <div className="text-xs text-secondary-text">
      {label}
      <div className="mt-1 flex flex-wrap gap-1.5">
        {COLORS.map((name) => (
          <button
            key={name}
            type="button"
            title={name}
            onClick={() => onChange(name)}
            style={{ background: PALETTE[name] }}
            className={`h-6 w-6 rounded border ${
              value === name ? "border-primary ring-2 ring-primary" : "border-divider"
            }`}
          />
        ))}
      </div>
    </div>
  );
}
