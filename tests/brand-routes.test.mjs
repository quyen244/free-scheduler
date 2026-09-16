/**
 * The browser side of the brand editor round trip.
 *
 * `/api/brands` is a proxy, so what can break here is the mapping: an action
 * reaching the wrong upstream path, a brand id that is not checked before it
 * becomes part of a URL, or an upstream error turned into a success. The render
 * service is stubbed for exactly that reason - its own rules are tested in
 * `automation/render-service/tests/test_brands.py`, against real files on disk.
 *
 *   node --test tests/
 */

import assert from "node:assert/strict";
import { register } from "node:module";
import { beforeEach, describe, it } from "node:test";

process.env.RENDER_SERVICE_URL = "http://render-service:8003";

// Must run before the route files are imported below; see next-resolve.mjs.
register("./next-resolve.mjs", import.meta.url);

const { GET, POST } = await import("../src/app/api/brands/route.js");
const assets = await import("../src/app/api/brands/assets/route.js");

/** Every upstream call the route made, newest last. */
let calls = [];

/** Answer the next upstream call with this status and body. */
function upstream(body, status = 200) {
  calls = [];
  globalThis.fetch = async (url, init = {}) => {
    // An upload is raw bytes, not JSON: record its size instead of parsing it.
    const raw = init.body instanceof ArrayBuffer;
    calls.push({
      url: String(url),
      method: init.method || "GET",
      body: init.body && !raw ? JSON.parse(init.body) : null,
      bytes: raw ? init.body.byteLength : null,
    });
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  };
}

