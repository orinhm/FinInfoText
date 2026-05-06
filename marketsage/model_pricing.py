"""
Known pricing for LLM models (per 1M tokens, USD).

This is a best-effort lookup — prices change frequently.
Run `python -m marketsage.llm_client --refresh` to regenerate
available_models.yaml with pricing attached.

Last updated: 2026-05-06
"""

# (input_per_1M, output_per_1M, description) in USD
# None means "free tier available" or "pricing unknown"

MODEL_INFO: dict[str, tuple[float | None, float | None, str]] = {
    # ── Gemini ────────────────────────────────────────────────────
    # 3.x flagship
    "gemini-3.1-pro-preview":           (2.00, 12.00, "Flagship. Deep reasoning, coding, and complex analysis. Expensive but very strong."),
    "gemini-3-pro-preview":             (1.25, 10.00, "Previous flagship. Strong reasoning, superseded by 3.1-pro."),
    "gemini-3-pro-image-preview":       (1.25, 10.00, "Previous flagship optimized for image analysis."),
    # 3.x flash
    "gemini-3.1-flash-lite-preview":    (0.02, 0.10, "Ultra-fast, very cheap. Good for simple classification or basic extraction."),
    "gemini-3.1-flash-image-preview":   (0.10, 0.40, "Fast vision model. Good for analyzing images quickly and cheaply."),
    "gemini-3.1-flash-live-preview":    (0.10, 0.40, "Fast, balanced model optimized for low-latency live streaming/chat."),
    "gemini-3.1-flash-tts-preview":     (0.10, 0.40, "Fast model optimized for Text-to-Speech workloads."),
    "gemini-3-flash-preview":           (0.10, 0.40, "Previous generation fast model. Good for general quick tasks."),
    # 2.5 pro
    "gemini-2.5-pro":                   (1.25, 10.00, "Older flagship. Deep reasoning, but superseded by 3.x."),
    # 2.5 flash
    "gemini-2.5-flash":                 (0.15, 0.60, "Older fast model. Solid general performance."),
    "gemini-2.5-flash-lite":            (0.02, 0.10, "Older ultra-cheap model. Weak reasoning, only for simple tasks."),
    "gemini-2.5-flash-image":           (0.15, 0.60, "Older fast vision model."),
    # 2.0 flash
    "gemini-2.0-flash":                 (0.10, 0.40, "Legacy fast model. Good, but 3.x is better."),
    "gemini-2.0-flash-001":             (0.10, 0.40, "Legacy fast model. Good, but 3.x is better."),
    "gemini-2.0-flash-lite":            (0.02, 0.10, "Legacy ultra-cheap model."),
    "gemini-2.0-flash-lite-001":        (0.02, 0.10, "Legacy ultra-cheap model."),
    # Embeddings
    "gemini-embedding-001":             (0.00, 0.00, "Legacy text embeddings."),
    "gemini-embedding-2":               (0.00, 0.00, "Modern text embeddings. Best for semantic search."),

    # ── OpenAI ────────────────────────────────────────────────────
    # GPT-5.x
    "gpt-5.5":                          (2.00, 10.00, "Flagship. State of the art reasoning, coding, and creative generation."),
    "gpt-5.5-pro":                      (5.00, 20.00, "Premium Flagship. Extreme context length and deep analysis. Very expensive."),
    "gpt-5.4":                          (1.50, 8.00, "Previous generation flagship. Excellent performance."),
    "gpt-5.4-pro":                      (4.00, 16.00, "Previous generation premium model. Very strong."),
    "gpt-5.4-mini":                     (0.40, 1.60, "Fast, highly capable compact model. Excellent balance of cost and performance."),
    "gpt-5.4-nano":                     (0.15, 0.60, "Ultra-fast, very cheap. Best for high-volume simple tasks."),
    "gpt-5.2":                          (1.25, 10.00, "Older flagship model."),
    "gpt-5.2-pro":                      (3.00, 12.00, "Older premium model."),
    "gpt-5.1":                          (1.25, 10.00, "Older flagship model."),
    "gpt-5":                            (1.25, 10.00, "First generation GPT-5. Very strong, but newer versions are better."),
    "gpt-5-pro":                        (3.00, 12.00, "First generation GPT-5 Premium."),
    "gpt-5-mini":                       (0.40, 1.60, "Older compact model."),
    "gpt-5-nano":                       (0.15, 0.60, "Older ultra-fast model."),
    # GPT-4.x
    "gpt-4.1":                          (2.00, 8.00, "Legacy flagship. Good, but superseded by GPT-5."),
    "gpt-4.1-mini":                     (0.40, 1.60, "Legacy compact model."),
    "gpt-4.1-nano":                     (0.10, 0.40, "Legacy ultra-fast model."),
    "gpt-4o":                           (2.50, 10.00, "Omni model. Excellent multimodal capabilities (text/vision/audio)."),
    "gpt-4o-mini":                      (0.15, 0.60, "Compact Omni model. Very fast and cheap multimodal."),
    "gpt-4-turbo":                      (10.00, 30.00, "Legacy high-power model. Expensive."),
    "gpt-4":                            (30.00, 60.00, "Original GPT-4. Very expensive and slow by modern standards."),
    # GPT-3.5
    "gpt-3.5-turbo":                    (0.50, 1.50, "Legacy fast model. Use GPT-4o-mini or GPT-5-nano instead."),
    # Reasoning
    "o3":                               (2.00, 8.00, "Advanced Reasoning model. Best for math, logic, and deep problem solving."),
    "o3-mini":                          (1.10, 4.40, "Compact Reasoning model. Fast logic solving."),
    "o4-mini":                          (1.10, 4.40, "Latest Compact Reasoning model. Excellent logic solving at lower cost."),
    "o1":                               (15.00, 60.00, "Original Deep Reasoning model. Very expensive, use o3 instead."),
    # Embeddings
    "text-embedding-3-large":           (0.13, 0.00, "High-dimension text embeddings. Best quality semantic search."),
    "text-embedding-3-small":           (0.02, 0.00, "Low-dimension text embeddings. Very cheap, good quality."),
    "text-embedding-ada-002":           (0.10, 0.00, "Legacy text embeddings. Use embedding-3 instead."),
}


def get_model_info(model_id: str) -> dict[str, float | str | None] | None:
    """
    Look up pricing and description for a model ID.

    Tries exact match first, then strips common prefixes/suffixes
    (e.g. "models/gemini-3.1-pro-preview" → "gemini-3.1-pro-preview",
    "gpt-5-2025-08-07" → "gpt-5").

    Returns {"input": X, "output": Y, "description": Z} or None if unknown.
    """
    # Strip "models/" prefix (Gemini API returns this)
    clean = model_id.removeprefix("models/")

    # Exact match
    if clean in MODEL_INFO:
        inp, out, desc = MODEL_INFO[clean]
        return {"input": inp, "output": out, "description": desc}

    # Strip date suffixes like "-2025-08-07" or "-2024-04-09"
    import re
    stripped = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", clean)
    if stripped in MODEL_INFO:
        inp, out, desc = MODEL_INFO[stripped]
        return {"input": inp, "output": out, "description": desc}

    # Strip "-preview" suffix variants
    for suffix in ("-preview", "-latest"):
        if clean.endswith(suffix):
            base = clean[: -len(suffix)]
            if base in MODEL_INFO:
                inp, out, desc = MODEL_INFO[base]
                return {"input": inp, "output": out, "description": desc}

    return None
