# Task: Prototype “Ẩn Số” Vertical Video Intro + Main Content Layout

You are working on:

`/home/quyen/projects/Free-AI-Social-Media-Scheduler`

This task is for a Vietnamese mystery-content channel named:

**ẨN SỐ**

The channel focuses on:
- mysterious disappearances
- unexplained real events
- strange true stories
- unresolved mysteries
- unusual scientific or historical phenomena

This is a PROTOTYPE / VISUAL-APPROVAL task.

Do NOT immediately rewrite or fully integrate the production pipeline.

The goal is:

1. inspect existing assets
2. clean Gemini watermarks from image assets
3. create a static MAIN CONTENT layout prototype
4. create an INTRODUCTION preview
5. clearly demonstrate that INTRO and MAIN CONTENT use different layouts
6. visually inspect all outputs
7. write a report for user approval

If any important requirement is unclear, ASK ME instead of inventing a major design decision.

---

# 0. IMPORTANT — WORK IN STAGES

Follow this order:

1. Inspect all relevant assets.
2. Clean Gemini watermarks from IMAGE assets only.
3. Create MAIN CONTENT static 9:16 layout using a still image.
4. Export and visually inspect it.
5. Generate introduction narration from current title + part + AI summary.
6. Mute original host audio.
7. Generate TTS.
8. Create INTRODUCTION preview.
9. Export representative frames.
10. Visually inspect everything.
11. Write a Markdown report.

Do NOT run a full source video through the real production pipeline just to test layout.

Workflow principle:

**prototype → inspect → user approval → later integrate**

---

# 1. SOURCE ASSETS

Preset folder:

`/home/quyen/projects/Free-AI-Social-Media-Scheduler/automation/data/presets/ẩn số`

Important files:

Host video:

`/home/quyen/projects/Free-AI-Social-Media-Scheduler/automation/data/presets/ẩn số/ẩn-số-host.mp4`

Intro background / first image:

`/home/quyen/projects/Free-AI-Social-Media-Scheduler/automation/data/presets/ẩn số/Ẩn-Số-First.png`

Static placeholder that MUST be used as fake MAIN VIDEO content:

`/home/quyen/projects/Free-AI-Social-Media-Scheduler/reports/image.png`

First inspect the preset folder and identify:

- background
- logo
- watermark
- host
- intro image
- any Gemini-generated image
- any branding assets
- any decorative assets

Do not assume filenames beyond paths explicitly provided above.

---

# 2. GEMINI IMAGE WATERMARK CLEANUP

Some IMAGE assets inside:

`automation/data/presets/ẩn số`

may contain Gemini-generated watermarks.

## Requirement

Remove or visually conceal Gemini watermarks from IMAGE assets only.

Do NOT modify videos in this step.

Prefer the least destructive method:

- local crop only if composition is unaffected
- localized blur
- localized reconstruction
- inpainting if a suitable local tool already exists
- cover/rebuild using nearby texture

Do NOT blur the entire image.

Do NOT overwrite originals immediately.

Create cleaned copies first.

## Evidence

Store before/after artifacts under:

`artifacts/an-so/watermark-cleanup/`

Examples:

- `before-first.png`
- `after-first.png`
- `before-background.png`
- `after-background.png`

Visually verify:
- watermark is no longer distracting
- no obvious ugly blur patch
- important content remains intact

---

# 3. TARGET FORMAT

All prototype visuals should target:

**1080 × 1920**
**9:16 vertical**

Intended platforms:

- YouTube Shorts
- Facebook Reels
- TikTok

The result should feel like a repeatable visual identity for the **Ẩn Số** channel.

---

# 4. IMPORTANT: THERE ARE TWO DIFFERENT LAYOUT STATES

There are TWO intentionally different visual states:

1. **INTRODUCTION**
2. **MAIN CONTENT**

Do NOT use the same layout for both.

This distinction is critical.

---

# 5. INTRODUCTION LAYOUT

The INTRODUCTION does NOT use blurred extensions.

## Intro rules

During the introduction:

- NO upper blurred extension
- NO lower blurred extension
- use cleaned:

`/home/quyen/projects/Free-AI-Social-Media-Scheduler/automation/data/presets/ẩn số/Ẩn-Số-First.png`

as the MAIN VISUAL area
- host is CENTERED
- host introduces the episode
- original host audio is muted
- TTS narration is used

The intro should feel like a branded opening / presenter scene.

Conceptually:

┌─────────────────────────────┐
│            ẨN SỐ            │
│ Những câu chuyện chưa...    │
│                             │
│         VIDEO TITLE         │
│          PHẦN 1/3           │
│                             │
│ ┌─────────────────────────┐ │
│ │                         │ │
│ │    Ẩn-Số-First.png      │ │
│ │                         │ │
│ │      HOST CENTERED      │ │
│ │                         │ │
│ │       SUBTITLE          │ │
│ │                         │ │
│ └─────────────────────────┘ │
│                             │
└─────────────────────────────┘

This diagram describes hierarchy, not exact pixel coordinates.

---

# 6. INTRODUCTION HOST POSITION

During INTRODUCTION:

- host must be centered
- host is the main presenter
- host should appear integrated with `Ẩn-Số-First.png`
- do NOT move host to bottom-left during intro
- do NOT create blur bands behind host
- do NOT turn host into a small picture-in-picture box

The scene should feel like:

> “The host is introducing the episode.”

If the host source contains its own background, inspect it first before deciding whether background removal is needed.

Do NOT assume chroma/background removal is required.

---

# 7. INTRODUCTION BRANDING

Keep:

- `ẨN SỐ`
- tagline
- translated video title
- part number
- logo
- watermark
- subtitles if needed

Because there is NO upper blurred band during intro, place logo/watermark in a balanced way that does not cover the host or title.

Prefer one of these depending on existing assets:

- logo near upper-left of main visual area
- watermark near upper-right of main visual area

or another equivalent position if visually better.

Do NOT place branding randomly.

Do NOT let logo/watermark dominate the host.

---

# 8. MAIN CONTENT LAYOUT

After intro ends, switch to MAIN CONTENT layout.

This layout DOES use blurred extensions.

Use:

`/home/quyen/projects/Free-AI-Social-Media-Scheduler/reports/image.png`

as the fake/sharp MAIN VIDEO placeholder for the static prototype.

Conceptually:

┌─────────────────────────────┐
│            ẨN SỐ            │
│ Những câu chuyện chưa...    │
│                             │
│         VIDEO TITLE         │
│          PHẦN 1/3           │
│                             │
│ ╔═════════════════════════╗ │
│ ║ LOGO   UPPER BLUR   WM  ║ │
│ ║                         ║ │
│ ║      MAIN VIDEO         ║ │
│ ║                         ║ │
│ ║ HOST                    ║ │
│ ║ ███                     ║ │
│ ║ ███   LOWER BLUR        ║ │
│ ║ ███                     ║ │
│ ╚═════════════════════════╝ │
│                             │
└─────────────────────────────┘

---

# 9. MAIN CONTENT BLURRED EXTENSIONS

Create TWO contextual blur bands:

1. upper blurred extension
2. lower blurred extension

These are NOT black bars.

They must look like blurred extensions derived from the main video/image.

Recommended process:

1. take current main frame/image
2. duplicate it
3. scale/crop to fill width
4. apply strong blur
5. slightly darken if needed
6. use as upper blur
7. use as lower blur
8. keep original frame sharp in the middle

Desired effect:

> sharp content in center, blurred contextual continuation above and below

---

# 10. BLUR SIZE

If 1920px vertical height is conceptually divided into 10 sections:

Each blur band should be around:

**1.5–2 / 10**

roughly:

**288–384 px**

Do NOT hardcode blindly.

Adjust based on visual balance.

---

# 11. LOGO + WATERMARK IN MAIN CONTENT

This is REQUIRED.

Inside the UPPER BLURRED EXTENSION:

- LOGO goes on the LEFT
- WATERMARK goes on the RIGHT