const get = (query) => GET(new Request(`http://localhost/api/brands?${query}`));
const post = (payload) =>
  POST(
    new Request("http://localhost/api/brands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );

const draft = (overrides = {}) => ({
  schema_version: "brand.v1",
  brand_id: "an-so",
  display_name: "Ẩn Số",
  status: "draft",
  assets: [],
  vertical: { canvas: "vertical_9_16", layers: [] },
  landscape: { canvas: "landscape_16_9", layers: [] },
  ...overrides,
});

describe("/api/brands", () => {
  beforeEach(() => upstream({}));

  it("lists brands for the picker", async () => {
    upstream({ brands: [{ brand_id: "an-so", latest_revision: 1 }] });
    const response = await get("action=brands");
    assert.equal(response.status, 200);
    assert.equal(calls[0].url, "http://render-service:8003/brands");
    assert.equal((await response.json()).brands[0].brand_id, "an-so");
  });

  it("creates a brand, then opens the draft it created", async () => {
    upstream(draft(), 201);
    const created = await post({
      action: "create",
      brand_id: "an-so",
      display_name: "Ẩn Số",
    });
    assert.equal(created.status, 201);
    assert.equal(calls[0].method, "POST");
    assert.equal(calls[0].url, "http://render-service:8003/brands");
    assert.deepEqual(calls[0].body, { brand_id: "an-so", display_name: "Ẩn Số" });

    upstream(draft());
    const opened = await get("action=draft&brandId=an-so");
    assert.equal(calls[0].url, "http://render-service:8003/brands/an-so/draft");
    assert.equal((await opened.json()).brand_id, "an-so");
  });

  it("saves an edited draft back under the brand it belongs to", async () => {
    const edited = draft({
      vertical: {
        canvas: "vertical_9_16",
        layers: [
          { kind: "main_video", id: "video", x: 0, y: 0.3, w: 1, h: 0.4, z: 10 },
        ],
      },
    });
    upstream(edited);
    const response = await post({ action: "save-draft", ...edited });
    assert.equal(response.status, 200);
    assert.equal(calls[0].url, "http://render-service:8003/brands/an-so/draft");
    // The whole layout travels: a proxy that dropped a key would silently
    // delete layers the user had just placed.
    assert.deepEqual(calls[0].body, edited);
  });

  it("publishes the draft and reads back the revision it produced", async () => {
    upstream({ ...draft(), status: "published", revision: 2 });
    const published = await post({ action: "publish", ...draft() });
    assert.equal(calls[0].url, "http://render-service:8003/brands/an-so/publish");
    assert.equal((await published.json()).revision, 2);

    upstream({ ...draft(), status: "published", revision: 2 });
    await get("action=published&brandId=an-so&revision=2");
    assert.equal(calls[0].url, "http://render-service:8003/brands/an-so/revisions/2");
  });

  it("fetches the render config a published revision hands to the renderer", async () => {
    upstream({ images: [], video_z: 10 });
    await get("action=render-config&brandId=an-so&revision=2&aspect=landscape");
    assert.equal(
      calls[0].url,
      "http://render-service:8003/brands/an-so/render-config/2/landscape",
    );
  });

  it("asks the render service which revision is the latest", async () => {
    upstream({ brand_id: "an-so", revision: 3, content_sha256: "abc" });
    await get("action=latest&brandId=an-so");
    assert.equal(calls[0].url, "http://render-service:8003/brands/an-so/latest");
  });

  it("passes 'latest' through as a revision instead of resolving it here", async () => {
    // Resolution belongs to the one process that can see the files. A proxy
    // that guessed would be a second answer to the same question.
    upstream(draft());
    await get("action=published&brandId=an-so&revision=latest");
    assert.equal(calls[0].url, "http://render-service:8003/brands/an-so/revisions/latest");
    upstream({ images: [] });
    await get("action=render-config&brandId=an-so&revision=latest&aspect=vertical");
    assert.equal(
      calls[0].url,
      "http://render-service:8003/brands/an-so/render-config/latest/vertical",
    );
  });

  it("refuses a brand id that is not a brand id, before it reaches a URL", async () => {
    for (const bad of ["../etc", "An-So", "a", "an so"]) {
      upstream({});
      const read = await get(`action=draft&brandId=${encodeURIComponent(bad)}`);
      assert.equal(read.status, 400, `GET accepted ${bad}`);
      upstream({});
      const write = await post({ action: "save-draft", ...draft({ brand_id: bad }) });
      assert.equal(write.status, 400, `POST accepted ${bad}`);
      assert.equal(calls.length, 0, `${bad} still reached the render service`);
    }
  });

  it("passes an upstream rejection through instead of reporting success", async () => {
    upstream({ error: "a host layer needs an alpha mask" }, 400);
    const response = await post({ action: "publish", ...draft() });
    assert.equal(response.status, 400);
    assert.match((await response.json()).error, /alpha mask/);
  });

  it("reports an upstream crash as the crash it is, not as a dead service", async () => {
    // FastAPI answers an unhandled exception with plain text. Parsing that as
    // JSON throws, and the throw used to be caught as "unavailable" - which
    // says the service is down when it is up and rejecting the payload.
    calls = [];
    globalThis.fetch = async () =>
      new Response("Internal Server Error", {
        status: 500,
        headers: { "Content-Type": "text/plain" },
      });
    const response = await post({ action: "publish", ...draft() });
    assert.equal(response.status, 500);
    const { error } = await response.json();
    assert.match(error, /Internal Server Error/);
    assert.doesNotMatch(error, /unavailable/);
  });

  it("reports the render service being down as 503, not as a crash", async () => {
    calls = [];
    globalThis.fetch = async () => {
      throw new Error("connect ECONNREFUSED");
    };
    const response = await get("action=brands");
    assert.equal(response.status, 503);
    assert.match((await response.json()).error, /unavailable/);
  });

  it("rejects an unknown action", async () => {
    upstream({});
    assert.equal((await get("action=nonsense")).status, 400);
    assert.equal((await post({ action: "nonsense", brand_id: "an-so" })).status, 400);
    assert.equal(calls.length, 0);
  });
});

describe("/api/brands/assets", () => {
  const upload = (fields) => {
    const form = new FormData();
    for (const [key, value] of Object.entries(fields)) form.append(key, value);
    return assets.POST(
      new Request("http://localhost/api/brands/assets", { method: "POST", body: form }),
    );
  };

  it("hands the uploaded bytes on as a raw body, with the asset described in the query", async () => {
    upstream({ id: "logo", kind: "image", file: "logo.png" }, 201);
    const response = await upload({
      brandId: "an-so",
      assetId: "logo",
      role: "logo",
      file: new File([new Uint8Array([1, 2, 3])], "logo.png", { type: "image/png" }),
    });
    assert.equal(response.status, 201);
    assert.equal(
      calls[0].url,
      "http://render-service:8003/brands/an-so/assets?asset_id=logo&filename=logo.png&role=logo",
    );
    assert.equal(calls[0].bytes, 3);
    assert.equal(calls[0].method, "POST");
  });

  it("refuses an empty file and a bad asset id before uploading anything", async () => {
    upstream({});
    const empty = await upload({
      brandId: "an-so",
      assetId: "logo",
      file: new File([], "logo.png"),
    });
    assert.equal(empty.status, 400);

    upstream({});
    const badId = await upload({
      brandId: "an-so",
      assetId: "Logo Chinh",
      file: new File([new Uint8Array([1])], "logo.png"),
    });
    assert.equal(badId.status, 400);
    assert.equal(calls.length, 0);
  });

  it("serves a stored asset back to the preview with its own content type", async () => {
    calls = [];
    globalThis.fetch = async (url) => {
      calls.push({ url: String(url) });
      return new Response(new Uint8Array([137, 80, 78, 71]), { status: 200 });
    };
    const response = await assets.GET(
      new Request("http://localhost/api/brands/assets?brandId=an-so&file=logo.png"),
    );
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("Content-Type"), "image/png");
    assert.equal(calls[0].url, "http://render-service:8003/brands/an-so/assets/logo.png");
  });
});
