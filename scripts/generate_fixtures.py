"""LLM-driven fixture generator for high-quality, diverse seed content.

Produces four fixture files (saved as JSON under `seed/fixtures/`):

  * customer_reports.json — 40 unique apiary inspection narratives, each
    bound to deterministic structured fields (customer/site/author/varroa/
    diseases/etc) and tagged with retrieval keywords.
  * emails.json           — 80 unique inbound emails (45 clear orders,
    20 ambiguous, 8 non-orders, 7 multi-site complex). Each has a unique
    body, distinct customer voice, and grounded slot values.
  * product_data_sheets.json — 10 product data sheets covering regulated
    treatments + signature SKUs (Italian queens, packaged bees, etc.).
  * regulatory_docs.json     — 5 regulatory references (state import
    permits, VFD, EPA label restrictions, TTB analog permits, federal
    inspection triggers like Tropilaelaps quarantine).

Each LLM call is independent — no batching — so the model genuinely
diverges across items rather than templating from a single response.
Sequential calls keep cost low (~$0.10 total at gpt-4o-mini pricing).

Usage (from inside the seed container):

    docker compose run --rm seed python -m scripts.generate_fixtures
    docker compose run --rm seed python -m scripts.generate_fixtures --type narratives
    docker compose run --rm seed python -m scripts.generate_fixtures --force

By default, fixture files that already exist are LEFT ALONE. Pass
`--force` to overwrite. This makes the script safe to re-run for partial
regeneration.

Side effects:
  * Calls the OpenAI API (uses OPENAI_API_KEY from env).
  * Writes to `seed/fixtures/*.json`.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from faker import Faker
from openai import OpenAI

# Reuse the same generators the seed pipeline uses, so the deterministic
# fields (customer names, site names, apiarist emails) line up exactly.
from seed.faker_providers import BeekeepingProvider
from seed.models import Product
from seed.regions import generate as generate_regions
from seed.apiarists import generate as generate_apiarists
from seed.customers import generate as generate_customers
from seed.sites import generate as generate_sites
from seed.products import generate as generate_products

log = logging.getLogger("generate_fixtures")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "seed" / "fixtures"
SEED_VALUE = 42
GEN_MODEL = os.environ.get("FIXTURE_GEN_MODEL", "gpt-4o-mini")


# ---------------------------------------------------------------------------
# OpenAI helper
# ---------------------------------------------------------------------------


def _client() -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is required to generate fixtures. Set it in .env."
        )
    return OpenAI()


def _llm(system: str, user: str, *, max_tokens: int = 800, retries: int = 2) -> str:
    """Single chat completion. Retries on transient errors. Returns raw text."""
    client = _client()
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=GEN_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.85,  # higher temp = more diverse outputs
                max_tokens=max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < retries:
                wait = 1.5 * (attempt + 1)
                log.warning("LLM call failed (%s); retrying in %.1fs", exc, wait)
                time.sleep(wait)
    raise RuntimeError(f"LLM call failed after {retries + 1} attempts") from last_err


# ---------------------------------------------------------------------------
# Deterministic spec generation (matches the seed pipeline's randomness)
# ---------------------------------------------------------------------------


def _build_faker() -> Faker:
    faker = Faker(locale="en_US")
    Faker.seed(SEED_VALUE)
    faker.add_provider(BeekeepingProvider)
    return faker


# ---------------------------------------------------------------------------
# 1. Customer report narratives + tags (40 entries)
# ---------------------------------------------------------------------------


AUTHOR_VOICES = [
    "Terse field rep — clipped sentences, abbreviations, jumps between observations.",
    "Verbose new-hire — over-explains every step, narrates the drive in.",
    "By-the-book ops — methodical, references SOP numbers, lists tasks completed.",
    "Senior apiarist — historical comparisons, references prior visits, opinions.",
    "Casual but skilled — chatty tone, drops anecdotes, still hits all the technical fields.",
    "Concerned — flags a developing issue, recommends specific follow-up, urgent register.",
]

SCENARIOS = [
    "routine_low_varroa",
    "varroa_alert_climbing",
    "disease_observation_efb",
    "disease_observation_afb_quarantine",
    "tropilaelaps_suspect_federal_alert",
    "regulatory_pesticide_drift",
    "weather_impact_storm",
    "queen_failure_replacement",
    "robbing_pressure",
    "pre_pollination_setup",
    "post_bloom_pull",
    "owner_business_change",
    "neighbor_orchard_concern",
    "monitoring_unremarkable",
]


def _build_inspection_specs(faker: Faker, sites, apiarists, n: int = 40) -> list[dict]:
    """Pick a deterministic inspection spec list — same scaffold as
    seed/inspections.py uses, but holds back the narrative for the LLM."""
    from datetime import date, timedelta

    field_apiarists = [a for a in apiarists if a.role == "apiarist"]
    anchor = date.today()
    out = []

    for i in range(n):
        site = faker.random_element(sites)
        author = faker.random_element(field_apiarists)
        visit_date = anchor - timedelta(days=faker.random_int(0, 365))

        # Scenario-driven varroa + disease so the narrative has interesting
        # ground truth to anchor to. Heuristic: spread scenarios deterministically.
        scenario = SCENARIOS[i % len(SCENARIOS)]
        if "varroa_alert" in scenario:
            varroa = round(faker.random_int(310, 480) / 100.0, 2)
        elif "low_varroa" in scenario or "monitoring_unremarkable" in scenario:
            varroa = round(faker.random_int(40, 180) / 100.0, 2)
        else:
            varroa = round(faker.random_int(80, 320) / 100.0, 2)

        if "tropilaelaps" in scenario:
            diseases = ["TROPILAELAPS"]
        elif "afb" in scenario:
            diseases = ["AFB"]
        elif "efb" in scenario:
            diseases = ["EFB"]
        elif faker.random_int(1, 100) <= 12:
            diseases = [faker.random_element(["AFB", "EFB", "NOSEMA", "SHB"])]
        else:
            diseases = ["NONE"]

        pesticide_note = None
        if "pesticide_drift" in scenario or faker.random_int(1, 100) <= 18:
            pesticide_note = faker.random_element([
                "Neighbor orchard sprayed dormant oil last Thursday — minimal exposure risk during current bloom phase",
                "Grower confirmed no spray during bloom per county NOI filing on record",
                "Pesticide applicator truck visible on adjacent block at dawn; recommended blocking the upwind entrance",
                "Drift suspected from neighboring stone fruit pesticide application; collected sample bees for lab analysis",
            ])

        crop_key = next(iter(site.acreage_by_crop.keys()), "almond")
        voice = AUTHOR_VOICES[i % len(AUTHOR_VOICES)]

        out.append({
            "customer_name": site.customer_name,
            "site_name": site.name,
            "author_email": author.email,
            "author_name": author.name,
            "visit_date": visit_date.isoformat(),
            "hives_placed": faker.random_int(80, 500),
            "varroa_count": varroa,
            "diseases_observed": diseases,
            "pesticide_exposure_note": pesticide_note,
            "crop": crop_key,
            "scenario": scenario,
            "author_voice": voice,
        })
    return out


def _generate_one_narrative(spec: dict) -> tuple[str, list[str]]:
    """Single LLM call → (narrative, tags). Each call is independent so
    the model isn't pressured to homogenize across items."""
    diseases = spec["diseases_observed"]
    disease_str = "none observed" if diseases == ["NONE"] else ", ".join(diseases)

    system = (
        "You are an experienced commercial-beekeeping field apiarist writing an "
        "inspection report after visiting a customer's bee yard. Your reports are "
        "read by ops managers and feed into a Sales Copilot CRM. Write in the "
        "specified VOICE and capture the SCENARIO faithfully. Your narrative "
        "must reference all the structured fields provided (visit date, customer, "
        "site, hive count, varroa count, diseases, pesticide note if any) and "
        "include lateral observations a real apiarist would note: weather, "
        "queen status, brood pattern, stores, neighbor activity, "
        "follow-up actions. Length: 1200-1800 characters of narrative text. "
        "Do not use bullet lists. Do not add headers or markdown. Plain "
        "paragraphs only. Be specific and concrete, not generic."
    )

    user = (
        f"VOICE: {spec['author_voice']}\n"
        f"SCENARIO: {spec['scenario']}\n\n"
        f"FIELDS TO ANCHOR TO:\n"
        f"  Author: {spec['author_name']} ({spec['author_email']})\n"
        f"  Customer: {spec['customer_name']}\n"
        f"  Site: {spec['site_name']}\n"
        f"  Visit date: {spec['visit_date']}\n"
        f"  Hives placed at site: {spec['hives_placed']}\n"
        f"  Crop context: {spec['crop']}\n"
        f"  Varroa count: {spec['varroa_count']} mites per 100 bees "
        f"(treatment threshold is 3.0)\n"
        f"  Diseases: {disease_str}\n"
        f"  Pesticide note: {spec['pesticide_exposure_note'] or 'no specific concern'}\n\n"
        "Then on a new line at the end, output exactly:\n"
        "TAGS: tag1, tag2, tag3, tag4\n"
        "Where the tags are 4-6 short keyword phrases (snake_case) that "
        "categorize the report for retrieval — e.g., low_varroa, "
        "queen_failure, pesticide_drift, regulatory_alert, almond_pre_setup. "
        "Use scenario-relevant tags."
    )

    raw = _llm(system, user, max_tokens=800)

    # Extract tags from the trailing TAGS: line.
    narrative = raw
    tags: list[str] = []
    for marker in ("\nTAGS:", "\ntags:"):
        if marker in raw:
            parts = raw.rsplit(marker, 1)
            narrative = parts[0].strip()
            tags = [t.strip().lower().replace(" ", "_") for t in parts[1].split(",")]
            tags = [t for t in tags if t and len(t) <= 40]
            break

    return narrative, tags


