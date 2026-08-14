---
name: Kinetic Heritage
colors:
  surface: '#f9f9ff'
  surface-dim: '#d9dadf'
  surface-bright: '#f9f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f3f3f9'
  surface-container: '#ededf3'
  surface-container-high: '#e8e8ed'
  surface-container-highest: '#e2e2e8'
  on-surface: '#1a1c20'
  on-surface-variant: '#424750'
  inverse-surface: '#2e3035'
  inverse-on-surface: '#f0f0f6'
  outline: '#737781'
  outline-variant: '#c2c6d1'
  surface-tint: '#2f5f9b'
  primary: '#00386b'
  on-primary: '#ffffff'
  primary-container: '#1a4f8a'
  on-primary-container: '#9ac2ff'
  inverse-primary: '#a5c8ff'
  secondary: '#5b5f61'
  on-secondary: '#ffffff'
  secondary-container: '#e0e3e6'
  on-secondary-container: '#626567'
  tertiary: '#582c00'
  on-tertiary: '#ffffff'
  tertiary-container: '#793f00'
  on-tertiary-container: '#ffae6b'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#d4e3ff'
  primary-fixed-dim: '#a5c8ff'
  on-primary-fixed: '#001c3a'
  on-primary-fixed-variant: '#0d4782'
  secondary-fixed: '#e0e3e6'
  secondary-fixed-dim: '#c4c7ca'
  on-secondary-fixed: '#191c1e'
  on-secondary-fixed-variant: '#44474a'
  tertiary-fixed: '#ffdcc3'
  tertiary-fixed-dim: '#ffb77e'
  on-tertiary-fixed: '#2f1500'
  on-tertiary-fixed-variant: '#6e3900'
  background: '#f9f9ff'
  on-background: '#1a1c20'
  surface-variant: '#e2e2e8'
typography:
  display-lg:
    fontFamily: Quicksand
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Quicksand
    fontSize: 24px
    fontWeight: '700'
    lineHeight: 32px
  headline-sm:
    fontFamily: Quicksand
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  body-lg:
    fontFamily: Quicksand
    fontSize: 18px
    fontWeight: '500'
    lineHeight: 26px
  body-md:
    fontFamily: Quicksand
    fontSize: 16px
    fontWeight: '500'
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
  headline-lg-mobile:
    fontFamily: Quicksand
    fontSize: 28px
    fontWeight: '700'
    lineHeight: 36px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  container-margin: 20px
  card-gap: 16px
---

## Brand & Style

The design system bridges the established reliability of a legacy financial institution with the energetic, tech-native expectations of Gen Z and Alpha. The brand personality is "The Empathetic Expert"—authoritative enough to manage wealth, but approachable enough to explain it.

The aesthetic follows a **Modern-Tactile** approach. It utilizes clean layouts and heavy whitespace typical of minimalism, but injects warmth through soft geometry and subtle depth. The interface avoids "stiff" corporate structures in favor of a fluid, mobile-first experience that feels like a lifestyle companion rather than a ledger. The goal is to evoke a sense of financial confidence that is light, fast, and devoid of traditional banking friction.

## Colors

The palette is anchored by the primary "Heritage Blue," ensuring the core identity remains recognizable and trustworthy. This is balanced against a "Soft Mist" background to reduce ocular strain and create a premium, spacious feel.

- **Primary Blue (#1A4F8A):** Used for key actions, headers, and brand moments.
- **Background (#F5F7FA):** The foundation for all screens; provides a soft canvas for white cards.
- **White (#FFFFFF):** Reserved for containment layers (cards, sheets) to create "lift."
- **Accents:** Used sparingly for psychological cues—Orange for alerts/rewards, Green for growth/success, and Pink for lifestyle/offers.

## Typography

This design system uses **Quicksand** across all touchpoints to maximize friendliness and readability. Its rounded terminals mirror the geometric language of the UI components. 

- **Headlines:** Use Bold (700) weights with tight letter spacing for a punchy, modern look.
- **Body:** Use Medium (500) weights to ensure clarity on high-density mobile screens.
- **Numeric Data:** For balance displays, use Bold weight to ensure immediate information hierarchy.
- **Scaling:** On mobile devices, Display-LG scales down to Headline-LG-Mobile to prevent awkward text wrapping in card components.

## Layout & Spacing

The layout utilizes a **4-column fluid mobile grid** with a 20px outer margin. The spacing rhythm is strictly based on a 4px baseline, ensuring visual alignment across complex financial data.

- **Safe Zones:** Content must maintain a 20px horizontal margin from the screen edge.
- **Vertical Rhythm:** Use 24px (lg) between distinct sections (e.g., between "Total Balance" and "Quick Actions"). Use 16px (md) for spacing between internal elements of a section.
- **Card Padding:** Standard card internal padding is 20px to maintain the "breathable" feel.

## Elevation & Depth

Hierarchy is established through **Tonal Layering** and **Soft Ambient Shadows**. 

- **Level 0 (Background):** The #F5F7FA surface. All static content lives here.
- **Level 1 (Cards):** White (#FFFFFF) surfaces with a very soft, diffused shadow (0px 4px 20px rgba(26, 79, 138, 0.05)). This provides a "floating" effect without feeling heavy.
- **Level 2 (Active/Modals):** Elevated sheets or focused cards use a slightly deeper shadow and a 1px inner stroke in a light grey to define edges against other white elements.
- **Interactions:** When pressed, elements should visually "sink" by reducing shadow spread, providing haptic-adjacent visual feedback.

## Shapes

The shape language is defined by **High-Radius Geometry**. Circles and heavy rounds are used to signify approachability.

- **Cards:** Use `rounded-lg` (16px) for all primary container cards.
- **Buttons:** Use `rounded-xl` (24px) or full pill-shape for primary CTAs to make them feel "tappable."
- **Inputs:** Use `rounded-md` (12px) to maintain a structural feel while remaining friendly.
- **Icons:** Use icons with rounded caps and joins; avoid sharp 90-degree angles.

## Components

- **Primary Buttons:** High-contrast Heritage Blue with white text. Pill-shaped. Height: 56px for main actions.
- **Secondary Buttons:** Ghost style with a Heritage Blue 1.5px border and #1A4F8A text.
- **Quick Action Chips:** Circular icons (48x48px) with a soft-tinted background (e.g., 10% opacity of the accent color) and a label underneath.
- **Cards:** Always white. Use for account summaries, offer banners, and transaction history. 16px corner radius is mandatory.
- **Input Fields:** Filled style with a very light grey background. On focus, the border transitions to Primary Blue. Floating labels help save vertical space.
- **Progress Bars:** Used for savings goals. Use a thick 8px track with a rounded cap. Use Accent Green for positive progress.
- **Navigation:** A floating bottom tab bar with a blurred glass background (backdrop-filter) to allow content to peek through, maintaining the mobile-first focus.