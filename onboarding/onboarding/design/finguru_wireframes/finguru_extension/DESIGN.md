---
name: FinGuru Extension
colors:
  surface: '#fbf9f8'
  surface-dim: '#dbd9d9'
  surface-bright: '#fbf9f8'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f5f3f3'
  surface-container: '#efeded'
  surface-container-high: '#eae8e7'
  surface-container-highest: '#e4e2e2'
  on-surface: '#1b1c1c'
  on-surface-variant: '#424750'
  inverse-surface: '#303030'
  inverse-on-surface: '#f2f0f0'
  outline: '#737781'
  outline-variant: '#c2c6d1'
  surface-tint: '#2f5f9b'
  primary: '#00386b'
  on-primary: '#ffffff'
  primary-container: '#1a4f8a'
  on-primary-container: '#9ac2ff'
  inverse-primary: '#a5c8ff'
  secondary: '#795900'
  on-secondary: '#ffffff'
  secondary-container: '#ffc641'
  on-secondary-container: '#715300'
  tertiary: '#353839'
  on-tertiary: '#ffffff'
  tertiary-container: '#4c4f50'
  on-tertiary-container: '#bfc0c2'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#d4e3ff'
  primary-fixed-dim: '#a5c8ff'
  on-primary-fixed: '#001c3a'
  on-primary-fixed-variant: '#0d4782'
  secondary-fixed: '#ffdfa0'
  secondary-fixed-dim: '#f6be39'
  on-secondary-fixed: '#261a00'
  on-secondary-fixed-variant: '#5c4300'
  tertiary-fixed: '#e1e3e4'
  tertiary-fixed-dim: '#c5c7c8'
  on-tertiary-fixed: '#191c1d'
  on-tertiary-fixed-variant: '#454748'
  background: '#fbf9f8'
  on-background: '#1b1c1c'
  surface-variant: '#e4e2e2'
typography:
  display-lg:
    fontFamily: Quicksand
    fontSize: 40px
    fontWeight: '700'
    lineHeight: 48px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Quicksand
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
  headline-lg-mobile:
    fontFamily: Quicksand
    fontSize: 24px
    fontWeight: '700'
    lineHeight: 32px
  headline-md:
    fontFamily: Quicksand
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  body-lg:
    fontFamily: Quicksand
    fontSize: 18px
    fontWeight: '500'
    lineHeight: 28px
  body-md:
    fontFamily: Quicksand
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  label-md:
    fontFamily: Quicksand
    fontSize: 14px
    fontWeight: '600'
    lineHeight: 20px
    letterSpacing: 0.01em
  label-sm:
    fontFamily: Quicksand
    fontSize: 12px
    fontWeight: '700'
    lineHeight: 16px
rounded:
  sm: 0.5rem
  DEFAULT: 1rem
  md: 1.5rem
  lg: 2rem
  xl: 3rem
  full: 9999px
spacing:
  base: 8px
  xs: 4px
  sm: 12px
  md: 24px
  lg: 40px
  xl: 64px
  gutter: 20px
  margin-mobile: 16px
  margin-desktop: 48px
---

## Brand & Style

The design system extension focuses on the "FinGuru" persona: a wise, approachable financial mentor. The aesthetic bridges traditional banking reliability with the warmth of a personal advisor. 

The style is **Modern/Tactile**, characterized by soft elevations, deep layered surfaces, and a friendly, rounded geometric language. It avoids the austerity of typical fintech apps in favor of a "human-centric" interface that feels supportive rather than cold. The primary emotional response is one of safety, clarity, and optimism. High-quality whitespace and large, legible hit areas are prioritized to ensure the UI feels calm and unhurried.

## Colors

The palette is anchored by **SBI Blue**, representing the heritage and stability of the underlying banking infrastructure. The **FinGuru Gold** is used strategically to signal "Financial Wisdom"—it marks educational moments, insights, and premium advice.

- **Primary (SBI Blue):** Used for core functional actions, primary navigation, and headers.
- **Secondary (FinGuru Gold):** Reserved for the mascot’s presence, specific insight tags, "Guru" recommendations, and active states of advisory tools.
- **Backgrounds:** The interface uses a tiered light gray (`#F2F4F7`) to distinguish the "stage" from the "cards."
- **Feedback:** Use a soft emerald green for positive growth trends and a muted terracotta for alerts, maintaining the warm tone of the system.

## Typography

The typography uses **Quicksand** exclusively to maintain a friendly, accessible character. The rounded terminals of the typeface complement the 16px+ corner radius of the UI components. 

Headlines should use the Bold weight to establish a clear hierarchy, while body copy benefits from the Medium weight to ensure legibility against light-gray backgrounds. For the "FinGuru" specific insights, use the `display-lg` style with tighter letter spacing to create a distinct, editorial feel. Avoid using the Light weight of Quicksand, as it lacks the "sturdiness" required for financial confidence.

## Layout & Spacing

The layout follows a **Fluid Grid** model with generous inner margins to allow components "room to breathe." 

- **Mobile:** 4-column grid with 16px margins. Cards should typically span the full width of the grid.
- **Desktop:** 12-column grid with a max-width of 1200px. Content is centered with 48px outer margins.
- **Rhythm:** Spacing follows an 8px base unit. Component internal padding should default to `md` (24px) to emphasize the soft, airy nature of the design. 

Vertical spacing between different sections should use the `lg` or `xl` tokens to prevent the "clutter" often associated with banking apps.

## Elevation & Depth

This design system uses **Tonal Layers** combined with **Ambient Shadows** to create a physical sense of depth.

1.  **Canvas Layer:** The base background is a cool, light gray (`#F2F4F7`).
2.  **Card Layer:** Interactive content sits on pure white (`#FFFFFF`) surfaces. These use a multi-layered shadow (0px 4px 20px rgba(26, 79, 138, 0.08)) to appear softly lifted.
3.  **Active/Guru Layer:** When the FinGuru mascot provides an insight, the card may gain a subtle 2px gold border or a faint gold inner glow to indicate "Advisory" status.

Avoid harsh blacks for shadows; instead, use a tinted blue or neutral gray shadow to maintain the "Soft Modern" aesthetic.

## Shapes

The shape language is defined by a high **Pill-shaped** or **Hyper-rounded** aesthetic. 

- **Standard Containers:** A minimum radius of 16px (`rounded-lg`).
- **Main Action Buttons:** Should be fully pill-shaped (radius: 9999px).
- **Selection Chips:** Pill-shaped with a 1px border.
- **Input Fields:** 12px radius to balance the more aggressive rounding of the buttons while maintaining a consistent visual family.

## Components

- **Buttons:** Primary buttons are SBI Blue with white text, pill-shaped. FinGuru-specific calls-to-action (e.g., "See Wisdom") use the Gold color with white or dark-gray text.
- **Cards:** White background, 24px internal padding, and 16px or 24px corner radius. Advisory cards feature a top-right Gold tag or a subtle Gold accent bar on the left edge.
- **Inputs:** Soft gray stroke (2px) that transforms into an SBI Blue stroke on focus. The label should float inside the border.
- **Chips:** Used for categorizing spending. These are pill-shaped with light pastel background tints of the category color.
- **FinGuru Mascot Placement:** The small owl icon should appear half-submerged at the top-right of "Insight" cards or as a floating action button (FAB) for quick help.
- **Progress Bars:** Thicker than average (12px height) with fully rounded ends, using a gradient from SBI Blue to a lighter sky blue to denote progress toward goals.