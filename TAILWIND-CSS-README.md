# Static Tailwind CSS - Generated for Tauri Desktop App

## Overview
This is a **complete, static CSS file** containing ALL Tailwind utility classes extracted from `index.html`. It's designed for the Tauri desktop app where dynamic CSS generation is unreliable.

**File:** `tailwind-static.css`  
**Size:** 12KB (258 lines)  
**Format:** Valid CSS with CSS custom properties for theming  
**Dark Mode:** Full support with class-based dark mode (`class="dark"`)

## Usage

Simply link this CSS file in your HTML:
```html
<link rel="stylesheet" href="tailwind-static.css">
```

The CSS uses CSS custom properties (variables) that automatically update when dark mode is toggled on the `<html>` element:
```html
<html class="dark">  <!-- Dark mode active -->
```

## Utilities Included

### Layout & Positioning (11)
- `absolute`, `relative`, `inset-0`
- `top-1`, `top-1/2`, `bottom-1`, `bottom-full`
- `left-0`, `left-1`, `left-2.5`, `right-1`

### Flexbox & Grid (15)
- `flex`, `flex-col`, `flex-wrap`, `flex-1`
- `grid`, `grid-cols-4`, `col-span-4`
- `items-center`, `justify-between`, `justify-center`
- `gap-0.5`, `gap-1`, `gap-1.5`, `gap-2`, `gap-3`, `gap-6`

### Sizing - Width (17)
- `w-px`, `w-1.5`, `w-2.5`, `w-3`, `w-3.5`, `w-4`, `w-5`, `w-6`, `w-8`, `w-10`
- `w-16`, `w-24`, `w-44`, `w-48`, `w-72`, `w-full`
- `max-w-xl`

### Sizing - Height (13)
- `h-1.5`, `h-2.5`, `h-3`, `h-3.5`, `h-4`, `h-5`, `h-6`, `h-7`, `h-8`, `h-10`, `h-12`
- `h-full`, `h-screen`
- `max-h-32`

### Spacing - Margin (15)
- Margin bottom: `mb-1`, `mb-1.5`, `mb-2`
- Margin left: `ml-0.5`, `ml-1.5`, `ml-2`, `ml-auto`
- Margin top: `mt-0.5`, `mt-1`, `mt-1.5`, `mt-2`, `mt-3`
- Margin horizontal: `mx-0.5`, `mx-3`, `mx-auto`

### Spacing - Padding (15)
- General: `p-0.5`, `p-2`
- Horizontal: `px-1`, `px-1.5`, `px-3`, `px-4`
- Vertical: `py-0.5`, `py-1`, `py-1.5`, `py-8`
- Directional: `pb-2`, `pb-3`, `pl-8`, `pr-3`

### Typography (18)
- Font sizes: `text-xs`, `text-sm`, `text-2xs` (10px custom)
- Alignment: `text-center`, `text-left`
- Font family: `font-sans`, `font-mono`
- Font weight: `font-medium`, `font-semibold`
- Other: `italic`, `leading-relaxed`, `tracking-wide`, `tracking-wider`, `uppercase`, `truncate`

### Text Colors (15)
- Standard: `text-white`, `text-accent`, `text-danger`, `text-success`, `text-warning`
- Custom: `text-txt-primary`, `text-txt-secondary`, `text-txt-tertiary`, `text-panel-border`
- With opacity: `text-txt-tertiary/30`, `text-warning/40`

### Background Colors (16)
- Standard: `bg-white/20`, `bg-black/70`
- Custom: `bg-panel`, `bg-surface`, `bg-surface-50`, `bg-surface-200`
- Accent shades: `bg-accent`, `bg-accent/40`, `bg-accent/80`
- Alert colors: `bg-danger`, `bg-success`, `bg-warning`, `bg-txt-tertiary`, `bg-panel-border`

### Gradients (6)
- Directions: `bg-gradient-to-t`, `bg-gradient-to-br`
- From: `from-black/60`, `from-warning/10`
- To: `to-transparent`, `to-surface-50`

### Borders (6)
- Width: `border`, `border-b`, `border-l`, `border-r`, `border-t`
- Color: `border-panel-border`, `border-accent`, `border-accent/40`

### Border Radius (4)
- `rounded`, `rounded-md`, `rounded-lg`, `rounded-full`

