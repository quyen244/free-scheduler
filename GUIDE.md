# GUIDE — things that are easy to get wrong

Short operational notes. Facts that cost time to re-derive, written down the
first time someone had to work them out. Not a spec: see `features/` for that.

## Signature music

**The music is not an asset.** It does *not* need an entry in the brand's
`assets` array. `brands.py` resolves it straight from `signature_music.file`
through `asset_file(brand_id, name)`, `_validate_files_exist` checks that one
path on its own, and `render.py` passes it to ffmpeg as an extra input. Adding
an `assets` row for the mp3 does nothing.

**The draft and the published revision are separate objects.** `publish()`
takes a payload, not the draft file, so a revision can be published carrying a
field the on-disk draft has never had. This already happened once: `an-so` v11
was published with `signature_music` while `config.json` still had
`signature_music: null`, which meant the next publish from the visual editor
would have silently dropped the music. **After changing a brand, check that the
draft and the newest `vN.json` agree.**

**`revision: latest` in `render-plan.yaml` does resolve.** The submit script
reports what it picked — `"brand_revisions": {"an-so": 11}`. If music seems
missing, that resolution is not the place to look.

### How to tell whether music actually reached the output

Do not judge by ear alone; `volume_db` is easy to set below the threshold of
attention. Measure it. In voice-silent windows the rendered audio should be the
mp3 attenuated by exactly `volume_db`:

```bash
docker compose exec -T render-service python - <<'PY'
import subprocess, numpy as np, soundfile as sf
subprocess.run(['ffmpeg','-v','error','-y','-i','<render>.mp4','-ac','1','-ar','48000','-f','wav','/tmp/out.wav'],check=True)
subprocess.run(['ffmpeg','-v','error','-y','-i','<music>.mp3','-ac','1','-ar','48000','-f','wav','/tmp/music.wav'],check=True)
out,sr=sf.read('/tmp/out.wav'); voc,_=sf.read('<voice>.wav'); mus,_=sf.read('/tmp/music.wav')
win=int(sr*0.5); n=min(len(out),len(voc)); f=n//win
vv=np.sqrt((voc[:f*win].reshape(f,win)**2).mean(1))
for i in np.where(vv<1e-4)[0][:5]:
    s=i*win; seg=out[s:s+win]; ref=np.take(mus,np.arange(s,s+win)%len(mus))
    print('%.1fs  %.1f dB' % (s/sr, 20*np.log10(seg.std()/ref.std())))
PY
```

A delta that sits on `volume_db` ±1 dB **is** the music. Waveform correlation
is the wrong test — AAC and loop phase drag it down to ~0.3 even when the bed
is unquestionably there.

Reference measurement, `hS3VXBeEv0I` @ an-so v11, `volume_db: -24.0`: music
−42.5 dBFS, speech −22.5 dBFS. Present, correct, and **20 dB under the
narration** — quiet enough to be mistaken for absent.

### `volume_db` is not a percentage

`volume_db` is absolute gain on the mp3, not a ratio against the voice, so the
same number sounds different under a loud narrator and a quiet one. To set the
bed as a share of the narration, measure the **active** speech level first —
100 ms frames above an RMS of `1e-3`, so the pauses do not drag the average
down — and solve:

```
volume_db = 20 * log10(pct/100 * speech_rms / music_rms)
```

For `hS3VXBeEv0I` (active speech −19.1 dBFS, this mp3 −17.5 dBFS):

| share of narration | `volume_db` |
| --- | --- |
| 5 % | −27.7 |
| 10 % | −21.6 |
| 15 % | −18.1 |
| 20 % | −15.6 |
| 30 % | −12.1 |
| **45 % (chosen)** | **−7.9** |

**Solve it open-loop, then close the loop.** The formula lands about 0.7 dB
low here, so finish by measuring: build a clip, take the speech-to-gap delta in
voice-silent windows, and correct the gain by the error until the delta reaches
`-20*log10(pct/100)` — 6.94 dB for 45 %. Two iterations was enough.

