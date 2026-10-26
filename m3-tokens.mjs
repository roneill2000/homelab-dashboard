// Generates the Material 3 colour tokens for the dashboard (light + dark) from one seed colour.
// Used by build.mjs (bundled with esbuild: the 0.4.0 package's ESM imports omit file extensions, so plain Node cannot load it).
// Reference implementation: @material/material-color-utilities 0.4.0, colour spec 2025 (the M3 Expressive / Android 16 spec).
import { argbFromHex, hexFromArgb, Hct, SchemeTonalSpot, MaterialDynamicColors, DynamicColor, customColor } from '@material/material-color-utilities';
export const SPEC = '2025';

export const SEED = '#7de5c0';
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
  const scheme = new SchemeTonalSpot(Hct.fromInt(argbFromHex(SEED)), isDark, 0, SPEC);
  const lines = ROLES.map(k => `--md-sys-color-${kebab(k)}:${hexFromArgb(DC[k]().getArgb(scheme))}`);
  for (const [name, hex] of Object.entries(CUSTOM)) {
    const c = customColor(argbFromHex(SEED), { name, value: argbFromHex(hex), blend: true })[isDark ? 'dark' : 'light'];
    lines.push(`--md-custom-${name}:${hexFromArgb(c.color)}`, `--md-custom-on-${name}:${hexFromArgb(c.onColor)}`,
      `--md-custom-${name}-container:${hexFromArgb(c.colorContainer)}`, `--md-custom-on-${name}-container:${hexFromArgb(c.onColorContainer)}`);
  }
  return lines.join(';');
}

export function m3TokensCss() {
  return `/* GENERATED: m3-tokens (m3-tokens.mjs, seed ${SEED}, spec ${SPEC}) */:root{color-scheme:dark;${block(true)}}:root[data-theme=light]{color-scheme:light;${block(false)}}`;
}