### Visual Effects (6)
- `shadow-lg`, `backdrop-blur`, `rotate-180`, `-translate-y-1/2`
- `object-cover`, `opacity-30`

### Transitions & Interactions (3)
- `transition`, `cursor-pointer`, `pointer-events-none`

### Overflow & Display (5)
- `hidden`, `overflow-hidden`, `overflow-y-auto`, `resize-none`, `shrink-0`

### Focus States (4)
- `focus:outline-none`, `focus:ring-1`, `focus:ring-accent/30`, `focus:border-accent`

### Hover States (7)
- `hover:bg-accent/10`, `hover:bg-white/30`, `hover:bg-accent-hover`
- `hover:text-txt-primary`, `hover:text-txt-secondary`, `hover:text-warning`, `hover:w-0.5`

### Ring (2)
- `ring-1`, `ring-accent/20`

### Placeholders (1)
- `placeholder-txt-tertiary`

### Aspect Ratio (1)
- `aspect-video` (16:9 ratio)

### Dark Mode (10+)
All major utilities have dark mode overrides:
- `dark` prefix: `.dark .bg-surface`, `.dark .text-txt-primary`, etc.
- Proper CSS variable inheritance

## Color Palette

### Light Mode
| Color | Value |
|-------|-------|
| Surface | #f5f5f7 |
| Surface-50 | #eaeaed |
| Surface-100 | #e0e0e4 |
| Surface-200 | #d5d5da |
| Panel | #ffffff |
| Panel Border | #e0e0e4 |
| Accent | #3b82f6 |
| Accent Hover | #2563eb |
| Accent Muted | #1d4ed8 |
| Danger | #ef4444 |
| Success | #22c55e |
| Warning | #f59e0b |
| Text Primary | #1a1a1e |
| Text Secondary | #52525b |
| Text Tertiary | #a1a1aa |

### Dark Mode
| Color | Value |
|-------|-------|
| Surface | #1a1a1e |
| Surface-50 | #222226 |
| Surface-100 | #2a2a2e |
| Surface-200 | #323236 |
| Panel | #1e1e22 |
| Panel Border | #2e2e34 |
| Text Primary | #e4e4e7 |
| Text Secondary | #a1a1aa |
| Text Tertiary | #71717a |

## Font Configuration

### Sans Serif
- Inter (primary)
- -apple-system (macOS system font)
- BlinkMacSystemFont (WebKit system font)
- system-ui (generic system font)
- sans-serif (fallback)

### Monospace
- SF Mono (macOS monospace)
- JetBrains Mono (cross-platform)
- Menlo (fallback)
- monospace (generic)

### Custom Font Sizes
- `text-2xs`: 10px (14px line height) - for compact UI text

## Implementation Notes

1. **CSS Variables**: All colors are defined as CSS custom properties (`--color-*`) that update automatically in dark mode
2. **Opacity Modifiers**: Full support for `/10`, `/20`, `/30`, `/40`, `/60`, `/70`, `/80` opacity variants
3. **Responsive**: No responsive prefixes (like `md:`) are included - only the utilities actually used in the HTML
4. **Valid CSS**: 100% valid CSS with balanced braces and proper syntax
5. **No JavaScript**: Pure CSS - no runtime dependency on Tailwind CLI or PostCSS

## Statistics

- **Total CSS Rules**: 185+
- **Total Lines**: 258
- **File Size**: 12KB
- **Custom Color Properties**: 15
- **Unique Utilities**: 150+

## How It Was Generated

1. Extracted all `class="..."` attributes from `index.html`
2. Parsed template literals for additional class strings
3. Generated CSS rules for each unique utility class
4. Applied custom color palette from Tailwind config
5. Included both light and dark mode variants
6. Validated CSS syntax and brace balance

## No Dependency Issues

This static CSS file:
- Does NOT require Tailwind CLI
- Does NOT require PostCSS
- Does NOT require npm/node
- Does NOT perform any dynamic class generation
- Works perfectly in Tauri/WKWebView environments

## Modification

To add new utilities in the future:
1. Add the class to your HTML
2. Extract the class name
3. Add the corresponding CSS rule to this file
4. Remember to include both light and dark variants if needed

---

**Generated for:** Media Asset Manager (Tauri Desktop App)  
**Date Generated:** 2026-03-30  
**Status:** Production Ready
