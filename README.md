# Bill Shocker ⚡

> Snap your bill. Understand why it's high. Know what to change next month.

AI-assisted electricity-bill explainer for Pakistani households (FESCO first). **Status: Stages 1-2 of the MVP**
(upload → extract → review → validate → results → English/Urdu explanation → appliance questions → evidence-labelled factors).
What-if simulator, trends polish and sharing come next.

## Principles (enforced in code and tests)
- **AI reads, Python calculates.** The model only transcribes printed text; every sum/ratio is in `services/validator.py`.
- **Nothing is invented.** Every field can be *unknown*, carries the printed `source_text`, and has a confirmation state.
- **Discrepancies are shown, not hidden** (printed total vs. sum of charges, readings vs. units, payable chain).
- **No tariff guesses.** `data/tariffs/fesco_tariffs.json` ships as an *unverified placeholder*; no estimates are produced until you add a verified, sourced version.
- **Protected-consumer status is never decided** from a single month's units.
- **Privacy:** the photo is processed in memory, EXIF/GPS is stripped, and nothing is written to disk. The prompt forbids transcribing names, addresses, CNIC, consumer/meter numbers and QR content, and returned text is scrubbed for long digit strings. Review data lives only in the browser's `sessionStorage` (cleared by "Clear my data" or when the tab closes).

> **Cover-before-upload (built in):** after choosing a photo, the user drags black boxes over personal details *in the browser*. Only the re-encoded, covered JPEG is uploaded (EXIF/GPS gone). The upload button stays disabled until at least one area is covered or the user ticks "no personal details visible". If the browser can't decode the photo for masking, nothing is sent (fail-closed). The e2e test checks the uploaded pixels are really black.

> **Free Gemini keys:** Google's pricing page states that free-tier content is used to improve its products (paid tier: not). Mask personal details before uploading, or use a paid key.

> **Known limitation:** covering is manual. Anything the user forgets to cover is still sent to the AI provider, so the tool lists what to cover and requires an explicit confirmation.

## Stage 2 behaviour
- `POST /api/explain` builds a PII-free facts object from *confirmed* values (all arithmetic done in Python), asks the model to phrase it in English or Urdu, then **rejects any answer containing a number that is not in the facts** and falls back to a Python template. With no API key, or in synthetic mode, the template is used (Urdu needs the AI).
- `POST /api/factors` takes four short appliance answers and returns factors labelled **Supported by your bill** (figures printed on the bill), **Possible, not proven** (self-reported appliances plus rising units), **Unlikely** (units not higher than earlier months) or **Note**. Appliance answers can never produce "Supported", and there are no kWh estimates yet.

## Bill layouts
Checks try each way a bill can add up instead of assuming one layout:
- charges → current bill: net charges + one taxes line, **or** itemised lines (with or without FPA);
- current bill → payable: current + arrears + installment + adjustments, **or** the same plus a separately printed FPA;
- two-tier late surcharge, each tier checked on its own;
- comparisons use last month, the same month last year and the median of the printed history, because an all-months average is distorted by seasons.

Fixtures: `data/demo/synthetic_bill.json` (itemised layout) and `synthetic_bill_aggregate.json` (net + taxes line, FPA after current bill, two tiers). Both are invented. Never commit real bill photos or transcriptions.

## Run it
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env           # then put your key in .env   (macOS/Linux: cp)
python app.py                    # http://127.0.0.1:5000
```
No API key yet? Run with `BILLSHOCKER_SYNTHETIC=1` (PowerShell: `$env:BILLSHOCKER_SYNTHETIC=1`) to get a clearly-labelled **fake** bill and try every screen.

## Environment variables
| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` or `anthropic`. If unset, Anthropic is used only when it is the only key present. |
| `GEMINI_API_KEY` | *(empty)* | Server-side only. Missing → upload page shows a warning and `/api/extract` returns a friendly 503. |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Model id from Google AI Studio (check which ones your key's tier allows). |
| `ANTHROPIC_API_KEY` | *(empty)* | Only needed when `LLM_PROVIDER=anthropic`. |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | Anthropic model id (only for the Anthropic provider). |
| `ANTHROPIC_TIMEOUT_S` | `60` | Per-request timeout. |
| `MAX_UPLOAD_MB` | `8` | Server-enforced upload limit. |
| `BILLSHOCKER_SYNTHETIC` | `0` | `1` = skip the AI, return a labelled synthetic bill (demos/UI work). |
| `FLASK_DEBUG`, `PORT` | `0`, `5000` | Dev server options. |

## Tests
```bash
python -m pytest -q
```
Optional browser test of the cover tool (skipped automatically if Playwright isn't installed): `pip install playwright && playwright install chromium && python -m pytest tests/e2e -q`.

All fixtures are synthetic (`data/demo/synthetic_bill.json`; images are generated in-test). The Anthropic client is stubbed, so tests need no key or network.

## Layout
```
app.py                  routes (/, /review, /results, /api/extract, /api/validate, /api/explain, /api/factors, /healthz)
config.py               env loading
services/image_check.py size/type/magic-byte/Pillow checks, rotate, strip EXIF, downscale
services/llm.py         AI providers behind one interface: Gemini (REST) and Anthropic; friendly error mapping
services/extractor.py   bill-reading prompt + post-checks, SyntheticExtractor
services/schema.py      field list (single source of truth), tool schema, sanitising of model/client JSON
services/pii.py         scrubs ID-like digit runs / emails from returned text
services/validator.py   parsing + all arithmetic + findings
services/explainer.py   facts builder, AI wording, number guard, template fallback
services/factors.py     appliance questions + evidence-labelled factors
services/tariffs.py     loads versioned tariff JSON; only `verified` versions are usable
data/tariffs/           editable tariff config (placeholder)
templates/, static/  Own CSS design system (no CSS framework) + vanilla JS, SVG charts
tests/                  pytest suite
```

## Adding verified tariffs later
Append a version to `versions` in `fesco_tariffs.json` with `status: "verified"`, `effective_from`, a `source` (notification name/URL/date), `applicability` notes and the `categories` rates. Never edit past versions.

## Notes
- The UI uses its own stylesheet (`static/css/app.css`, light only, warm "Runway" ledger style: cream, espresso, amber). Charts are hand-built SVG (`static/js/charts.js`) with tooltips and a "View as table" alternative; only Google Fonts load from a CDN and the app works without them.
- Run behind HTTPS in production. Add rate limiting if the API key is exposed to the public.

## Deploy to Vercel

Files: `api/index.py` (entry), `vercel.json` (routes everything to Flask, 60 s function limit), `.vercelignore`.

```
npm i -g vercel
vercel login
vercel            # first run: preview deployment
vercel --prod
```

Set these in Vercel → Project → Settings → Environment Variables (never commit them):

| Name | Value |
|------|-------|
| `GEMINI_API_KEY` | your Gemini key |
| `LLM_PROVIDER` | `gemini` |
| `GEMINI_MODEL` | model you want (default `gemini-3.8-flash`) |
| `MAX_UPLOAD_MB` | `4` (Vercel rejects request bodies over 4.5 MB; the browser already shrinks photos to ≤2000 px) |

Do not set `BILLSHOCKER_SYNTHETIC` in production. Check `https://<your-app>.vercel.app/healthz` — it should report `"ai_configured": true`. Nothing is stored on the server; review data lives only in the user's browser session.
#   B i l l S h o c k e r A i  
 