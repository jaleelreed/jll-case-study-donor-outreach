---
name: charity-donor-outreach
description: >-
  Generate personalized fundraising letter drafts from an uploaded donor list.
  Use this skill only when a user has uploaded a donor CSV and wants outreach
  letters, appeal letters, or a fundraising mail merge for a specific
  campaign. Do not use for general email drafting, grant reports, volunteer
  coordination, or financial questions unrelated to donor letters, and do not
  activate on a general question about how to prepare a donor list -- answer
  that directly without invoking the full workflow.
---

# Charity Donor Outreach Letter Generator

Generates draft fundraising letters for a donor list, one per donor, with ask
amounts computed deterministically by a bundled script. All output is a draft
for human review. This skill never sends anything.

## Core principles

1. **The uploaded donor CSV is the only source of donor data.** This skill
   contains no donor records. Never rely on remembered, embedded, or invented
   donor information.
2. **Code computes, the model writes.** Every ask amount, tier, and recency
   status comes from `scripts/calculate_asks.py`. The model's job is the
   letter prose, using only values from the script's output.
3. **Every factual claim in a letter must trace to an input.** Campaign facts
   come from the campaign configuration. Donor facts come from the CSV. If a
   fact is unavailable, omit it. Never invent names, URLs, statistics,
   matching gifts, program details, or registration counts.
4. **Missing data is an exception, never an assumption.** Records with
   missing or inconsistent required fields go to the exceptions report for a
   human to resolve. Do not guess and proceed.

## Required inputs

### 1. Donor CSV

Expected columns (header names are case-insensitive; extra columns are
ignored):

| Column           | Required | Notes                                          |
|------------------|----------|------------------------------------------------|
| donor_id         | Yes      | Stable unique ID from the CRM                  |
| first_name       | Yes      |                                                |
| last_name        | Yes      |                                                |
| title            | No       | Honorific exactly as recorded (Dr., Ms., Rev.) |
| gift_history     | Yes      | Semicolon-separated `YYYY:amount` pairs        |
| volunteer        | No       | true/false, defaults to false                  |
| do_not_contact   | No       | true/false; true excludes the donor            |
| deceased         | No       | true/false; true excludes the donor            |
| region           | No       | Used only for optional regional references     |

Largest gift, lifetime total, last gift year, value tier, and recency status
are all **computed** from `gift_history` by the script. If the CSV includes a
tier column, the script compares it to the computed tier and flags mismatches
in the exceptions report; the computed tier is used.

### 2. Campaign configuration

Collect these from the user before generating anything. If any required field
is missing, ask for it. Do not substitute placeholder or invented values.

| Field                  | Required | Notes                                        |
|------------------------|----------|-----------------------------------------------|
| charity_name           | Yes      | e.g. "ASPCA"                                  |
| campaign_type          | Yes      | emergency, annual_fund, capital, or event    |
| campaign_description   | Yes      | 1–3 sentences of true campaign facts to draw from |
| donation_url           | Yes      | Verbatim; never modify or invent             |
| signer_name            | Yes      | Real staff member who will sign              |
| signer_title           | Yes      |                                              |
| campaign_date          | Yes      | Anchors recency calculations (YYYY-MM-DD)    |
| match_details          | No       | Sponsor, ratio, and deadline of a **confirmed** match. If absent, no match language may appear anywhere. |
| event_registrations    | No       | Verified count; only cite if provided        |

## Workflow

1. **Validate inputs.** Confirm the CSV parses against the schema and the
   campaign configuration is complete. Stop and ask the user about anything
   missing.
2. **Run the ask calculator.**
   `python scripts/calculate_asks.py --csv <donor_file> --config <config.json> --out ./outputs/`
   This produces:
   - `outputs/asks.csv`: one row per letter-eligible donor, with columns
     `donor_id, first_name, last_name, title, value_tier, recency_status,
     largest_gift, lifetime_total, last_gift_year, ask_amount,
     applied_modifiers`. `applied_modifiers` lists every adjustment that
     touched the ask (e.g. `loyalty_uplift`, `volunteer_uplift`,
     `lapsed_reentry_ask`, `clamped_at_cap`, `clamped_at_floor`) and any
     `tier_mismatch:stated=X,computed=Y` flag. **These donors are still
     drafted for** -- a flag is a review signal, not an exclusion.
   - `outputs/exceptions.csv`: donors **excluded** from drafting entirely,
     with `donor_id, reason`. Reasons are `suppressed:deceased`,
     `suppressed:do_not_contact`, `missing_fields:<cols>`, or
     `unprocessable:<error>`.
   - `outputs/metrics.json`: run-level counts for performance monitoring --
     total records, asks produced, exception count and rate, flagged-ask
     count and rate, and a breakdown of flag types. Log this file (or its
     key figures) alongside each campaign run so a rising exception or flag
     rate is caught early rather than discovered downstream.
3. **Draft letters** for every donor in `asks.csv` (including flagged rows),
   one HTML file per donor, saved to `outputs/letters/<donor_id>.html`, using
   the template and the tone/messaging rules below. Use only the ask amount
   from `asks.csv`. Donors in `exceptions.csv` are never drafted for.
4. **Deliver for review.** Present the letters directory, `asks.csv`,
   `exceptions.csv`, and `metrics.json` to the user. State clearly that these
   are drafts, that exceptions require human resolution before those donors
   can be contacted at all, and that flagged rows in `asks.csv` (especially
   `tier_mismatch` and `clamped_at_*`) warrant a closer look. A staff member
   should review letters (all Platinum/Gold letters individually; a sample
   of at least 10% elsewhere, plus every flagged row) before any are sent.
