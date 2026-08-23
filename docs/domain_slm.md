# Domain-Expert SLMs — the "learned" tier of Network of Experts AI

**Status:** Reference for the `learned` expert tier in `select_expert` and the
provider contract in `fbp/slm.py`. This is the engineering reality that makes a
per-domain expert a practical, opt-in capability — not a research exercise.

## The thesis

The **Network of Experts AI** axiom holds: *the model is not the expert; the
network is.* A domain-expert SLM is one **kind** of expert — the `learned` tier —
chosen only when a deterministic method cannot cover the residue, and **never
bypassing the domain's verifier**. A domain SLM is a *better* expert, not a
*trusted* one. Its word is a hint that must be verified deterministically.

## Why domain SLMs are practical (2026)

- **Accuracy**: SLMs in the 1B–7B (sometimes 14B) range match or exceed larger
  general models on domain benchmarks.
- **Cost/latency**: lower inference cost, lower latency, edge/on-prem deployable.
- **Privacy**: better for regulated fields (healthcare, finance, legal).
- **Accessible**: QLoRA fine-tuning a 3B–7B base runs on a single RTX 4090, in
  hours to days, for tens to a few hundred dollars of cloud compute.

## The three paths

1. **Fine-tuning a pretrained base** (dominant): Llama 3.2 3B, Phi-4 Mini,
   Gemma 3 4B, Qwen 2.5 3B; LoRA/QLoRA; hundreds to tens of thousands of
   examples.
2. **DAPT + SFT + alignment**: domain-adaptive continued pretraining, then
   instruction tuning and DPO. Deeper knowledge internalization.
3. **From scratch / heavy distillation**: only for extremely narrow proprietary
   formats; rarely the first choice.

## The standard pipeline (corpus → expert)

1. **Corpus construction**: gather + relevance filter + quality score +
   deduplicate + PII removal + synthetic augmentation from a seed set.
2. **Domain-adaptive pretraining**: continue causal LM on the corpus; mix a
   fraction of general data to limit catastrophic forgetting.
3. **Supervised fine-tuning**: train on instruction/response pairs (real or
   synthetic).
4. **Alignment**: DPO on preference data.
5. **Evaluation**: domain benchmarks + general-capability checks; quantize
   (4/8-bit).
6. **Deployment**: vLLM / Ollama; combine with RAG for dynamic knowledge.

## The provider contract (`fbp/slm.py`)

Training is **external and opt-in** — it is never built into the deterministic
core. The contract:

- `SlmProvider` — protocol: `train(domain, corpus, spec) -> SlmExpert`.
- `SlmSpec` — the recipe (base model, method, max examples, quantize).
- `SlmExpert` — full **provenance**: base model, method, corpus, dataset size,
  benchmark score, artifact ref. This is what lands in the Artifact Vault.
- `StubSlmProvider` — the offline, deterministic default (CI-safe, no external
  deps).
- `build_domain_expert(domain, corpus, provider)` — the entry point; fail-closed
  (empty corpus / unverifiable domain / provider failure → `SlmError`).

The core **selects** (`select_expert` → `learned`) and **verifies** (the domain's
verifier); the provider **trains**. The Artifact Vault records the expert's
provenance as write-once evidence.

## Recommended base models for domain adaptation (2026) — the catalog

The platform keeps a **deterministic catalog** of recommended open-weight bases
in `fbp/model_catalog.py` (``model_catalog.resolve_base`` / ``min_tier_for``).
Each entry records size class, minimum hardware tier, licensing, and whether an
instruction-tuned variant exists. The training plan (`plan_training`) uses the
base's **size-class hardware floor** to gate the tier — a 14B base cannot train
on the same tier as a 0.5B base regardless of corpus size.

**Selection guidance (2026):** Qwen and Llama families currently show strong
tuning gains and community tooling. Prefer instruction-tuned variants as
starting points for most SFT/DPO work; use base (non-instruct) versions for pure
continued pretraining. QLoRA on 3B–8B bases is feasible on a single
high-end consumer GPU (e.g. RTX 4090).

### By size / deployment category

- **Ultra-small / edge (≤2B):** Qwen2.5 / Qwen3 (0.5B–1.7B/2B); Llama 3.2 1B;
  SmolLM-class; Phi-3/3.5 Mini variants. Lightweight, edge-friendly.
- **Small (2–4B):** Llama 3.2 3B; Qwen2.5 / Qwen3 3B–4B; Phi-4 Mini (~3.8B);
  Gemma 2/3 small variants. Strong per-parameter reasoning and multilingual.
- **Mid-size (7–14B) — most practical for high-quality domain experts:**
  Llama 3.1/3.2/3.3 8B; Qwen2.5 / Qwen3 7B–8B / 14B; Gemma 2/3 9B–12B;
  Mistral 7B / Mistral Nemo (~12B); Phi-4 (~14B).
- **Larger bases (more capacity):** Llama 3.1/3.3 70B or Qwen equivalents
  (depth injection before distillation/quantization); open families prioritizing
  full openness/auditability (OLMo, Apertus, EuroLLM).

### Domain model repositories (hubs with URLs + metadata)

**General / cross-domain indexes**
- Awesome-Domain-LLM (luban-agi) — https://github.com/luban-agi/Awesome-Domain-LLM — curated vertical-domain open models/datasets/benchmarks.

**Medical / biomedical / healthcare**
- Google HAI-DEF / MedGemma — huggingface.co/collections/google/health-ai-developer-foundations-hai-def
- EPFLiGHT / MeditronFO — huggingface.co/collections/EPFLiGHT/meditronfo
- BioMistral — huggingface.co/BioMistral
- epfl-llm / Meditron (original) — huggingface.co/epfl-llm + github.com/epfLLM/meditron
- OpenMed — search "OpenMed" on Hugging Face.

**Finance**
- FinGPT (AI4Finance) — huggingface.co/FinGPT + github.com/AI4Finance-Foundation/FinGPT
- LLM Open Finance (DragonLLM) — huggingface.co/collections/DragonLLM/llm-open-finance

**Legal**
- Equall / SaulLM — search "SaulLM" / "Equall" on Hugging Face (MIT on key releases).
- AdaptLLM domain series — huggingface.co/AdaptLLM (law-LLM / finance-LLM / medicine-LLM).

**Notes:** availability, licenses, and checkpoints change; always verify the model
card. Many strong domain models are adapters/continued-pretraining checkpoints
over the bases above.

## Open-source domain models (quick references)

- **Medical:** BioMistral, Meditron, MedGemma, OpenMed, PMC-LLaMA.
- **Finance:** FinGPT, LLM Open Finance (Llama 3.1 / Qwen 8B), Nemotron variants.
- **Legal**: SaulLM-7B, DISC-LawLLM.
- **Code**: CodeLlama, DeepSeek-Coder, StarCoder, DiagnosticSLM (3B automotive).
- **Multi-domain**: Shakti (healthcare/finance/legal), vertical adapters.

## Limitations (honest)

Broad multi-hop reasoning and creative cross-domain tasks still favor larger
general models. Rigorous evaluation and maintenance against distribution shift
are essential. Regulatory concerns (hallucination risk, auditability, data
provenance) must be addressed explicitly — which is exactly the provenance +
verification the Domain Registry + Artifact Vault provide.

## Recommendation

Select a strong 3B–7B open base, curate/synthesize a focused dataset, apply
QLoRA (or DAPT + SFT for deeper injection), evaluate thoroughly, iterate. The
result is a production-usable domain expert with controlled cost — served by the
platform as an opt-in `learned` expert, verified and evidenced.