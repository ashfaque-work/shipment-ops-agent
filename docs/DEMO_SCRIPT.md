# Demo script (90 seconds)

A voice-over to read while the demo video plays. The video is produced automatically; you
only record your voice.

## 1. Produce the video (no API keys needed)

```bash
cd frontend
npx playwright install chromium          # first time only
DEMO_PACE=1.7 npx playwright test -c playwright.demo.config.ts
```

This builds the app, starts the API in **replay** mode (real Gemini responses recorded
once in `data/demo/demo_llm.json`, replayed exactly), drives the story in a browser and
writes:

- `docs/demo/demo.webm` - about 1:28 at `DEMO_PACE=1.7` (about 1:00 at the default pace)
- `docs/demo/timeline.txt` - when each scene starts, in case your run's timing differs

Regenerate the README GIF from it with
`uv run --with imageio-ffmpeg python scripts/make_demo_gif.py`.
To re-record the model responses themselves (needs `GEMINI_API_KEY`, about 11 calls):
`DEMO_LLM=record npx playwright test -c playwright.demo.config.ts`.

To combine your recording with the video (ffmpeg ships with `imageio-ffmpeg`):

```bash
ffmpeg -i docs/demo/demo.webm -i voiceover.m4a -c:v libx264 -pix_fmt yuv420p \
       -c:a aac -shortest docs/demo/demo-voiceover.mp4
```

## 2. Narration

Times match `timeline.txt` for a `DEMO_PACE=1.7` run (± a second). Roughly 2.5 words per
second; pause where there's a gap.

**0:00 - Opening** *(empty dispatch desk)*
> This is Shipment Ops Agent: an AI assistant for a small exporter's dispatch desk.

**0:05 - Tracking** *("Why is order 4518 stuck?")*
> I ask in plain language. The agent calls the carrier's tracking tool, and on the right
> you see exactly what it returned. Held at customs: a missing HS code.

**0:16 - Customer email**
> An email to the customer: filled from the carrier's data, so dates and reasons can't
> be invented. It drafts; it never sends.

**0:28 - Rate comparison**
> Next, shipping options for an order to Nairobi. It compares the carrier's quotes and
> recommends Standard, with the price and transit-time trade-off spelled out.

**0:41 - Confirmation card**
> Booking is irreversible, so the agent can't do it alone. It only gets a priced preview,
> and this confirmation card. Nothing is booked until I press "Book shipment". That
> gate is enforced in code and in the MCP protocol, not by the prompt.

**0:58 - Documents**
> Now paperwork: an invoice and its bill of lading. Rules check the extracted fields, and
> the weights disagree: 60.68 versus 73.32 kilograms. That gets shipments held.

**1:09 - Eval results**
> And it's measured. Tool selection, multi-turn scenarios, safety checks and document
> extraction for each model, plus a harder set with ambiguity and prompt injection. Zero
> unconfirmed bookings everywhere. Every number re-runs in CI from recorded responses.

**1:19 - Close** *(eval table stays on screen)*
> The carrier is mocked and the documents are synthetic, so all of it is reproducible.
> The code is on GitHub.

*(about 214 words, ~2.4 per second over 88 s)*

## Notes

- What's on screen is **replayed** model output. The header says "Replay of recorded model
  responses", so the demo is honest even without a live model. The demo script fails if
  any step falls back to the rule-based policy.
- The scenes and wording are fixed by `frontend/e2e/demo.spec.ts`. If you change a
  message there, re-record the responses (`DEMO_LLM=record`), because the replay matches
  requests exactly.
