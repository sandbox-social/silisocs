"""Static language-model provider metadata with no SDK imports."""

from __future__ import annotations

BUILT_IN_PROVIDERS = ("openai", "openai_compatible", "scripted", "disabled")

# provider -> (OpenAI-compatible base URL, API-key environment variable)
OPENAI_COMPATIBLE_PRESETS: dict[str, tuple[str, str | None]] = {
    "anthropic": ("https://api.anthropic.com/v1/", "ANTHROPIC_API_KEY"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
    ),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "fireworks": ("https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY"),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
}