Conceptually:

┌───────────────────────────────┐
│ LOGO   UPPER BLUR      WM     │
└───────────────────────────────┘

Rules:

## Logo
- left side
- vertically centered in upper blur
- more prominent than watermark
- not oversized

## Watermark
- right side
- vertically centered
- smaller/subtler than logo

Both should look integrated into the blur band.

They must NOT make the blur band look like a toolbar.

If contrast is weak, use:
- local darkening
- subtle shadow
- low-opacity backing

Avoid large opaque boxes.

---

# 12. MAIN CONTENT HOST POSITION

This is a REQUIRED composition rule.

During MAIN CONTENT:

- host moves to the BOTTOM-LEFT
- host should overlap the boundary between:
  - sharp main video
  - lower blurred extension

Conceptually:

┌───────────────────────────────┐
│        MAIN VIDEO             │
│                               │
│                               │
│ HOST                          │
│ ███                           │
│ ███      LOWER BLUR           │
│ ███                           │
└───────────────────────────────┘

The host is NOT fully inside the sharp video.

The host should partially overlap the lower blurred band.

The lower blurred band acts like a visual floor/background behind the host.

Rules:

- anchor host bottom-left
- lower body overlaps lower blur
- host may slightly overlap sharp video above
- do NOT center host during main content
- do NOT place host in top area
- do NOT cover title
- do NOT cover part indicator
- do NOT cover logo/watermark

If needed:
- subtle shadow
- soft outline
- local darkening behind host

Do NOT use a rectangular webcam box.

The host should feel like a presenter layered over the footage.

---

# 13. TITLE AREA

The top text area should include:

## Brand

`ẨN SỐ`

## Tagline

Suggested:

`Những câu chuyện chưa có lời giải`

## Video title

Use the translated title of the current video.

IMPORTANT:

Do NOT use the AI-generated introduction paragraph as title.

Use the actual translated VIDEO TITLE.

## Part

Examples:

`PHẦN 1`

or

`PHẦN 1/3`

Pick one consistent format.

Recommended hierarchy:

1. VIDEO TITLE
2. PART
3. BRAND / TAGLINE

---

# 14. SUBTITLE SAFE ZONE

Because MAIN CONTENT host occupies bottom-left:

- do NOT place subtitles behind host
- do NOT place subtitles over host
- prefer center or center-right
- subtitles may sit slightly above lower blur
- preferably max 2 lines
- must remain readable on mobile
- must avoid app UI near bottom edge

During INTRO, host is centered, so adapt subtitle placement accordingly.

Do not use one rigid subtitle position for both states if it causes overlap.

---

# 15. INTRODUCTION CONTENT SOURCE

The upstream AI may return text similar to:

> "10 bí ẩn có thật mà khoa học vẫn chưa thể giải thích trọn vẹn—từ vụ mất tích trong rừng đến nước nặng bất thường ngoài Hệ Mặt Trời. Từ rừng Angeles đến đại dương sâu và không gian liên sao: những manh mối thực tế đang đặt ra câu hỏi lớn cho khoa học."

DO NOT read this verbatim.

Treat it only as source material.

Rewrite it into natural spoken Vietnamese for the **Ẩn Số** host.

---

# 16. INTRODUCTION STRUCTURE

Use this structure:

## A. Signature opening

Example:

> “Chào mừng bạn đến với Ẩn Số — nơi chúng ta cùng lần theo những câu chuyện vẫn còn bỏ ngỏ.”

Keep it concise.

---

## B. Curiosity hook

Lead with an intriguing question or contrast.

Example:

> “Một người có thể biến mất giữa khu rừng mà gần như không để lại bất kỳ dấu vết nào không?”

or:

> “Có những câu hỏi con người đã theo đuổi hàng chục năm, nhưng càng tìm hiểu, lời giải dường như càng xa hơn.”

Do not reveal the answer.

---

## C. Episode/topic introduction

Example:

> “Trong số hôm nay, chúng ta sẽ khám phá 10 bí ẩn có thật mà đến nay khoa học vẫn chưa thể giải thích trọn vẹn.”