def generate_customer_reports_fixture(n: int = 40) -> list[dict]:
    faker = _build_faker()
    # Order MUST match tests/test_seed_inspections.py::_setup_deps so the
    # Faker RNG state at each generator call lines up — otherwise site
    # names produced here drift from the ones the test suite generates,
    # and persistence raises LookupError on a missing site.
    regions = generate_regions()
    customers = generate_customers(faker)
    sites = generate_sites(faker, customers, regions)
    apiarists = generate_apiarists(faker, regions)

    specs = _build_inspection_specs(faker, sites, apiarists, n=n)
    out = []
    for i, spec in enumerate(specs, start=1):
        log.info("[narrative %d/%d] %s @ %s (%s)", i, len(specs),
                 spec["customer_name"], spec["site_name"], spec["scenario"])
        narrative, tags = _generate_one_narrative(spec)
        out.append({
            "customer_name": spec["customer_name"],
            "site_name": spec["site_name"],
            "author_email": spec["author_email"],
            "visit_date": spec["visit_date"],
            "hives_placed": spec["hives_placed"],
            "varroa_count": spec["varroa_count"],
            "diseases_observed": spec["diseases_observed"],
            "pesticide_exposure_note": spec["pesticide_exposure_note"],
            "narrative": narrative,
            "tags": tags,
            "_scenario": spec["scenario"],  # for debugging / diversity audit
        })
    return out


