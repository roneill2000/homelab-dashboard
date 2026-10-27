// Generates the Material 3 colour tokens for the dashboard (light + dark) from one seed colour.
// Used by build.mjs (bundled with esbuild: the 0.4.0 package's ESM imports omit file extensions, so plain Node cannot load it).
// Reference implementation: @material/material-color-utilities 0.4.0, colour spec 2025 (the M3 Expressive / Android 16 spec).
import { argbFromHex, hexFromArgb, Hct, SchemeFidelity, DynamicScheme, TonalPalette, MaterialDynamicColors, DynamicColor, customColor, Blend } from '@material/material-color-utilities';
export const SPEC = '2025';

// Twilight pine (Ryan, 26 Oct 2026): deep Pacific-Northwest evergreen. Fidelity keeps the seed's own shade as the
// primary container and pairs it with a cedar/rust tertiary; TonalSpot would have softened it to mint.
export const SEED = '#1f4d45';
// Service and chart accents. Each name becomes --md-custom-<name>, --md-custom-on-<name>,
// --md-custom-<name>-container and --md-custom-on-<name>-container, harmonised toward the seed.
export const CUSTOM = {
  teal: '#7de5c0', green: '#a8d798', cyan: '#8acfe2', blue: '#7dacf5', indigo: '#9aadee',
  purple: '#c3a5f4', pink: '#f49b99', orange: '#ef9c65', amber: '#f4cf7d',
};
// Every colour role the 2025 spec defines (methods on a MaterialDynamicColors instance that return a DynamicColor).
const DC = new MaterialDynamicColors();
const ROLES = Object.getOwnPropertyNames(Object.getPrototypeOf(DC))
  .filter(k => k !== 'constructor' && typeof DC[k] === 'function' && DC[k].length === 0 && !/PaletteKeyColor$/.test(k))
  .filter(k => { try { return DC[k]() instanceof DynamicColor; } catch { return false; } });
const kebab = s => s.replace(/[A-Z]/g, c => '-' + c.toLowerCase());

function block(isDark) {
  const seed = Hct.fromInt(argbFromHex(SEED));
  const fidelity = new SchemeFidelity(seed, isDark, 0, SPEC);
  // Fidelity's own neutrals are near-grey; tint surfaces and outlines with the seed hue (chroma 10 / 14) so the
  // page reads as pine at dusk. Light theme uses less tint (4 / 7): at light tones the same chroma reads as mint (Ryan, 27 Oct).
  // Accent palettes stay Fidelity's. Contrast is still computed by the library.
  const scheme = new DynamicScheme({ sourceColorHct: seed, variant: fidelity.variant, contrastLevel: 0, isDark, specVersion: SPEC,
    platform: fidelity.platform, primaryPalette: fidelity.primaryPalette, secondaryPalette: fidelity.secondaryPalette,
    tertiaryPalette: fidelity.tertiaryPalette, errorPalette: fidelity.errorPalette,
    neutralPalette: TonalPalette.fromHueAndChroma(seed.hue, isDark ? 10 : 4), neutralVariantPalette: TonalPalette.fromHueAndChroma(seed.hue, isDark ? 14 : 7) });
  const lines = ROLES.map(k => `--md-sys-color-${kebab(k)}:${hexFromArgb(DC[k]().getArgb(scheme))}`);
  for (const [name, hex] of Object.entries(CUSTOM)) {
    const c = customColor(argbFromHex(SEED), { name, value: argbFromHex(hex), blend: true })[isDark ? 'dark' : 'light'];
    lines.push(`--md-custom-${name}:${hexFromArgb(c.color)}`, `--md-custom-on-${name}:${hexFromArgb(c.onColor)}`,
      `--md-custom-${name}-container:${hexFromArgb(c.colorContainer)}`, `--md-custom-on-${name}-container:${hexFromArgb(c.onColorContainer)}`);
  }
  // Chart series --md-chart-1..5: pine (primary hue), cedar (tertiary hue), then blue, amber and violet harmonised to the
  // seed. Tone/chroma per theme were chosen with the dataviz palette validator (lightness band, chroma floor, colour-blind
  // separation of neighbours, >= 3:1 against the card surface): dark tone 60 / chroma 48, light tone 50 / chroma 70.
  const [tone, chroma] = isDark ? [60, 48] : [50, 70];
  const harmonised = hex => Hct.fromInt(Blend.harmonize(argbFromHex(hex), seed.toInt())).hue;
  [fidelity.primaryPalette.hue, fidelity.tertiaryPalette.hue, harmonised(CUSTOM.blue), harmonised(CUSTOM.amber), harmonised(CUSTOM.purple)]
    .forEach((h, i) => lines.push(`--md-chart-${i + 1}:${hexFromArgb(Hct.from(h, chroma, tone).toInt())}`));
  return lines.join(';');
}

export function m3TokensCss() {
  return `/* GENERATED: m3-tokens (m3-tokens.mjs, seed ${SEED}, spec ${SPEC}) */:root{color-scheme:dark;${block(true)}}:root[data-theme=light]{color-scheme:light;${block(false)}}`;
}