Avoid repetitive wording such as:
- video hôm nay
- nội dung hôm nay
- chủ đề hôm nay

---

## D. Part indicator

Examples:

> “Đây là phần một.”

or:

> “Và trong phần đầu tiên...”

Prefer natural spoken Vietnamese.

Avoid robotic:
> “Phần một trên ba.”

---

## E. Preview / tease

Use 1–2 sentences from the AI summary.

Do NOT reveal the conclusions.

Example:

> “Từ những vụ mất tích không để lại dấu vết, những hiện tượng kỳ lạ dưới đáy đại dương, cho đến những dấu hiệu ngoài không gian khiến giới khoa học phải đặt lại những giả thuyết tưởng như đã chắc chắn.”

Purpose:
curiosity, not summary.

---

## F. Transition

End with a short transition.

Examples:

> “Và bây giờ, hãy bắt đầu với ẩn số đầu tiên.”

or:

> “Hãy bắt đầu từ câu chuyện đầu tiên.”

Avoid repeating generic phrases in every episode.

---

# 17. INTRODUCTION WRITING STYLE

The narration should:

- sound spoken, not written like an article
- use natural Vietnamese
- preferably use sentences around 8–20 words
- create curiosity
- avoid fake sensationalism
- avoid inventing facts
- avoid revealing conclusions
- avoid overusing “bí ẩn”
- avoid repeatedly restating the title
- avoid excessive clickbait

Tone:

**calm + intriguing + documentary**

Not:

**loud / exaggerated YouTube clickbait**

---

# 18. HOST AUDIO

Host source:

`automation/data/presets/ẩn số/ẩn-số-host.mp4`

MUTE original host audio completely.

Do NOT preserve original speech.

Reuse the project's existing TTS infrastructure.

Inspect current code/services first.

Do NOT add a new paid API unless absolutely necessary.

---

# 19. HOST VIDEO DURATION

Do NOT force narration to fit the original host clip duration.

If narration is longer:

loop the host video.

Looping is acceptable for this prototype.

Priority:

1. natural narration
2. clear TTS
3. acceptable loop

Perfect lip-sync is NOT required at this prototype stage unless the project already supports it.

---

# 20. INTRO → MAIN CONTENT TRANSITION

The prototype must clearly demonstrate the visual state change.

## INTRO STATE

- cleaned `Ẩn-Số-First.png`
- NO upper blur
- NO lower blur
- host centered
- TTS introduction

## MAIN CONTENT STATE

- sharp content in center
- upper blur appears
- lower blur appears
- logo appears on left side of upper blur
- watermark appears on right side of upper blur
- host moves to bottom-left
- host overlaps lower blur

A simple clean cut or short crossfade is acceptable.

Do NOT spend excessive time on advanced animation.

The visual state distinction is more important than transition complexity.

---

# 21. OUTPUT 1 — MAIN CONTENT STATIC LAYOUT

Create a static image:

**1080 × 1920**

Use:

`/home/quyen/projects/Free-AI-Social-Media-Scheduler/reports/image.png`

as fake MAIN VIDEO content.

The mockup must show:

- brand
- tagline
- translated title
- part
- upper blur
- logo on upper-left blur
- watermark on upper-right blur
- sharp center content
- lower blur
- host bottom-left
- host overlapping lower blur
- subtitle sample
- dark branded background

Store:

`artifacts/an-so/layout/`

Example:

`artifacts/an-so/layout/main-layout-v1.png`

---

# 22. OUTPUT 2 — INTRODUCTION PREVIEW

Use cleaned:

`automation/data/presets/ẩn số/Ẩn-Số-First.png`

Create a vertical intro preview with:

- NO upper blur
- NO lower blur
- centered host
- original host audio muted
- TTS narration
- title
- part
- branding
- subtitles if appropriate

Store:

`artifacts/an-so/intro/`

Example:

`artifacts/an-so/intro/intro-v1.mp4`

---

# 23. OUTPUT 3 — WATERMARK CLEANUP