# ---------------------------------------------------------------------------
# 2. Email bodies (80 entries — 45 clear / 20 ambiguous / 8 non-order / 7 complex)
# ---------------------------------------------------------------------------


EMAIL_KIND_QUOTAS = {
    "clear_order": 45,
    "ambiguous": 20,
    "non_order": 8,
    "complex": 7,
}


def _generate_one_email_body(kind: str, customer_name: str, customer_segment: str, faker: Faker) -> tuple[str, str]:
    """Returns (subject, body)."""
    system = (
        "You are an inbound customer writing to a commercial pollination + bee "
        "products company called Meridian Apiaries. The recipient is the "
        "apiarist sales rep. Write naturally, vary your tone (formal, casual, "
        "rushed, friendly), include realistic specifics. NO subject line in "
        "the body. NO markdown. Plain text email body only."
    )

    contact_name = faker.name()
    contact_first = contact_name.split()[0]

    if kind == "clear_order":
        directive = (
            "INTENT: Request a clear, well-specified pollination contract or "
            "product order. Specify hive count or product quantity, target "
            "site/block, expected dates, any constraints (e.g. 'same as last "
            "year', 'blueberry bloom', 'split delivery'). Length: 60-160 words."
        )
    elif kind == "ambiguous":
        directive = (
            "INTENT: Open-ended question or fuzzy intent — exploring options, "
            "asking pricing, hedging on commitment, missing key details. "
            "Could be order-relevant later but isn't actionable as-is. "
            "Length: 50-130 words."
        )
    elif kind == "non_order":
        directive = (
            "INTENT: Not order-related. Newsletter forward, regulatory "
            "reminder, compliance question, social greeting, conference "
            "follow-up, paperwork query, etc. Length: 40-110 words."
        )
    else:  # complex
        directive = (
            "INTENT: Multi-site or multi-month or multi-product request "
            "spanning several considerations — schedule changes, contract "
            "restructuring, season planning across crops. Realistic and "
            "specific with multiple sites, dates, hive counts. Length: "
            "100-220 words."
        )

    user = (
        f"FROM CUSTOMER: {customer_name} ({customer_segment} segment)\n"
        f"SIGNED BY: {contact_name}\n\n"
        f"{directive}\n\n"
        f"Output exactly two lines: the subject line, then a blank line, "
        f"then the body text. Format:\n"
        f"SUBJECT: <subject>\n\n"
        f"<body text — multiple paragraphs OK, no markdown>"
    )

    raw = _llm(system, user, max_tokens=400)

    subject = ""
    body = raw
    if raw.upper().startswith("SUBJECT:"):
        first_line, _, rest = raw.partition("\n")
        subject = first_line.split(":", 1)[1].strip()
        body = rest.strip()
    else:
        # Fallback: synthesize a subject
        subject = f"From {customer_name}"
        body = raw

    return subject, body


