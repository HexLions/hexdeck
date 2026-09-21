"""The bundled themes: well-known palettes, settled so every token that is
drawn as text clears 4.5:1 on the page and on a card, in both brightnesses.

A theme is a name and the same fifteen tokens for dark and for light, as
``#rrggbb``. The accent's soft and glow shades are derived by the browser.
"""

from __future__ import annotations

#: The tokens a theme may set, without the ``--nd-`` prefix.
TOKENS: tuple[str, ...] = (
    "bg", "bg-elev", "surface", "surface-hover", "border", "border-strong",
    "text", "text-muted", "text-faint", "accent", "on-accent", "ok", "warn", "bad", "unknown",
)

THEMES: dict[str, dict] = {
    "nord": {
        "name": "Nord",
        "dark": {
            "bg": "#2e3440", "bg-elev": "#3b4252", "surface": "#3b4252", "surface-hover": "#434c5e",
            "border": "#4c566a", "border-strong": "#5e6a82", "text": "#eceff4", "text-muted": "#d8dee9",
            "text-faint": "#b8c0d0", "accent": "#88c0d0", "on-accent": "#2e3440", "ok": "#a3be8c",
            "warn": "#ebcb8b", "bad": "#daa2ab", "unknown": "#a7b1c4",
        },
        "light": {
            "bg": "#eceff4", "bg-elev": "#ffffff", "surface": "#ffffff", "surface-hover": "#e5e9f0",
            "border": "#d8dee9", "border-strong": "#c2c9d6", "text": "#2e3440", "text-muted": "#4c566a",
            "text-faint": "#5e6a82", "accent": "#506d92", "on-accent": "#ffffff", "ok": "#517536",
            "warn": "#8a6400", "bad": "#a2525e", "unknown": "#636c86",
        },
    },
    "catppuccin": {
        "name": "Catppuccin",
        "dark": {
            "bg": "#1e1e2e", "bg-elev": "#313244", "surface": "#313244", "surface-hover": "#45475a",
            "border": "#45475a", "border-strong": "#585b70", "text": "#cdd6f4", "text-muted": "#bac2de",
            "text-faint": "#a6adc8", "accent": "#89b4fa", "on-accent": "#1e1e2e", "ok": "#a6e3a1",
            "warn": "#f9e2af", "bad": "#f38ba8", "unknown": "#979db5",
        },
        "light": {
            "bg": "#eff1f5", "bg-elev": "#ffffff", "surface": "#ffffff", "surface-hover": "#e6e9ef",
            "border": "#ccd0da", "border-strong": "#bcc0cc", "text": "#4c4f69", "text-muted": "#5c5f77",
            "text-faint": "#686b80", "accent": "#1d62eb", "on-accent": "#ffffff", "ok": "#317921",
            "warn": "#945f13", "bad": "#d20f39", "unknown": "#696c7d",
        },
    },
    "gruvbox": {
        "name": "Gruvbox",
        "dark": {
            "bg": "#282828", "bg-elev": "#3c3836", "surface": "#3c3836", "surface-hover": "#504945",
            "border": "#504945", "border-strong": "#665c54", "text": "#ebdbb2", "text-muted": "#d5c4a1",
            "text-faint": "#bdae93", "accent": "#88a99c", "on-accent": "#282828", "ok": "#b8bb26",
            "warn": "#fabd2f", "bad": "#fb7b6c", "unknown": "#aea18e",
        },
        "light": {
            "bg": "#fbf1c7", "bg-elev": "#fffbe8", "surface": "#fffbe8", "surface-hover": "#ebdbb2",
            "border": "#d5c4a1", "border-strong": "#bdae93", "text": "#3c3836", "text-muted": "#504945",
            "text-faint": "#665c54", "accent": "#076678", "on-accent": "#ffffff", "ok": "#746f0d",
            "warn": "#94600f", "bad": "#9d0006", "unknown": "#776b60",
        },
    },
    "dracula": {
        "name": "Dracula",
        "dark": {
            "bg": "#282a36", "bg-elev": "#343746", "surface": "#343746", "surface-hover": "#44475a",
            "border": "#44475a", "border-strong": "#6272a4", "text": "#f8f8f2", "text-muted": "#d6d6d0",
            "text-faint": "#b8bcc8", "accent": "#bd93f9", "on-accent": "#282a36", "ok": "#50fa7b",
            "warn": "#f1fa8c", "bad": "#ff7575", "unknown": "#9ca3bc",
        },
        "light": {
            "bg": "#f8f8f2", "bg-elev": "#ffffff", "surface": "#ffffff", "surface-hover": "#ececec",
            "border": "#dcdcdc", "border-strong": "#c4c4c4", "text": "#282a36", "text-muted": "#44475a",
            "text-faint": "#5a5e73", "accent": "#7c4dcc", "on-accent": "#ffffff", "ok": "#1f7a3a",
            "warn": "#8a6400", "bad": "#c62828", "unknown": "#5f6577",
        },
    },
}
