# SPDX-License-Identifier: Apache-2.0
"""
UI Styles for Blender Bridge

Consistent styling to match Scene Optimizer visuals.
"""

import omni.ui as ui

# Colors (ABGR format for Omniverse)
PURPLE_PRIMARY = 0xFF99004B      # #4B0099 - main accent
PURPLE_HOVER = 0xFF99336A        # lighter purple
PURPLE_BAR = 0xFF8B3B86          # section indicator bar
DARK_BG = 0xFF232323             # dark background
DARKER_BG = 0xFF1A1A1A           # darker background
TEXT_MUTED = 0xFFAAAAAA          # muted text
TEXT_NORMAL = 0xFFCCCCCC         # normal text
STATUS_TEXT = 0xFF888888         # status text
LINE_COLOR = 0xFF3A3A3A          # separator line

# Dimensions
LABEL_WIDTH = 130
FIELD_HEIGHT = 22
SECTION_BAR_WIDTH = 4
BUTTON_HEIGHT = 28
SPACING = 2
RESET_DOT_SIZE = 8

# Section collapsible frame style (like Scene Optimizer)
SECTION_STYLE = {
    "CollapsableFrame": {
        "background_color": DARKER_BG,
        "secondary_color": DARKER_BG,
        "border_radius": 4,
        "border_width": 0,
        "padding": 4,
    },
    "CollapsableFrame:hovered": {
        "secondary_color": 0xFF252525,
    },
}

# Execute button style
EXECUTE_BTN_STYLE = {
    "Button": {
        "background_color": PURPLE_PRIMARY,
        "border_radius": 4,
        "padding": 8,
    },
    "Button:hovered": {
        "background_color": PURPLE_HOVER,
    },
}

# Field styles
FIELD_STYLE = {
    "background_color": DARK_BG,
    "border_radius": 2,
}

# Label styles
LABEL_STYLE = {
    "color": TEXT_NORMAL,
    "font_size": 14,
}

LABEL_MUTED_STYLE = {
    "color": TEXT_MUTED,
    "font_size": 13,
}

# Small icon button (like X close button)
ICON_BTN_STYLE = {
    "Button": {
        "background_color": 0x00000000,
        "padding": 0,
    },
    "Button:hovered": {
        "background_color": 0x40FFFFFF,
    },
}

# Add button style
ADD_BTN_STYLE = {
    "Button": {
        "background_color": 0xFF3A3A3A,
        "border_radius": 2,
    },
    "Button:hovered": {
        "background_color": 0xFF4A4A4A,
    },
}

# Reset dot style (small circle button)
RESET_DOT_STYLE = {
    "Button": {
        "background_color": 0xFF4A4A4A,
        "border_radius": RESET_DOT_SIZE // 2,
        "padding": 0,
    },
    "Button:hovered": {
        "background_color": 0xFF6A6A6A,
    },
}

# Separator line style
LINE_STYLE = {
    "background_color": LINE_COLOR,
    "border_radius": 1,
}


def create_labeled_row(label: str, width: int = LABEL_WIDTH):
    """Create a horizontal stack with a label. Returns the HStack context."""
    stack = ui.HStack(height=FIELD_HEIGHT, spacing=SPACING)
    return stack


def build_section_header(title: str, on_close=None):
    """Build a section header with colored bar and optional close button."""
    with ui.HStack(height=24):
        # Colored bar on left
        ui.Rectangle(width=SECTION_BAR_WIDTH, style={"background_color": PURPLE_BAR, "border_radius": 2})
        ui.Spacer(width=8)
        # Triangle collapse indicator
        ui.Label("▼", width=16, style={"color": TEXT_MUTED})
        ui.Spacer(width=4)
        # Title
        ui.Label(title, style={"font_size": 14, "color": TEXT_NORMAL})
        ui.Spacer()
        # Close button if provided
        if on_close:
            ui.Button("✕", width=24, height=24, clicked_fn=on_close, 
                      style=ICON_BTN_STYLE, tooltip="Remove")