def generate_emails_fixture() -> list[dict]:
    faker = _build_faker()
    # Same order as the customer-reports generator above for consistency
    # with the test suite's Faker RNG sequence.
    regions = generate_regions()
    customers = generate_customers(faker)
    _ = generate_sites(faker, customers, regions)  # advance RNG to match test order
    apiarists = generate_apiarists(faker, regions)

    out = []
    counter = 0
    for kind, count in EMAIL_KIND_QUOTAS.items():
        for _ in range(count):
            counter += 1
            customer = faker.random_element(
                [c for c in customers if c.market_segment.startswith("ORCHARD_")]
                if kind in ("clear_order", "complex")
                else customers
            )
            apiarist = faker.random_element(apiarists)
            log.info("[email %d/80 %s] from %s",
                     counter, kind, customer.name)

            subject, body = _generate_one_email_body(
                kind, customer.name, customer.market_segment, faker
            )

            out.append({
                "customer_name": customer.name,
                "from_customer": True,
                "account_id": apiarist.email,
                "to_email": apiarist.email,
                "subject": subject,
                "body_text": body,
                "_kind": kind,
            })
    return out


# ---------------------------------------------------------------------------
# 3. Product data sheets (10 entries covering important SKUs)
# ---------------------------------------------------------------------------


def _pick_data_sheet_skus(products: list[Product]) -> list[Product]:
    """Pick 10 products that should have data sheets — prioritize regulated
    items, then signature SKUs (queens, packages), filling out with treatments."""
    by_category: dict[str, list] = {}
    for p in products:
        by_category.setdefault(p.category, []).append(p)

    picks: list = []
    # All regulated treatments
    for p in products:
        if p.is_regulated and len(picks) < 6:
            picks.append(p)
    # Signature queen + bee SKUs
    for p in by_category.get("queens_and_bees", [])[:3]:
        if p not in picks:
            picks.append(p)
    # Fill with treatments
    for p in by_category.get("treatments", []):
        if p not in picks and len(picks) < 10:
            picks.append(p)
    return picks[:10]