Two traps in that measurement: mask the clip against the **same window** of
`voice.wav` it was cut from, not against the whole file, and remember the delta
is only meaningful where the narrator is silent.

The old `-24.0` was about **8 %** by this measure. For `an-so`, the confirmed
setting is **a flat 45 % bed**: `volume_db: -7.9`, with `ducking.ratio: 1.0`.
At a ratio of 1.0, `sidechaincompress` has unity gain, so it does not dip the
bed while the host speaks. The 20-second rendered check measured 44 % during
speech and 45 % in pauses; the 1 % difference is the final limiter, not
ducking. Listenable variants at each of the five levels are at
`automation/data/hS3VXBeEv0I/previews/music-test/`, cut from the busiest
20 seconds of the render (t=470 s, 83 % speech, 7 speech→silence transitions).
They are built through the same
`_music_graph` chain the renderer uses, with the music seeked to `t mod 200 s`
so the loop sits where it really would.

## Layout: the editor box is not the video

`preset.resolve()` fits the source **inside** the layer rect preserving the
source's aspect ratio, then centres it. The rect is a *slot*, not the output
size. When the slot's aspect differs from the source's, the rendered video is
smaller than the box drawn in the visual editor.

Worked example — `an-so` landscape, `video` layer
`x=0.1 y=0.166 w=0.831 h=0.62`, 1920x1080 canvas, 16:9 source:

| | x | y | w | h | aspect |
| --- | --- | --- | --- | --- | --- |
| Slot (what the editor draws) | 192 | 179 | 1595 | 669 | 2.38:1 |
| Video (what is rendered) | 394 | 180 | 1192 | 670 | 1.78:1 |

202 px of background shows down each side. Confirmed against a real frame:
brightness steps at exactly x=394, x=1586, y=180, y=850.

**The visual editor now draws both** (fixed 2026-09-17): the dashed rectangle
is the slot and stays the thing you drag, the picture inside it is the real
video area, and the caption under the canvas prints its pixel size. It fits
against the mock main-video asset's own shape when the brand has one, and
otherwise against the **Tỉ lệ nguồn** dropdown, which defaults to 16:9. That
dropdown is preview-only state — it is never saved to a brand, because the
renderer reads the shape from the footage itself and a stored guess could
disagree with it.

`fit` accepts only `contain` and `fill` — there is no crop-to-fill. `fill`
stretches and will distort faces.

**`video_fit` is dead.** `brands.py` writes it into the render config and
nothing reads it: `preset.resolve()` always fits. The editor no longer offers a
fit control for the main video, because it did nothing. Image layers *do*
honour `fit`.

## The renderer draws no title

`RENDER_FROZEN_TEXT` (render-service) lists text bindings or layer ids the
renderer refuses to draw. It **defaults to `title`**: a re-up carries its title
in the platform post, so burning one into the frame duplicates it.

- Brands keep their title layer. Freezing is a rendering decision, so nothing
  about the layout changes and no revision is needed either way.
- To draw titles again: `RENDER_FROZEN_TEXT=` (empty) and restart the service.
  **Empty is not the same as unset** — unset takes the default, `title`.
- A frozen layer appends a warning to the asset, so a missing title is
  explicable from `media-manifest.json` instead of looking like a fault.
- It matches on either the layer's `source` binding or its `id`, because a
  title can be a bound field or a static string.

## Measuring a rendered frame

```bash
docker compose exec -T render-service ffmpeg -v error -y -ss <seconds> \
  -i <render>.mp4 -frames:v 1 /data/<video_id>/previews/frames/<name>.png
```

Then find edges by column/row brightness rather than by eye — a busy background
makes eyeballing unreliable, and a screenshot of a player window is usually
cropped and not the render's real aspect ratio.

## Tests

`test_render.py`, `test_subs.py` and `test_timing.py` share a fixture that
downloads a clip through `media-service`. With that container down they produce
38 errors that look alarming and mean nothing. Either start `media-service` or
read past them; `-m no_pipeline` selects the tests that do not need it.