Store:

`artifacts/an-so/watermark-cleanup/`

Include:
- before
- after
- side-by-side if useful

---

# 24. OUTPUT 4 — REPRESENTATIVE FRAMES

Extract representative frames from intro preview.

Store:

`artifacts/an-so/intro/frames/`

At minimum:
- opening frame
- middle frame
- final frame

---

# 25. VISUAL VERIFICATION IS REQUIRED

Do NOT declare success merely because FFmpeg exits with code 0.

Actually inspect outputs.

## Main layout checks

Verify:
- 1080×1920
- no clipping
- no distortion
- main image remains sharp
- blur bands look intentional
- logo is upper-left blur
- watermark is upper-right blur
- host is bottom-left
- host overlaps lower blur
- subtitles do not collide with host
- title is readable

## Intro checks

Verify:
- no blur bands
- `Ẩn-Số-First.png` is visible
- host is centered
- host does not cover important title text
- original host audio is muted
- TTS is audible
- no black frames
- loop is acceptable
- output is 9:16
- subtitles remain readable

---

# 26. REPORT

Create:

`reports/an-so-intro-layout-prototype.md`

Include:

## Assets found

List relevant input assets.

## Watermark cleanup

Explain:
- what was cleaned
- method used
- before/after paths

## Intro layout

Explain:
- no blur bands
- `Ẩn-Số-First.png`
- centered host
- title / branding placement
- subtitle placement

## Main content layout

Explain:
- upper blur
- lower blur
- logo on upper-left
- watermark on upper-right
- host bottom-left
- host overlap with lower blur
- subtitle safe zone

## Introduction content

Include:
- translated title
- part number
- original AI summary
- rewritten narration

## TTS

Include:
- TTS system reused
- voice/config
- duration

## Host

Include:
- original audio muted
- looping method
- intro position
- main-content position

## Technical details

Include:
- canvas size
- FFmpeg flow/commands
- fonts
- blur settings
- scaling/cropping
- host looping
- subtitle placement

## Artifacts

List:
- main layout image
- intro MP4
- cleanup comparisons
- intro screenshots

## Known limitations

Clearly state anything imperfect.

---

# 27. DO NOT DO THESE YET

Do NOT:

- rewrite the full production pipeline
- process a full real source video just for layout testing
- delete source assets
- overwrite original images before verification
- add a paid API without asking
- hardcode one intro globally
- invent missing story facts
- force perfect lip-sync
- over-engineer animation
- redesign unrelated workflow pieces
- optimize performance prematurely

---

# 28. SUCCESS CRITERIA

The task is successful only when:

- [ ] Gemini watermark cleanup is completed for relevant IMAGE assets
- [ ] Before/after evidence exists
- [ ] Original assets remain preserved
- [ ] Main content static mockup exists
- [ ] Main mockup uses `reports/image.png`
- [ ] Main content has upper blur
- [ ] Main content has lower blur
- [ ] Main content remains sharp in center
- [ ] Logo is LEFT inside upper blur
- [ ] Watermark is RIGHT inside upper blur
- [ ] Main-content host is bottom-left
- [ ] Main-content host overlaps lower blur
- [ ] Intro has NO upper blur
- [ ] Intro has NO lower blur
- [ ] Intro uses cleaned `Ẩn-Số-First.png`
- [ ] Intro host is centered
- [ ] Original host audio is muted
- [ ] TTS narration is audible
- [ ] Subtitle placement avoids host
- [ ] Intro preview MP4 exists
- [ ] Representative intro frames exist
- [ ] Outputs were visually inspected
- [ ] Markdown report exists
- [ ] Production pipeline was NOT unnecessarily modified

---

# 29. FINAL RESPONSE FORMAT

At the end, do NOT only say “done”.

Show me:

1. MAIN CONTENT layout artifact
2. INTRODUCTION preview MP4
3. intro screenshots
4. watermark cleanup before/after
5. final rewritten introduction narration
6. report path
7. any visual issue you found
8. what you recommend adjusting before integrating this into production