def _generate_one_data_sheet(product: Product) -> str:
    system = (
        "You are a product/regulatory copywriter for a commercial bee-products "
        "supplier. Write a product data sheet — the kind a sales rep references "
        "on a call. Include real-feeling sections: product description, "
        "intended use, application/dosage if applicable, label warnings + "
        "personal protective equipment, regulatory status (EPA, FDA-VFD, "
        "state restrictions), shelf life, storage, contact for technical "
        "questions. Be technically specific. NO markdown. Plain text with "
        "section headers in CAPS. Length: 1200-2000 characters."
    )
    user = (
        f"PRODUCT SKU: {product.sku}\n"
        f"PRODUCT NAME: {product.name}\n"
        f"CATEGORY: {product.category}\n"
        f"DESCRIPTION: {product.description or 'see name'}\n"
        f"REGULATED: {'yes' if product.is_regulated else 'no'}\n\n"
        "Write the data sheet now."
    )
    return _llm(system, user, max_tokens=900)


def generate_product_data_sheets_fixture() -> list[dict]:
    faker = _build_faker()
    products = generate_products(faker)
    picks = _pick_data_sheet_skus(products)

    out = []
    for i, p in enumerate(picks, start=1):
        log.info("[product sheet %d/%d] %s — %s", i, len(picks), p.sku, p.name)
        content = _generate_one_data_sheet(p)
        out.append({
            "product_sku": p.sku,
            "product_name": p.name,
            "category": p.category,
            "is_regulated": p.is_regulated,
            "content": content,
        })
    return out


# ---------------------------------------------------------------------------
# 4. Regulatory documents (5 entries covering distinct regulatory regimes)
# ---------------------------------------------------------------------------


REGULATORY_TOPICS = [
    {
        "code": "USDA-APHIS-TROPILAELAPS-Q",
        "title": "USDA-APHIS Tropilaelaps quarantine and reporting protocol",
        "scope": "federal",
        "summary": "Tropilaelaps spp. are a federal quarantine pest in the US. "
                   "Suspected detection requires immediate reporting to APHIS "
                   "and state apiarist; movement of suspect colonies is restricted "
                   "pending laboratory confirmation.",
    },
    {
        "code": "CDFA-APIARY-MOVEMENT",
        "title": "California CDFA apiary movement and registration requirements",
        "scope": "state-CA",
        "summary": "All commercial apiaries in California must register annually "
                   "with the county agricultural commissioner. Inter-county movement "
                   "of 5+ colonies requires advance notice. Tampering with required "
                   "identification placards is prosecutable.",
    },
    {
        "code": "FDA-VFD-OXYTET",
        "title": "FDA Veterinary Feed Directive — oxytetracycline for AFB suppression",
        "scope": "federal",
        "summary": "Oxytetracycline-medicated feed for honey bee colonies requires "
                   "a Veterinary Feed Directive issued by a licensed veterinarian "
                   "with a valid Veterinarian-Client-Patient Relationship (VCPR). "
                   "Records must be retained for two years.",
    },
    {
        "code": "EPA-LABEL-FORMICACID",
        "title": "EPA label restrictions — formic acid varroa treatment",
        "scope": "federal",
        "summary": "Formic acid varroa treatments (e.g., Formic Pro) carry EPA "
                   "label restrictions on application temperature, brood-chamber "
                   "exposure, and required ventilation. Off-label application "
                   "is a federal violation regardless of intent.",
    },
    {
        "code": "TTB-MEAD-COFA",
        "title": "TTB Certificate of Label Approval analog requirements for mead",
        "scope": "federal",
        "summary": "Honey-based fermented beverages above 7% ABV require TTB "
                   "Certificate of Label Approval (COLA). Producers selling honey "
                   "directly to mead/cyser fermenters should be aware of "
                   "compliance traceability that may be requested for the "
                   "honey sourcing chain.",
    },
]


def _generate_one_regulatory_doc(topic: dict) -> str:
    system = (
        "You are a regulatory compliance writer for a commercial beekeeping "
        "supply company. Write a concise but authoritative internal reference "
        "document. Include: regulatory code, what triggers it, who must "
        "comply, what the operational requirements are, what the "
        "consequences of non-compliance are, who to contact for questions. "
        "Cite agency by name. No markdown. Plain text with section headers "
        "in CAPS. Length: 1200-1800 characters. Do not invent statute "
        "numbers; speak in general terms. The document will be searched "
        "via semantic retrieval."
    )
    user = (
        f"TOPIC CODE: {topic['code']}\n"
        f"TITLE: {topic['title']}\n"
        f"SCOPE: {topic['scope']}\n"
        f"SUMMARY (anchor only): {topic['summary']}\n\n"
        "Write the reference document now."
    )
    return _llm(system, user, max_tokens=900)