5. **Track outcomes.** After human review, record the reviewer edit rate
   (share of letters staff changed before sending) alongside the run's
   `metrics.json`. Rising exception rates, flag rates, or edit rates over
   successive campaigns indicate a data-quality or rule problem that should
   be investigated before the next run, not silently absorbed.

## Segmentation

Two independent dimensions, both computed by the script:

**Value tier** (lifetime giving): Platinum ≥ $50,000 · Gold $10,000–$49,999
· Silver $1,000–$9,999 · Bronze < $1,000.

**Recency status** (last gift vs. campaign_date): Active ≤ 36 months ·
Lapsed > 36 months.

Treatment is the combination. Tier sets tone and the tier-specific offer;
recency adjusts the ask (see script) and adds a welcome-back opening for
lapsed donors. A lapsed Platinum donor gets Platinum tone and stewardship
with a re-entry ask, never the small-donor lapsed treatment.

### Tone and tier-specific content

- **Platinum**: Formal, personal stewardship. Mention a naming opportunity
  only if the campaign configuration lists one as available.
- **Gold**: Warm and professional. Mention legacy giving options.
- **Silver**: Friendly. Mention the monthly giving upgrade.
- **Bronze**: Encouraging and casual. Mention peer fundraising pages.
- **Lapsed (any tier)**: Open with genuine, warm acknowledgment of their past
  support and what it accomplished. Welcoming, never apologetic and never
  guilt-based. Skip the tier upsell (naming, legacy, monthly) in favor of a
  simple invitation to reconnect.

### Campaign messaging

- **Emergency appeal**: Urgency grounded in the true facts from
  campaign_description. Match language **only** if match_details is present,
  and only stating the confirmed sponsor, ratio, and deadline.
- **Annual fund**: Consistency and community. Reference the donor's giving
  streak only if gift_history actually shows consecutive years.
- **Capital campaign**: Legacy and permanence.
- **Event fundraiser**: Community and participation. Cite registration
  numbers only if event_registrations is provided.
- **Unknown or other**: Ask the user to pick one of the four. Do not default
  silently.

## Personalization hierarchy

When writing the opening and campaign paragraphs, draw on donor information
in this priority order, using only what the CSV actually contains:

1. Verified giving history (years of support, consecutive-year streaks)
2. Volunteer status
3. Region (light touch, e.g. "supporters across the Midwest")
4. General appreciation language

Skip any level with no data behind it. A shorter, true letter beats a longer
one padded with invented specifics.

## Salutation rules

- Title on record: `Dear [Title] [Last Name],`
- No title, Platinum/Gold: `Dear [First Name] [Last Name],`
- No title, Silver/Bronze: `Hi [First Name],`
- **Never infer a title or gender from a name.** No exceptions.

## Ask amounts

All ask math lives in `scripts/calculate_asks.py`. Summary of its logic (do
not reimplement mentally; run the script):

1. Base ask: largest gift × tier rate (Platinum 40%, Gold 25%, Silver 15%;
   Bronze: 125% of largest gift with a $75 floor).
2. Loyalty uplift ×1.10 if the donor gave in the calendar year before
   campaign_date.
3. Volunteer uplift: the lesser of $100 or 20% of the running amount.
4. Emergency campaign multiplier ×1.20, only when campaign_type is emergency.
5. Lapsed adjustment: ask is capped at the donor's largest gift (a re-entry
   ask at a familiar level), with a $50 floor.
6. Round to the nearest $25 **last**, then clamp to bounds: never below $25,
   never above 150% of the donor's largest gift. Clamped asks are flagged.

Coefficients sit at the top of the script and should be reviewed with the
development team before each campaign.

## Letter template

Fill every placeholder from the campaign configuration or `asks.csv`. If a
value for a placeholder is unavailable, stop and resolve it; never leave a
bracket or invent a value.

```html
<html>
<body style="font-family: Georgia; padding: 30px; max-width: 600px; color: #222;">

  <p style="text-align:right; color: #888;">[CAMPAIGN_DATE]</p>

  <p>[SALUTATION]</p>

  <p>[OPENING: one to two sentences of genuine, specific gratitude.
  Reference their support history in natural language (e.g. "your support
  since 2015"). Mention the lifetime total only for Platinum and Gold, where
  stewardship convention expects it.]</p>

  <p>[CAMPAIGN_PARAGRAPH: two to three sentences built only from
  campaign_description and the messaging rules above.]</p>

  <p>Would you consider a gift of <strong>$[ASK_AMOUNT]</strong>?
  [TIER_SPECIFIC_LINE per the rules above.]</p>

  <p>You can give at <strong>[DONATION_URL]</strong>, or simply reply to
  this letter.</p>

  <p>With gratitude,<br>
  <strong>[SIGNER_NAME]</strong><br>
  [SIGNER_TITLE], [CHARITY_NAME]</p>

</body>
</html>
```

## Hard rules

- Never state or imply a matching gift without confirmed match_details.
- Never invent URLs, staff names, statistics, or program facts.
- Never infer gender or honorifics.
- Never compute an ask amount outside the script.
- Never draft a letter for a donor listed in `exceptions.csv`.
- Never present output as final; it is always a draft pending human review.
- Never skip the `metrics.json` step; performance monitoring is part of the
  workflow, not optional follow-up.