def generate_regulatory_docs_fixture() -> list[dict]:
    out = []
    for i, t in enumerate(REGULATORY_TOPICS, start=1):
        log.info("[reg doc %d/%d] %s", i, len(REGULATORY_TOPICS), t["code"])
        content = _generate_one_regulatory_doc(t)
        out.append({
            "code": t["code"],
            "title": t["title"],
            "scope": t["scope"],
            "content": content,
        })
    return out


# ---------------------------------------------------------------------------
# 5. Customer aliases (rule-based, no LLM)
# ---------------------------------------------------------------------------


def _alias_for(name: str) -> list[str]:
    """Generate plausible variants. Pure string ops — deterministic, free."""
    aliases: list[str] = []
    bare = name
    # Variant: drop apostrophe ("O'Reilly" → "OReilly")
    if "'" in name:
        aliases.append(name.replace("'", ""))
    # Variant: spell out abbreviations
    if " Co." in name:
        aliases.append(name.replace(" Co.", " Company"))
    if " LP" in name:
        aliases.append(name.replace(" LP", " Limited Partnership"))
    if " LLC" in name:
        aliases.append(name.replace(" LLC", ""))
    # Variant: "&" ↔ "and"
    if "&" in name:
        aliases.append(name.replace("&", "and"))
    # Variant: drop common suffix
    if name.endswith(" Orchards"):
        aliases.append(name[: -len(" Orchards")])
    if name.endswith(" Farms"):
        aliases.append(name[: -len(" Farms")])
    # Numeric variant: "Five Rivers" → "5 Rivers"
    NUM_MAP = {"Five": "5", "Seven": "7", "Three": "3", "Four": "4", "Six": "6"}
    for word, num in NUM_MAP.items():
        if name.startswith(word + " "):
            aliases.append(num + " " + name[len(word) + 1:])
            break
    # Dedup, return at most 3 distinct from the original
    seen = set()
    uniq = []
    for a in aliases:
        if a and a != bare and a not in seen:
            seen.add(a)
            uniq.append(a)
        if len(uniq) >= 3:
            break
    return uniq


def generate_customer_aliases_fixture() -> dict[str, list[str]]:
    faker = _build_faker()
    customers = generate_customers(faker)
    # Boost coverage to ~50% — pick every other customer, plus any with
    # naturally generatable variants.
    out: dict[str, list[str]] = {}
    for i, c in enumerate(customers):
        aliases = _alias_for(c.name)
        if aliases:
            out[c.name] = aliases
        elif i % 2 == 0:
            # No natural variant — concoct a "Old Mission Farm" → "Old Mission" type alias
            tokens = c.name.split()
            if len(tokens) >= 3:
                out[c.name] = [" ".join(tokens[:2])]
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


GENERATORS = {
    "narratives": ("customer_reports.json", generate_customer_reports_fixture),
    "emails": ("emails.json", generate_emails_fixture),
    "product_sheets": ("product_data_sheets.json", generate_product_data_sheets_fixture),
    "regulatory_docs": ("regulatory_docs.json", generate_regulatory_docs_fixture),
    "aliases": ("customer_aliases.json", generate_customer_aliases_fixture),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Waggle seed fixtures.")
    parser.add_argument(
        "--type",
        choices=list(GENERATORS.keys()) + ["all"],
        default="all",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing fixture files.",
    )
    args = parser.parse_args(argv)

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    targets = list(GENERATORS.keys()) if args.type == "all" else [args.type]

    for t in targets:
        filename, gen_fn = GENERATORS[t]
        path = FIXTURES_DIR / filename
        if path.exists() and not args.force:
            log.info("⏭  %s already exists; skipping (pass --force to overwrite)", filename)
            continue
        log.info("→ generating %s", filename)
        result = gen_fn()
        path.write_text(json.dumps(result, indent=2, default=str) + "\n")
        log.info("✓ wrote %s (%d bytes)", filename, path.stat().st_size)

    log.info("✅ fixture generation complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
